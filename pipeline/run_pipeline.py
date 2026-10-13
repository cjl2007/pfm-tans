#!/usr/bin/env python3
"""Run the MATLAB-free TANS workflow in dependency order."""

from __future__ import annotations

import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        "pfm-tans requires Python 3.10+; running "
        f"{sys.version.split()[0]} at {sys.executable}. "
        "Set TANS_PYTHON to a compatible interpreter (for example, the "
        "SimNIBS environment's Python), or use that interpreter directly "
        "when running standalone.")

import argparse
import copy
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Mapping, Sequence

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'search'))
from selected_pose import robustness_enabled
from coil_center_constraint import resolve_coil_center_constraint


STAGES = ("headmodel", "targets", "plan", "search", "reports")
SM_HAND_NETWORK = "Somatomotor_Hand"
GLASSER_MFG_SEARCH_SPACE = "glasser_predefined_plus_subject_specific_mfg"
GLASSER_MFG_SEARCH_SPACE_ALIASES = {
    GLASSER_MFG_SEARCH_SPACE,
    "glasser_lpfc_plus_subject_mfg",
}


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def run(command: Sequence[str], label: str) -> float:
    print(f"[python-tans] {label}", flush=True)
    started = time.perf_counter()
    completed = subprocess.run(command)
    elapsed = time.perf_counter() - started
    if completed.returncode:
        raise RuntimeError(f"{label} failed with status {completed.returncode}")
    return elapsed


def record_pipeline_stage(output: Path, stage: str, status: str,
                          wall_seconds: float | None = None) -> None:
    path = output / "PipelineTiming.json"
    timing = json.loads(path.read_text()) if path.is_file() else {
        "schema_version": 1,
        "stages": {},
    }
    previous = timing["stages"].get(stage, {})
    timing["stages"][stage] = {
        "status": status,
        "wall_seconds": (
            float(wall_seconds) if wall_seconds is not None
            else previous.get("wall_seconds")),
    }
    path.write_text(json.dumps(timing, indent=2) + "\n")


def render_strings(value: Any, variables: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(variables)
    if isinstance(value, list):
        return [render_strings(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: render_strings(item, variables)
                for key, item in value.items()}
    return value


def stage_index(name: str) -> int:
    try:
        return STAGES.index(name)
    except ValueError as exc:
        raise ValueError(f"Unknown stage: {name}") from exc


def complete(path: Path, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"{label} did not produce {path}")


def resolve_network_reference(requested: str, metadata: str | Path) -> str:
    from prepare_targets import network_metadata, resolve_unique_name

    names, _ = network_metadata(require_file(metadata, "network metadata"))
    return names[resolve_unique_name(names, requested, "Prior network")]


def resolve_target_search_space(requested: str | None, subject: str,
                                subject_dir: Path, output: Path,
                                resource_root: Path) -> tuple[str | None, str]:
    """Resolve a search-space file or build a named subject-specific recipe."""
    if not requested:
        return None, "configured cortical search space"
    if requested not in GLASSER_MFG_SEARCH_SPACE_ALIASES:
        return str(require_file(requested, "target search space")), \
            "configured cortical search space"

    import nibabel as nib
    import numpy as np

    envelope = require_file(
        resource_root / "res0urces" / "SearchSpaces" /
        "HCP_MMP_L_LPFC_TMS_envelope_v1.dscalar.nii",
        "bundled Glasser LPFC search-space envelope")
    label_file = require_file(
        subject_dir / "anat" / "MNINonLinear" / "fsaverage_LR32k" /
        f"{subject}.L.aparc.a2009s.32k_fs_LR.label.gii",
        "subject aparc.a2009s labels")
    destination = (output / "shared" / "search-spaces" /
                   f"{subject}.Glasser_LPFC_plus_subject_MFG.dscalar.nii")
    provenance_file = destination.with_suffix(".provenance.json")
    if destination.is_file() and provenance_file.is_file():
        print(f"[python-tans] reusing resolved target search space: {destination}")
        return str(destination), \
            "Glasser predefined LPFC + subject-specific MFG"

    envelope_image = nib.load(envelope)
    envelope_data = np.asarray(envelope_image.dataobj)[0].astype(bool)
    brain_axis = envelope_image.header.get_axis(1)
    left = next((item for item in brain_axis.iter_structures()
                 if str(item[0]) == "CIFTI_STRUCTURE_CORTEX_LEFT"), None)
    if left is None:
        raise RuntimeError(f"Glasser LPFC envelope has no left cortex: {envelope}")
    _, left_slice, left_model = left

    label_image = nib.load(label_file)
    label_data = np.asarray(label_image.darrays[0].data)
    keys = [entry.key for entry in label_image.labeltable.labels
            if entry.label == "L_G_front_middle"]
    if len(keys) != 1:
        raise RuntimeError(
            f"Expected one L_G_front_middle label in {label_file}; found {len(keys)}")
    subject_mfg = label_data[left_model.vertex] == int(keys[0])
    resolved = envelope_data.copy()
    resolved[left_slice] = np.logical_or(
        resolved[left_slice], subject_mfg)

    destination.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Cifti2Image(
        resolved.astype(np.float32)[np.newaxis, :],
        header=envelope_image.header,
        nifti_header=envelope_image.nifti_header), destination)
    provenance_file.write_text(json.dumps({
        "schema_version": 1,
        "recipe": GLASSER_MFG_SEARCH_SPACE,
        "definition": (
            "Glasser predefined left LPFC TMS envelope OR subject-specific "
            "L_G_front_middle"),
        "subject": subject,
        "glasser_envelope": str(envelope),
        "subject_gyral_labels": str(label_file),
        "subject_mfg_label": "L_G_front_middle",
        "glasser_vertices": int(envelope_data.sum()),
        "subject_mfg_vertices": int(subject_mfg.sum()),
        "union_vertices": int(resolved.sum()),
        "output": str(destination),
    }, indent=2) + "\n")
    print(f"[python-tans] built target search space: {destination}")
    return str(destination), "Glasser predefined LPFC + subject-specific MFG"


def configure_target_plan(config: dict, treatment_network: str | None,
                          search_space: str | None,
                          anatomical_zone_display: str,
                          include_sm_hand: bool,
                          sm_hand_coil: str | Path,
                          treatment_coil: str | Path) -> None:
    targets = config.get("targets", [])
    calibration = next((item for item in targets
                        if str(item.get("network")) == SM_HAND_NETWORK), None)
    if calibration is None and (include_sm_hand or treatment_network == SM_HAND_NETWORK):
        raise ValueError(
            "Configuration must define the default Somatomotor_Hand calibration target")

    planned = []
    if include_sm_hand:
        calibration = copy.deepcopy(calibration)
        calibration["coil"] = str(require_file(
            sm_hand_coil, "SM-Hand calibration coil model"))
        planned.append(calibration)
    if treatment_network:
        if treatment_network == SM_HAND_NETWORK:
            if not include_sm_hand:
                treatment = copy.deepcopy(calibration)
                treatment["coil"] = str(require_file(
                    treatment_coil, "treatment coil model"))
                planned.append(treatment)
        else:
            if not search_space:
                raise ValueError(
                    "A non-SM-Hand treatment target requires --target-search-space")
            from prepare_targets import safe_slug
            planned.append({
                "network": treatment_network,
                "display_name": safe_slug(treatment_network),
                "map_mode": str(config.get("search", {}).get(
                    "map_mode", "weighted")),
                "anatomical_zone_display": anatomical_zone_display,
                "anatomical_mask": {
                    "type": "cifti_binary_mask",
                    "name": Path(search_space).stem,
                    "file": str(require_file(search_space, "target search space")),
                },
                "scoring": {
                    "constrain_target_to_anatomical_mask": True,
                    "restrict_weights_to_binary_support": True,
                },
                "coil": str(require_file(
                    treatment_coil, "treatment coil model")),
            })
    if not planned:
        raise ValueError(
            "Skipping SM-Hand calibration requires a treatment target")

    config["targets"] = planned
    workflow = config.setdefault("workflow", {})
    workflow.update({
        "sm_hand_calibration_enabled": include_sm_hand,
        "calibration_target": SM_HAND_NETWORK if include_sm_hand else None,
        "treatment_target": treatment_network,
        "dose_transfer_requested": bool(
            include_sm_hand and treatment_network
            and treatment_network != SM_HAND_NETWORK),
        "sm_hand_coil_model": (str(require_file(
            sm_hand_coil, "SM-Hand calibration coil model"))
            if include_sm_hand else None),
        "treatment_coil_model": (str(require_file(
            treatment_coil, "treatment coil model"))
            if treatment_network else None),
    })


def configure_dosing_calibrations(config: dict, registry: str | Path,
                                  sm_hand_requested: str | None,
                                  treatment_requested: str | None,
                                  allow_similar: bool,
                                  substitution_justification: str) -> None:
    from calibration_registry import resolve_calibration

    workflow = config.setdefault("workflow", {})
    targets = config["targets"]
    resolved = {}
    if workflow["sm_hand_calibration_enabled"]:
        calibration_target = targets[0]
        calibration = resolve_calibration(
            registry, calibration_target["coil"], sm_hand_requested,
            "sm_hand_motor_threshold", allow_similar,
            substitution_justification)
        calibration_target["coil_calibration"] = calibration
        calibration_target["coil_hardware_name"] = calibration[
            "coil_hardware_name"]
        resolved["sm_hand"] = calibration
    has_separate_treatment_target = bool(
        workflow.get("treatment_target") and
        (not workflow["sm_hand_calibration_enabled"] or
         workflow["treatment_target"] != SM_HAND_NETWORK))
    if has_separate_treatment_target:
        treatment_target = targets[-1]
        treatment = resolve_calibration(
            registry, treatment_target["coil"], treatment_requested,
            "treatment", allow_similar, substitution_justification)
        treatment_target["coil_calibration"] = treatment
        treatment_target["coil_hardware_name"] = treatment[
            "coil_hardware_name"]
        resolved["treatment"] = treatment
    workflow["dosing_calibrations"] = {
        "registry": str(require_file(registry, "calibration registry")),
        "similar_coil_substitution_allowed": allow_similar,
        "selections": resolved,
    }


def resolve_pfm_directory(subject_dir: Path, func_dir: str,
                          explicit: str | None = None) -> Path:
    """Prefer RidgePFM for new runs; keep existing RidgeFusion studies usable."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    parent = subject_dir / "func" / func_dir / "PFM"
    current = parent / "RidgePFM"
    legacy = parent / "RidgeFusion"
    if not current.is_dir() and legacy.is_dir():
        print("[python-tans] using existing RidgeFusion directory; YAML input filenames must match it.")
        return legacy.resolve()
    return current.resolve()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--output", required=True)
    parser.add_argument("--subject-dir")
    parser.add_argument("--func-dir", default="rest")
    parser.add_argument("--pfm-dir")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--target-network")
    parser.add_argument(
        "--target-search-space", default=GLASSER_MFG_SEARCH_SPACE,
        help=("Named search-space recipe (default: "
              f"{GLASSER_MFG_SEARCH_SPACE}) or a binary cortical CIFTI path"))
    parser.add_argument("--coarse-grid-density", type=float)
    parser.add_argument('--robustness-enabled', type=int, choices=(0, 1),
                        help='0: select nominal fine winner without placement-error simulations')
    parser.add_argument("--sm-hand-coil")
    parser.add_argument("--treatment-coil")
    parser.add_argument("--calibration-registry")
    parser.add_argument("--sm-hand-didt-calibration")
    parser.add_argument("--treatment-didt-calibration")
    parser.add_argument("--allow-similar-coil-calibration", action="store_true")
    parser.add_argument("--calibration-substitution-justification", default="")
    dosing_limit = parser.add_mutually_exclusive_group()
    dosing_limit.add_argument(
        "--maximum-treatment-rmt-ratio", type=float,
        help=("Hard upper bound for treatment %%MSO relative to measured "
              "SM-Hand rMT %%MSO (default from config: 1.20)"))
    dosing_limit.add_argument(
        "--disable-treatment-rmt-limit", action="store_true",
        help="Disable the optional upward treatment/rMT %%MSO ceiling")
    parser.add_argument("--skip-sm-hand-calibration", action="store_true")
    parser.add_argument(
        "--handle-orientation-preference",
        choices=("as_simulated", "posterior_inferior"),
        help="Canonicalize a magnitude-equivalent final pose for handle ergonomics")
    parser.add_argument(
        "--coil-180-degree-magnitude-equivalent", type=int, choices=(0, 1),
        help="Assert that a 180-degree physical coil flip preserves |E|")
    parser.add_argument("--start-from", choices=STAGES, default="headmodel")
    parser.add_argument("--stop-after", choices=STAGES, default="reports")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("workers must be positive")
    if stage_index(args.stop_after) < stage_index(args.start_from):
        raise ValueError("--stop-after precedes --start-from")

    source_config = require_file(args.config, "configuration")
    requested = yaml.safe_load(source_config.read_text())
    if not isinstance(requested, dict):
        raise ValueError("Configuration root must be a mapping")
    subject_dir = Path(args.subject_dir or requested["subject_adapter"][
        "subject_dir"]).expanduser().resolve()
    subject = subject_dir.name
    output = Path(args.output).expanduser().resolve()
    if output.exists() and not args.resume:
        raise FileExistsError(f"Output exists; use --resume: {output}")
    output.mkdir(parents=True, exist_ok=True)
    pfm_dir = resolve_pfm_directory(subject_dir, args.func_dir, args.pfm_dir)
    variables = {
        "subject": subject,
        "subject_dir": str(subject_dir),
        "func_dir": args.func_dir,
        "pfm_dir": str(pfm_dir),
        "tans_root": str(output),
        "pfm_tans_root": str(Path(__file__).resolve().parents[1]),
    }
    config = render_strings(requested, variables)
    config["subject"] = subject
    config["coil_center_constraint"] = resolve_coil_center_constraint(config)
    if args.robustness_enabled is not None:
        config.setdefault('robustness', {})['enabled'] = bool(args.robustness_enabled)
    robustness_enabled(config)
    search_manifest = output / 'MultinetworkRunManifest.json'
    if search_manifest.is_file():
        existing = json.loads(search_manifest.read_text())
        for target in existing['targets']:
            previous = json.loads(Path(target['coarse_config']).read_text())
            if robustness_enabled(previous) != robustness_enabled(config):
                raise ValueError('Cannot change robustness mode in an existing search plan; use a fresh output')
    density = (args.coarse_grid_density if args.coarse_grid_density is not None
               else float(config.get("search", {}).get(
                   "coverage_fraction", 0.85)))
    if not 0 < density <= 1:
        raise ValueError("--coarse-grid-density must be in (0, 1]")
    config.setdefault("search", {})["coverage_fraction"] = density
    from network_inputs import (resolve_network_inputs, validate_requested_networks,
                                validate_prepared_network_inputs)
    if args.stop_after != "headmodel":
        resolve_network_inputs(config, output / "shared" / "network-inputs")
    treatment_network = None
    if args.target_network:
        treatment_network = resolve_network_reference(
            args.target_network, config["inputs"]["network_metadata"])
    search_space = None
    anatomical_zone_display = "configured cortical search space"
    if treatment_network and treatment_network != SM_HAND_NETWORK:
        search_space, anatomical_zone_display = resolve_target_search_space(
            args.target_search_space, subject, subject_dir, output,
            Path(__file__).resolve().parents[1])
    simulation = config.setdefault("simulation", {})
    default_coil = simulation.get("coil")
    sm_hand_coil = (args.sm_hand_coil
                    or simulation.get("sm_hand_calibration_coil")
                    or default_coil)
    treatment_coil = (args.treatment_coil
                      or simulation.get("treatment_coil")
                      or default_coil)
    if not sm_hand_coil or not treatment_coil:
        raise ValueError("Both SM-Hand and treatment coil models must be configured")
    configure_target_plan(
        config, treatment_network, search_space, anatomical_zone_display,
        not args.skip_sm_hand_calibration, sm_hand_coil, treatment_coil)
    if args.stop_after != "headmodel":
        validate_requested_networks(config)
        previous_preparation = output / "shared" / "target-preparation" / "TargetPreparationManifest.json"
        if previous_preparation.is_file():
            validate_prepared_network_inputs(config, json.loads(previous_preparation.read_text()))
    config.setdefault("workflow", {})["target_search_space"] = {
        "requested": args.target_search_space,
        "resolved_file": search_space,
        "display_name": anatomical_zone_display,
    }
    dosing = config.setdefault("dosing", {})
    configured_ratio = dosing.get("maximum_treatment_rmt_ratio", 1.20)
    maximum_treatment_rmt_ratio = (
        None if args.disable_treatment_rmt_limit else
        args.maximum_treatment_rmt_ratio
        if args.maximum_treatment_rmt_ratio is not None else
        None if configured_ratio in (None, "") else float(configured_ratio))
    if (maximum_treatment_rmt_ratio is not None and
            (not math.isfinite(maximum_treatment_rmt_ratio) or maximum_treatment_rmt_ratio < 1)):
        raise ValueError("--maximum-treatment-rmt-ratio must be positive")
    dosing["maximum_treatment_rmt_ratio"] = maximum_treatment_rmt_ratio
    registry = (args.calibration_registry or dosing.get("registry") or
                (Path(__file__).resolve().parents[1] / "res0urces" /
                 "StimulatorCalibration" / "calibration_registry.json"))
    sm_hand_calibration = (args.sm_hand_didt_calibration or
                           dosing.get("sm_hand_calibration"))
    treatment_calibration = (args.treatment_didt_calibration or
                             dosing.get("treatment_calibration"))
    allow_similar = bool(
        args.allow_similar_coil_calibration or
        dosing.get("allow_similar_coil_calibration", False))
    justification = (args.calibration_substitution_justification or
                     str(dosing.get("substitution_justification", "")))
    configure_dosing_calibrations(
        config, registry, sm_hand_calibration, treatment_calibration,
        allow_similar, justification)
    handle = config.setdefault("prescription", {}).setdefault(
        "handle_orientation", {})
    if args.handle_orientation_preference is not None:
        handle["mode"] = (
            "prefer_posterior_inferior"
            if args.handle_orientation_preference == "posterior_inferior"
            else "as_simulated")
    if args.coil_180_degree_magnitude_equivalent is not None:
        handle["coil_180_degree_magnitude_equivalent"] = bool(
            args.coil_180_degree_magnitude_equivalent)
    adapter = config.setdefault("subject_adapter", {})
    adapter["mode"] = "native_t1w_t2w_charm"
    adapter["subject_dir"] = str(subject_dir)
    adapter["native_t1w"] = str(subject_dir / "anat" / "T1w" / "T1w.nii.gz")
    adapter["native_t2w"] = str(subject_dir / "anat" / "T2w" / "T2w.nii.gz")

    config_dir = output / "config"
    config_dir.mkdir(exist_ok=True)
    requested_copy = config_dir / "requested.yaml"
    if not requested_copy.exists():
        shutil.copy2(source_config, requested_copy)

    module = Path(__file__).resolve().parent
    headmodel = output / "shared" / "headmodel"
    adapter_manifest = headmodel / "SubjectAdapter.json"
    adapter["manifest"] = str(adapter_manifest)
    resolved_config = config_dir / "resolved.yaml"
    resolved_config.write_text(yaml.safe_dump(config, sort_keys=False))

    if stage_index(args.start_from) <= 0 <= stage_index(args.stop_after):
        if adapter_manifest.is_file():
            print(f"[python-tans] reusing completed native TANS head model: {headmodel}")
            record_pipeline_stage(output, "headmodel", "reused")
        else:
            if headmodel.exists():
                raise FileExistsError(
                    f"Incomplete head model requires review before resume: {headmodel}")
            headmodel.parent.mkdir(parents=True, exist_ok=True)
            elapsed = run([sys.executable, str(module / "prepare_subject.py"),
                           str(resolved_config), "--output", str(headmodel)],
                          "de novo CHARM from native T1w and T2w")
            record_pipeline_stage(output, "headmodel", "complete", elapsed)
        complete(adapter_manifest, "headmodel stage")
    elif not adapter_manifest.is_file():
        raise FileNotFoundError(
            f"--start-from={args.start_from} requires {adapter_manifest}")
    manifest = json.loads(adapter_manifest.read_text())
    from prepare_subject import validate_scalp_manifest
    validate_scalp_manifest(manifest)
    if args.stop_after == "headmodel":
        return

    resolved_adapter = manifest["resolved_subject_adapter"]
    surfaces = resolved_adapter["surfaces"]
    config["inputs"]["left_pial"] = surfaces["left_pial"]
    config["inputs"]["right_pial"] = surfaces["right_pial"]
    config["inputs"]["scalp"] = resolved_adapter["skin"]
    resolved_config.write_text(yaml.safe_dump(config, sort_keys=False))

    preparation = output / "shared" / "target-preparation"
    preparation_manifest = preparation / "TargetPreparationManifest.json"
    if stage_index(args.start_from) <= 1 <= stage_index(args.stop_after):
        if preparation_manifest.is_file():
            print(f"[python-tans] reusing completed target preparation: {preparation}")
            record_pipeline_stage(output, "targets", "reused")
        else:
            if preparation.exists():
                raise FileExistsError(
                    f"Incomplete target preparation requires review: {preparation}")
            elapsed = run([sys.executable, str(module / "prepare_targets.py"),
                           str(resolved_config), "--output", str(preparation)],
                          "prepare individualized targets")
            record_pipeline_stage(output, "targets", "complete", elapsed)
        complete(preparation_manifest, "target-preparation stage")
    elif not preparation_manifest.is_file():
        raise FileNotFoundError(
            f"--start-from={args.start_from} requires {preparation_manifest}")
    if args.stop_after == "targets":
        return

    if stage_index(args.start_from) <= 2 <= stage_index(args.stop_after):
        if search_manifest.is_file():
            print(f"[python-tans] reusing completed search plan: {search_manifest}")
            record_pipeline_stage(output, "plan", "reused")
        else:
            elapsed = run([
                sys.executable, str(module / "build_search_manifest.py"),
                str(resolved_config), "--target-preparation", str(preparation),
                "--output", str(output)], "build search plans")
            record_pipeline_stage(output, "plan", "complete", elapsed)
        complete(search_manifest, "search-plan stage")
    elif not search_manifest.is_file():
        raise FileNotFoundError(
            f"--start-from={args.start_from} requires {search_manifest}")
    if args.stop_after == "plan":
        return

    if stage_index(args.start_from) <= 3 <= stage_index(args.stop_after):
        elapsed = run([
            sys.executable, str(module / "run_search.py"), str(search_manifest),
            "--workers", str(args.workers)],
            ('run coarse, fine, and robust search' if robustness_enabled(config)
             else 'run coarse and fine search; robustness disabled'))
        record_pipeline_stage(output, "search", "complete", elapsed)
    if args.stop_after == "search":
        return

    elapsed = run([sys.executable, str(module / "generate_reports.py"),
                   str(search_manifest)], "generate reports and exports")
    record_pipeline_stage(output, "reports", "complete", elapsed)
    complete(output / "ReportIndex.json", "report stage")


if __name__ == "__main__":
    main()
