#!/usr/bin/env python3
"""Generate reports and neuronavigation exports from semantic stage paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Mapping, Sequence

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'search'))
from selected_pose import robustness_enabled, selected_pose_paths


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def load_json(path: str | Path, label: str) -> dict:
    value = json.loads(require_file(path, label).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def run(command: Sequence[str], label: str) -> None:
    print(f"[python-tans] {label}", flush=True)
    completed = subprocess.run(command)
    if completed.returncode:
        raise RuntimeError(f"{label} failed with status {completed.returncode}")


def ensure_symlink(path: Path, target: Path) -> None:
    target = target.resolve()
    if path.is_symlink():
        if path.resolve() != target:
            raise ValueError(f"Compatibility link points elsewhere: {path}")
        return
    if path.exists():
        raise FileExistsError(f"Compatibility path already exists: {path}")
    path.symlink_to(target, target_is_directory=target.is_dir())


def compatibility_view(target: Mapping[str, object], reports: Path) -> Path:
    root = Path(target["target_root"]).resolve()
    view = root / "work" / "report_compatibility" / str(target["display_name"])
    view.mkdir(parents=True, exist_ok=True)
    search = root / "search"
    links = {
        "CoarseRunConfig.json": Path(target["coarse_config"]),
        "02_CoarseSimulation": search / "coarse" / "simulation",
        "03_FinePlan": search / "fine" / "plan",
        "04_FineSimulation": search / "fine" / "simulation",
    }
    if robustness_enabled(load_json(target['coarse_config'], 'coarse config')):
        links.update({
            '05_RobustnessPlan': search / 'robustness/plan',
            '06_RobustnessSimulation': search / 'robustness/simulation',
            '07_RobustnessAnalysis': search / 'robustness/analysis',
        })
    for name, source in links.items():
        if not source.exists():
            raise FileNotFoundError(f"Missing compatibility source: {source}")
        ensure_symlink(view / name, source)
    legacy_reports = view / "08_Reports"
    legacy_reports.mkdir(exist_ok=True)
    optimal = reports / "optimal-pose"
    if optimal.is_dir():
        ensure_symlink(legacy_reports / "OptimalPose", optimal)
    return view


def target_preparation_view(view: Path, source: Path) -> Path:
    target = view / "TargetPreparation"
    target.mkdir(exist_ok=True)
    aliases = {
        "SelectedGyralMask.dscalar.nii": "SelectedAnatomicalMask.dscalar.nii",
        "FullBinaryTargetSupport.dscalar.nii": "FullBinaryTargetSupport.dscalar.nii",
        "BinaryTargetSupportInsideGyralMask.dscalar.nii":
            "EligibleBinaryTarget.dscalar.nii",
        "WeightedTargetInsideGyralMask.dscalar.nii":
            "EligibleWeightedTarget.dscalar.nii",
        "FullWeightedTargetMap.dscalar.nii": "FullWeightedTargetMap.dscalar.nii",
        "ClusterLabels.dscalar.nii": "ComponentLabels.dscalar.nii",
    }
    for legacy_name, source_name in aliases.items():
        ensure_symlink(target / legacy_name, require_file(
            source / source_name, source_name))
    return target


def visual_inputs(config: Mapping[str, object]) -> tuple[Path, Path]:
    subject_dir = Path(config["subject_dir"])
    subject = str(config["subject"])
    inflated = require_file(
        config['surfaces'].get('left_inflated') or
        Path(config['surfaces']['left_pial']).with_name(
            f'{subject}.L.inflated.native.32k_fs_LR.surf.gii'),
        'left inflated surface in native head-model coordinates')
    sulc = require_file(
        subject_dir / "anat" / "MNINonLinear" / "fsaverage_LR32k" /
        f"{subject}.L.sulc.32k_fs_LR.shape.gii", "left sulcal metric")
    return inflated, sulc


def complete(directory: Path, names: Sequence[str]) -> bool:
    return all((directory / name).is_file() for name in names)


def render_target_core(target: Mapping[str, object], module: Path,
                       priors: Path, visual: Path) -> dict:
    root = Path(target["target_root"]).resolve()
    config_path = require_file(target["coarse_config"], "coarse configuration")
    config = load_json(config_path, "coarse configuration")
    search = root / "search"
    coarse_result = search / "coarse" / "simulation"
    fine_plan = search / "fine" / "plan"
    fine_result = search / "fine" / "simulation"
    robust_plan = search / "robustness" / "plan"
    robust_analysis = search / "robustness" / "analysis"
    selected = selected_pose_paths(root, config_path)
    best_pose = selected['best_pose']
    selected_config = selected['config']
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    view = compatibility_view(target, reports)
    preparation = target_preparation_view(
        view, Path(config["target_preparation"]).resolve())

    target_report = reports / "target-selection"
    if not complete(target_report, (
            "TargetSelectionSearchSummary.png",
            "TargetSelectionSearchSummary.json")):
        if target_report.exists():
            raise FileExistsError(f"Incomplete report requires review: {target_report}")
        coverage = Path(config["coarse_grid_policy"]["coverage_summary"]).parent
        command = [
            sys.executable, str(module / "render_target_selection_report.py"),
            "--config", str(config_path),
            "--target-preparation", str(preparation),
            "--coverage-plan", str(coverage),
            "--coarse-result", str(coarse_result),
            "--fine-plan", str(fine_plan),
            "--fine-result", str(fine_result),
            "--priors", str(priors),
            "--output", str(target_report),
        ]
        if selected['robustness_enabled']:
            command.extend(['--robust-plan', str(robust_plan),
                            '--robust-analysis', str(robust_analysis)])
        run(command, f"{target['display_name']} target-selection report")

    pose_report = reports / "optimal-pose"
    if not complete(pose_report, (
            "OptimalPoseOverview.png", "OptimalPoseThresholdSweep.gif",
            "VisualizationSummary.json")):
        if pose_report.exists():
            raise FileExistsError(f"Incomplete report requires review: {pose_report}")
        pose_config = load_json(selected_config, "selected-pose configuration")
        coil_stl = require_file(
            Path(pose_config["coil"]).with_suffix(".stl"), "coil STL")
        inflated, sulc = visual_inputs(config)
        run([
            sys.executable, str(module / "render_optimal_pose_qc.py"),
            "--config", str(selected_config),
            "--fine-result", str(selected['result']),
            "--best-pose", str(best_pose),
            "--priors", str(priors),
            "--coil-stl", str(coil_stl),
            "--left-inflated", str(inflated),
            "--left-sulc", str(sulc),
            "--visualization-config", str(visual),
            "--pose-label", ('Robust prescription' if selected['mode'] == 'robust' else 'Fine winner'),
            "--output", str(pose_report),
        ], f"{target['display_name']} optimal-pose report")

    view = compatibility_view(target, reports)
    replay = reports / "search-replay"
    from publication_paths import navigation_source
    export = navigation_source(root)
    if not (export / "NeuronavigationManifest.json").is_file():
        if export.exists():
            raise FileExistsError(f"Incomplete export requires review: {export}")
        run([
            config["simnibs_python"],
            str(module / "export_neuronavigation_package.py"),
            "--target-root", str(view),
            "--output", str(export),
        ], f"{target['display_name']} neuronavigation export")
    return {
        "network": target["network"],
        "coil_model": config["coil"],
        "coil_hardware_name": config.get(
            "coil_hardware_name", Path(config["coil"]).stem.replace("_", " ")),
        "coil_calibration_id": (
            config.get("coil_calibration") or {}).get("id"),
        "target_selection": str(target_report),
        "optimal_pose": str(pose_report),
        "search_replay": str(replay),
        "neuronavigation": str(export),
        "selected_pose": str(best_pose),
        "selection_mode": selected['mode'],
        "robustness_status": selected['robustness_status'],
    }


def render_search_replay(target: Mapping[str, object], module: Path,
                         priors: Path, replay_fps: float) -> None:
    root = Path(target['target_root']).resolve()
    replay = root / 'reports/search-replay'
    if complete(replay, ('SearchOptimizationReplay.mp4', 'SearchOptimizationReplay.json')):
        return
    if replay.exists():
        raise FileExistsError(f'Incomplete report requires review: {replay}')
    config = load_json(target['coarse_config'], 'coarse configuration')
    view = compatibility_view(target, root / 'reports')
    coil_stl = require_file(Path(config['coil']).with_suffix('.stl'), 'coil STL')
    run([
        sys.executable, str(module / 'render_search_optimization_replay.py'),
        '--target-root', str(view), '--priors', str(priors),
        '--coil-stl', str(coil_stl), '--output', str(replay),
        '--fps', str(replay_fps), '--final-hold-seconds', '2.0',
    ], f"{target['display_name']} search replay")


def render_dose_calibration(targets: Sequence[Mapping[str, object]],
                            module: Path, output_root: Path, priors: Path,
                            config: Mapping[str, object]) -> Path | None:
    calibration = next(
        (item for item in targets
         if str(item["network"]) == "Somatomotor_Hand"), None)
    treatments = [
        item for item in targets
        if str(item["network"]) != "Somatomotor_Hand"]
    if calibration is None or not treatments:
        return None

    calibration_view = compatibility_view(
        calibration, Path(calibration["target_root"]) / "reports")
    treatment_views = [
        compatibility_view(item, Path(item["target_root"]) / "reports")
        for item in treatments]
    from publication_paths import dose_source
    report = dose_source(output_root)
    expected = (
        "DoseCalibrationReport.html", "DoseCalibrationOverview.png",
        "DoseLookup.json", "DoseLookup.tsv")
    if not complete(report, expected):
        if report.exists():
            raise FileExistsError(
                f"Incomplete dose-calibration report requires review: {report}")
        command = [
            sys.executable,
            str(module / "generate_calibration_dose_report.py"),
            "--calibration-target-root", str(calibration_view),
            "--priors", str(priors),
            "--output", str(report),
        ]
        maximum_ratio = config.get("dosing", {}).get(
            "maximum_treatment_rmt_ratio", 1.20)
        if maximum_ratio in (None, ""):
            command.append("--disable-treatment-rmt-limit")
        else:
            command.extend([
                "--maximum-treatment-rmt-ratio", str(maximum_ratio)])
        for view in treatment_views:
            command.extend(["--treatment-target-root", str(view)])
        run(command, "cross-target dose-calibration report")
    else:
        from dose_report_defaults import ensure_report
        ensure_report(report, priors=priors)
    return report


def main() -> None:
    started = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--network", action="append", default=[])
    parser.add_argument("--priors")
    parser.add_argument("--visualization-config")
    parser.add_argument("--replay-fps", type=float, default=12.0)
    args = parser.parse_args()
    manifest_path = require_file(args.manifest, "run manifest")
    manifest = load_json(manifest_path, "run manifest")
    config = yaml.safe_load(require_file(
        manifest["configuration"], "resolved configuration").read_text())
    if not isinstance(config, dict):
        raise ValueError("Resolved configuration must contain a YAML mapping")
    from prepare_subject import validate_search_scalp
    validate_search_scalp(manifest)
    module = Path(__file__).resolve().parents[1] / "search"
    repo = module.parent
    priors = require_file(
        args.priors or manifest.get("network_metadata") or
        config.get("inputs", {}).get("network_metadata") or
        repo / "res0urces" / "Priors.mat", "network metadata")
    visual = require_file(
        args.visualization_config or
        module / "default_optimal_pose_visualization.json",
        "optimal-pose visualization config")
    selected = set(args.network)
    targets = [item for item in manifest["targets"]
               if not selected or item["network"] in selected
               or item["display_name"] in selected]
    if not targets:
        raise ValueError("No manifest targets match --network")
    from publish_operator_export import migrate_sources
    migrate_sources(manifest_path.parent)
    # Make every target's review/export and cross-target dosing available before movies.
    reports = [render_target_core(item, module, priors, visual)
               for item in targets]
    dose_report = render_dose_calibration(
        targets, module, manifest_path.parent, priors, config)
    # Explicit alternatives retain the original pipeline-selected entry points.
    if len(targets) == len(manifest['targets']):
        from generate_pose_variants import generate
        generate(manifest_path, priors, visual)
    for item in targets:
        render_search_replay(item, module, priors, args.replay_fps)
    report_index = manifest_path.parent / "ReportIndex.json"
    master_report = manifest_path.parent / "reports" / "index.html"
    report_index.write_text(json.dumps({
        "schema_version": 3,
        "subject": manifest["subject"],
        "targets": reports,
        "dose_calibration": str(dose_report) if dose_report else None,
        "master_report": str(master_report),
    }, indent=2) + "\n")
    if len(targets) == len(manifest['targets']):
        from publish_operator_export import publish
        publish(manifest_path.parent)
    from run_pipeline import record_pipeline_stage
    record_pipeline_stage(manifest_path.parent, 'reports', 'complete',
                          time.perf_counter() - started)
    run([
        sys.executable, str(Path(__file__).with_name("generate_master_report.py")),
        "--manifest", str(manifest_path),
        "--report-index", str(report_index),
        "--priors", str(priors),
        "--output", str(master_report),
    ], "master TANS report")
    print(json.dumps({"report_index": str(report_index)}, indent=2))


if __name__ == "__main__":
    main()
