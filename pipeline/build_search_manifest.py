#!/usr/bin/env python3
"""Build the TANS search manifest from prepared individualized targets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Dict, Mapping, Sequence

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'search'))
from selected_pose import robustness_enabled
from coil_center_constraint import resolve_coil_center_constraint


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def require_directory(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def load_yaml(path: str | Path) -> dict:
    path = require_file(path, "configuration")
    value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Configuration root must be a mapping")
    return value


def load_json(path: str | Path, label: str) -> dict:
    path = require_file(path, label)
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_tsv(path: Path) -> list:
    with require_file(path, "TSV").open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def validate_fraction(value: object, label: str) -> float:
    fraction = float(value)
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"{label} must be in (0, 1]")
    return fraction


def resolve_retention(profile: str) -> dict:
    profiles = {
        "standard": {
            "profile": "standard",
            "keep_raw_simulation_workdir": False,
            "keep_mapped_efields_until_finalization": True,
            "cleanup_after_validation": True,
        },
        "debug": {
            "profile": "debug",
            "keep_raw_simulation_workdir": False,
            "keep_mapped_efields_until_finalization": True,
            "cleanup_after_validation": False,
        },
        "research": {
            "profile": "research",
            "keep_raw_simulation_workdir": True,
            "keep_mapped_efields_until_finalization": True,
            "cleanup_after_validation": False,
        },
    }
    if profile not in profiles:
        raise ValueError(
            f"retention profile must be one of {sorted(profiles)}, got {profile!r}")
    return dict(profiles[profile])


def resolve_policy(config: Mapping[str, object]) -> tuple[dict, Path]:
    search = config.get("search", {})
    source = require_file(search["policy"], "search policy")
    policy = load_json(source, "search policy")
    fraction = validate_fraction(
        search.get("coverage_fraction", policy["target_weighted_coverage_fraction"]),
        "search.coverage_fraction")
    policy["target_weighted_coverage_fraction"] = fraction
    if fraction != float(load_json(source, "search policy")[
            "target_weighted_coverage_fraction"]):
        policy["policy_name"] = f"{policy['policy_name']}_coverage_{fraction:.3f}"
        policy["policy_status"] = "explicit_run_override"
    return policy, source


def target_by_name(manifest: Mapping[str, object]) -> Dict[str, dict]:
    result = {}
    for item in manifest["targets"]:
        identifiers = {item["network"], item["display_name"]}
        if item.get("requested_network"):
            identifiers.add(item["requested_network"])
        for name in identifiers:
            if name in result and result[name] is not item:
                raise ValueError(f"Duplicate target identifier in preparation: {name}")
            result[name] = item
    return result


def orientations(spec: Mapping[str, object]) -> list:
    if "orientations_degrees" in spec:
        result = [float(value) for value in spec["orientations_degrees"]]
    else:
        start = float(spec.get("orientation_start_degrees", 0))
        stop = float(spec.get("orientation_stop_degrees", 180))
        step = float(spec.get("orientation_step_degrees", 15))
        if step <= 0 or stop <= start:
            raise ValueError("Invalid orientation sweep")
        count = int(round((stop - start) / step))
        result = [start + index * step for index in range(count)]
    if not result or any(value < 0 or value >= 180 for value in result):
        raise ValueError("Orientations must span values in [0, 180)")
    if len(set(result)) != len(result):
        raise ValueError("Orientations must be unique")
    return [int(value) if float(value).is_integer() else value for value in result]


def plan_directory(plan_root: Path, fraction: float) -> Path:
    label = f"{fraction:.3f}".rstrip("0").rstrip(".").replace(".", "p")
    return plan_root / f"target_projection_coverage_d{label}"


def target_files(target_dir: Path) -> dict:
    names = {
        "full_binary": "FullBinaryTargetSupport.dscalar.nii",
        "full_weighted": "FullWeightedTargetMap.dscalar.nii",
        "anatomical_mask": "SelectedAnatomicalMask.dscalar.nii",
        "eligible_binary": "EligibleBinaryTarget.dscalar.nii",
        "eligible_weighted": "EligibleWeightedTarget.dscalar.nii",
        "components": "ComponentLabels.dscalar.nii",
    }
    return {key: require_file(target_dir / name, key.replace("_", " "))
            for key, name in names.items()}


def initialize_output_root(value: str | Path) -> Path:
    output_root = Path(value).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    managed_outputs = (
        output_root / "ResolvedSearchPolicy.json",
        output_root / "MultinetworkRunManifest.json",
        output_root / "targets",
    )
    existing = [path for path in managed_outputs if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite existing search outputs: " +
            ", ".join(str(path) for path in existing))
    return output_root


def shared_configuration(config: Mapping[str, object], retention: dict) -> dict:
    inputs = config["inputs"]
    subject_config = config["subject_adapter"]
    workbench = require_file(
        subject_config["workbench"], "Connectome Workbench executable")
    subject = subject_config
    if "manifest" in subject:
        adapter_manifest = load_json(subject["manifest"], "subject adapter manifest")
        if adapter_manifest.get("status") != "validated":
            raise ValueError("Subject adapter manifest is not validated")
        if str(adapter_manifest.get("subject")) != str(config["subject"]):
            raise ValueError("Subject adapter manifest belongs to another subject")
        from prepare_subject import validate_scalp_manifest
        validate_scalp_manifest(adapter_manifest)
        subject = adapter_manifest["resolved_subject_adapter"]
    if subject.get("manifest"):
        adapter_manifest = load_json(
            subject["manifest"], "subject adapter manifest")
        if adapter_manifest.get("status") != "validated":
            raise ValueError("Subject adapter manifest is not validated")
        if str(adapter_manifest.get("subject")) != str(config["subject"]):
            raise ValueError("Subject adapter manifest belongs to another subject")
        from prepare_subject import validate_scalp_manifest
        validate_scalp_manifest(adapter_manifest)
        subject = adapter_manifest["resolved_subject_adapter"]
    surface = subject["surfaces"]
    resolved_surfaces = {
        key: str(require_file(value, key.replace("_", " ")))
        for key, value in surface.items()
    }
    expected = {
        "left_midthickness", "right_midthickness", "left_white", "right_white",
        "left_pial", "right_pial", "left_roi", "right_roi",
    }
    missing = expected - set(resolved_surfaces)
    if missing:
        raise ValueError(f"subject_adapter.surfaces is missing {sorted(missing)}")
    simulation = config.get("simulation", {})
    fine = config.get("fine_search", {})
    robust = config.get("robustness", {})
    return {
        "subject": str(config["subject"]),
        "subject_dir": str(require_directory(subject["subject_dir"], "subject directory")),
        "subject_adapter_mode": str(subject.get("mode", "explicit")),
        "network_label_map": str(require_file(
            inputs["categorical_networks"], "network label map")),
        "network_metadata": str(require_file(inputs["network_metadata"], "network metadata")),
        "network_input_provenance": config.get("network_input_provenance", {}),
        "vertex_area": str(require_file(inputs["vertex_area"], "vertex-area map")),
        "head_mesh": str(require_file(subject["head_mesh"], "head mesh")),
        "m2m_dir": str(require_directory(subject["m2m_dir"], "m2m directory")),
        "skin": str(require_file(subject["skin"], "skin surface")),
        "coil": str(require_file(simulation["coil"], "coil model")),
        "simnibs_python": str(require_file(
            simulation["simnibs_python"], "SimNIBS Python")),
        "workbench": str(workbench),
        "surfaces": resolved_surfaces,
        "hotspot_percentiles": [float(value) for value in simulation.get(
            "hotspot_percentiles", [99.9, 99.8, 99.7, 99.6, 99.5,
                                    99.4, 99.3, 99.2, 99.1, 99.0])],
        "distance_mm": float(simulation.get("coil_distance_mm", 1)),
        "didt_aperus": float(simulation.get("didt_aperus", 1000000)),
        "retention": retention,
        "prescription": dict(config.get("prescription", {})),
        "fine_search": {
            "radius_mm": float(fine.get("radius_mm", 10)),
            "spacing_mm": float(fine.get("spacing_mm", 4)),
            "relative_orientations_degrees": [float(value) for value in fine.get(
                "relative_orientations_degrees", [-10, -5, 0, 5, 10])],
        },
        "robustness": {
            "enabled": robustness_enabled(config),
            "nominal_relative_floor": float(robust.get("nominal_relative_floor", 0.98)),
            "maximum_finalists": int(robust.get("maximum_finalists", 3)),
            "minimum_finalist_separation_mm": float(
                robust.get("minimum_finalist_separation_mm", 4.0)),
            "position_error_radii_mm": [float(value) for value in robust.get(
                "position_error_radii_mm", [3.0, 5.0, 10.0])],
            "azimuth_samples_per_radius": int(
                robust.get("azimuth_samples_per_radius", 8)),
            "orientation_error_degrees": [float(value) for value in robust.get(
                "orientation_error_degrees", [0.0, 5.0, 10.0])],
            "default_position_error_mm": float(
                robust.get("default_position_error_mm", 5.0)),
            "default_orientation_error_degrees": float(
                robust.get("default_orientation_error_degrees", 5.0)),
            "selection_metric": str(
                robust.get("selection_metric", "lower_tail_mean_10_percent")),
        },
    }


def run_plan(generator: Path, command: Sequence[str], network: str) -> None:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(
            f"Coverage planning failed for {network}:\n"
            f"{completed.stdout}{completed.stderr}")


def build(config_path: str | Path, target_preparation: str | Path,
          output_root: str | Path) -> dict:
    config_path = require_file(config_path, "configuration")
    config = load_yaml(config_path)
    preparation = require_directory(target_preparation, "target preparation")
    prep_manifest = load_json(
        preparation / "TargetPreparationManifest.json", "target preparation manifest")
    if str(config["subject"]) != str(prep_manifest["subject"]):
        raise ValueError("Configuration subject does not match target preparation")
    from network_inputs import resolve_network_inputs, validate_prepared_network_inputs
    provenance = prep_manifest.get("network_input_provenance")
    if provenance:
        resolve_network_inputs(config, Path(provenance["manifest"]).parent)
        validate_prepared_network_inputs(config, prep_manifest)
    output_root = initialize_output_root(output_root)

    search = config.get("search", {})
    map_mode = str(search.get("map_mode", "weighted"))
    if map_mode not in {"weighted", "binary"}:
        raise ValueError("search.map_mode must be weighted or binary")
    policy, policy_source = resolve_policy(config)
    fraction = float(policy["target_weighted_coverage_fraction"])
    policy_path = output_root / "ResolvedSearchPolicy.json"
    policy_path.write_text(json.dumps({
        **policy,
        "base_policy_file": str(policy_source),
        "map_mode": map_mode,
        "coverage_measure": ("probability_weighted_surface_area"
                             if map_mode == "weighted" else
                             "binary_target_surface_area"),
    }, indent=2) + "\n")
    retention = resolve_retention(str(config.get("retention", {}).get(
        "profile", "standard")))
    shared = shared_configuration(config, retention)
    sweep = orientations(search)
    generator = require_file(
        Path(__file__).resolve().parents[1] / "search" /
        "generate_target_projection_coverage_plan.py",
        "coverage-grid generator")
    prepared = target_by_name(prep_manifest)
    manifest = {
        "schema_version": 2,
        "scalp_surface_sha256": file_sha256(Path(shared['skin'])),
        "subject": str(config["subject"]),
        "configuration": str(config_path),
        "configuration_sha256": file_sha256(config_path),
        "network_metadata": config["inputs"]["network_metadata"],
        "network_input_provenance": config.get("network_input_provenance", {}),
        "target_preparation": str(preparation),
        "target_preparation_manifest": str(
            preparation / "TargetPreparationManifest.json"),
        "target_preparation_source_mode": "python",
        "map_mode": map_mode,
        "policy": str(policy_path),
        "base_policy": str(policy_source),
        "retention_profile": retention["profile"],
        "targets": [],
    }
    for spec in config["targets"]:
        requested_name = str(spec["network"])
        display = str(spec.get("display_name", requested_name))
        if requested_name not in prepared and display not in prepared:
            raise ValueError(
                f"Target preparation does not contain {requested_name!r}")
        prep_item = prepared.get(requested_name, prepared.get(display))
        name = str(prep_item["network"])
        coil = str(require_file(
            spec.get("coil", shared["coil"]), f"{display} coil model"))
        coil_hardware_name = str(spec.get(
            "coil_hardware_name", Path(coil).stem.replace("_", " ")))
        coil_calibration = spec.get("coil_calibration")
        target_dir = require_directory(prep_item["output_directory"], "target directory")
        files = target_files(target_dir)
        target_root = output_root / "targets" / display
        plan_root = target_root / "search" / "coarse" / "plan"
        command = [
            sys.executable, str(generator),
            "--weighted-target", str(files[
                "eligible_weighted" if map_mode == "weighted" else "eligible_binary"]),
            "--target-map-mode", map_mode,
            "--eligible-support", str(files["eligible_binary"]),
            "--cluster-labels", str(files["components"]),
            "--vertex-area", shared["vertex_area"],
            "--left-pial", shared["surfaces"]["left_pial"],
            "--skin", shared["skin"],
            "--coverage-fractions", str(fraction),
            "--coverage-radius-mm", str(policy["coverage_radius_mm"]),
            "--candidate-margin-mm", str(policy["candidate_margin_mm"]),
            "--minimum-design-cluster-vertices",
            str(policy["minimum_design_cluster_vertices"]),
            "--minimum-design-cluster-area-mm2",
            str(policy["minimum_design_cluster_area_mm2"]),
            "--minimum-design-cluster-relative-weighted-area-fraction",
            str(policy["minimum_design_cluster_relative_weighted_area_fraction"]),
            "--minimum-centers", str(policy["minimum_centers"]),
            "--maximum-centers", str(policy["maximum_centers"]),
            "--network", name,
            "--output", str(plan_root),
        ]
        run_plan(generator, command, name)
        plan = plan_directory(plan_root, fraction)
        rows = read_tsv(plan / "SearchGrid.tsv")
        coverage = load_json(plan / "CoverageSummary.json", "coverage summary")
        anchors = [int(value) for value in
                   coverage["mandatory_centroid_anchor_scalp_vertices"]]
        grid_vertices = {int(row["scalp_vertex"]) for row in rows}
        if not anchors or not set(anchors).issubset(grid_vertices):
            raise ValueError(f"Mandatory centroid anchor missing for {name}")
        scoring_spec = spec.get("scoring", {})
        scoring = {
            "constrain_target_to_anatomical_mask": bool(
                scoring_spec.get("constrain_target_to_anatomical_mask", False)),
            "anatomical_mask": str(files["anatomical_mask"]),
            "restrict_weights_to_binary_support": bool(scoring_spec.get(
                "restrict_weights_to_binary_support", map_mode == "binary")),
            "restrict_target_to_design_components": (
                policy["scoring_target_scope"] == "retained_eligible_components"),
            "component_labels": str(files["components"]),
            "design_component_ids": coverage["design_cluster_ids"],
            "minimum_target_to_global_peak_ratio": float(
                policy["minimum_target_to_global_peak_ratio"]),
            "enforce_target_peak_ratio_for_selection": bool(
                policy["enforce_target_peak_ratio_for_nominal_selection"]),
            "report_full_network_scores": bool(policy["report_full_network_scores"]),
            "anatomical_zone_display": str(
                spec.get("anatomical_zone_display", "configured anatomical zone")),
        }
        config_json = {
            **shared,
            "coil_center_constraint": resolve_coil_center_constraint(config, spec),
            "coil": coil,
            "coil_hardware_name": coil_hardware_name,
            "coil_calibration": coil_calibration,
            "map_mode": map_mode,
            "network": name,
            "display_name": display,
            "anatomical_zone_display": scoring["anatomical_zone_display"],
            "strategy": "target_projection_coverage_default",
            "grid_tsv": str(plan / "SearchGrid.tsv"),
            "target_map": str(files[
                "full_weighted" if map_mode == "weighted" else "full_binary"]),
            "weighted_target_map": str(files["full_weighted"]),
            "binary_target_map": str(files["full_binary"]),
            "target_preparation": str(target_dir),
            "orientations_degrees": sweep,
            "scoring": scoring,
            "coarse_grid_policy": {
                **policy,
                "policy_file": str(policy_path),
                "coverage_measure": coverage["coverage_measure"],
                "coverage_summary": str(plan / "CoverageSummary.json"),
                "component_design_status": str(plan_root / "ComponentDesignStatus.tsv"),
                "mandatory_centroid_anchor_scalp_vertices": anchors,
                "achieved_target_weighted_coverage_fraction": coverage[
                    "achieved_target_weighted_coverage_fraction"],
            },
            "output_root": str(target_root / "search" / "coarse" / "simulation"),
            "grid_coverage_provenance": str(plan / "CoverageSummary.json"),
        }
        config_file = target_root / "CoarseRunConfig.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(config_json, indent=2) + "\n")
        manifest["targets"].append({
            "network": name,
            "display_name": display,
            "anatomical_zone": scoring["anatomical_zone_display"],
            "target_root": str(target_root),
            "coarse_config": str(config_file),
            "coarse_centers": len(rows),
            "coarse_poses": len(rows) * len(sweep),
            "coverage_fraction": coverage.get(
                "achieved_target_coverage_fraction",
                coverage["achieved_target_weighted_coverage_fraction"]),
            "coverage_measure": coverage["coverage_measure"],
            "map_mode": map_mode,
        })
    manifest_path = output_root / "MultinetworkRunManifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--target-preparation", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.config, args.target_preparation, args.output), indent=2))


if __name__ == "__main__":
    main()
