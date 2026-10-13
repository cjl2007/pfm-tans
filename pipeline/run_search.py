#!/usr/bin/env python3
"""Run the locked V2 search engine using the semantic Python pipeline layout."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'search'))
from selected_pose import robustness_enabled, selected_pose_paths
from coil_center_constraint import (
    resolve_coil_center_constraint, validate_reused_constraint,
)


STAGE_ORDER = ("coarse", "fine", "robustness")


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def load_json(path: str | Path, label: str) -> dict:
    path = require_file(path, label)
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def stage_paths(target_root: Path) -> dict:
    search = target_root / "search"
    return {
        "coarse_result": search / "coarse" / "simulation",
        "fine_plan": search / "fine" / "plan",
        "fine_result": search / "fine" / "simulation",
        "robust_plan": search / "robustness" / "plan",
        "robust_result": search / "robustness" / "simulation",
        "robust_analysis": search / "robustness" / "analysis",
    }


def run(command: Sequence[str], label: str, dry_run: bool = False) -> float:
    printable = " ".join(str(value) for value in command)
    print(f"[python-tans] {label}\n[python-tans] command: {printable}", flush=True)
    if dry_run:
        return 0.0
    started = time.perf_counter()
    completed = subprocess.run(command)
    elapsed = time.perf_counter() - started
    if completed.returncode:
        raise RuntimeError(f"{label} failed with status {completed.returncode}")
    return elapsed


def write_state(path: Path, target: Mapping[str, object], stage: str,
                status: str, wall_seconds: float) -> None:
    state = load_json(path, "run state") if path.is_file() else {
        "schema_version": 1,
        "network": target["network"],
        "display_name": target["display_name"],
        "stages": {},
    }
    state["stages"][stage] = {
        "status": status,
        "orchestration_wall_seconds": wall_seconds,
    }
    path.write_text(json.dumps(state, indent=2) + "\n")


def write_runtime_summary(target: Mapping[str, object], paths: Mapping[str, Path],
                          enabled: bool = True) -> dict:
    coarse = load_json(
        paths["coarse_result"] / "CoarseRunSummary.json", "coarse summary")
    fine = load_json(
        paths["fine_result"] / "CoarseRunSummary.json", "fine summary")
    plan = load_json(
        paths["robust_plan"] / "RobustPlanSummary.json", "robust plan summary"
    ) if enabled else {'planning_wall_seconds': 0.0}
    robust = load_json(
        paths["robust_result"] / "CoarseRunSummary.json", "robust summary"
    ) if enabled else {'wall_seconds': 0.0, 'poses': 0}
    analysis = load_json(
        paths["robust_analysis"] / "RobustnessSummary.json",
        "robustness analysis summary") if enabled else {'analysis_wall_seconds': 0.0}
    baseline = float(coarse["wall_seconds"]) + float(fine["wall_seconds"])
    robust_seconds = (
        float(plan["planning_wall_seconds"]) + float(robust["wall_seconds"]) +
        float(analysis["analysis_wall_seconds"]))
    summary = {
        "schema_version": 1,
        "robustness_enabled": enabled,
        "robustness_status": 'evaluated' if enabled else 'not_evaluated_disabled_by_config',
        "network": target["network"],
        "display_name": target["display_name"],
        "coarse_wall_seconds": float(coarse["wall_seconds"]),
        "fine_wall_seconds": float(fine["wall_seconds"]),
        "baseline_coarse_plus_fine_wall_seconds": baseline,
        "robustness_planning_wall_seconds": float(plan["planning_wall_seconds"]),
        "robustness_simulation_wall_seconds": float(robust["wall_seconds"]),
        "robustness_analysis_wall_seconds": float(analysis["analysis_wall_seconds"]),
        "robustness_total_wall_seconds": robust_seconds,
        "robustness_runtime_overhead_fraction": (
            robust_seconds / baseline if baseline else None),
        "coarse_poses": int(coarse["poses"]),
        "fine_poses": int(fine["poses"]),
        "robustness_poses": int(robust["poses"]),
        "raw_simulation_bytes_pruned": sum(int(item.get(
            "raw_simulation_bytes_pruned", 0)) for item in (coarse, fine, robust)),
    }
    root = Path(target["target_root"])
    (root / "RuntimeSummary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    return summary


def run_target(target: Mapping[str, object], module: Path, workers: int,
               stop_after: str, dry_run: bool) -> dict:
    root = Path(target["target_root"]).resolve()
    config_path = require_file(target["coarse_config"], "coarse configuration")
    config = load_json(config_path, "coarse configuration")
    enabled = robustness_enabled(config)
    paths = stage_paths(root)
    if not enabled and any(paths[key].exists() for key in (
            'robust_plan', 'robust_result', 'robust_analysis')):
        raise ValueError('Robustness is disabled but robustness outputs already exist; review before proceeding')
    state_path = root / "RunState.json"
    elapsed = {}

    coarse_summary = paths["coarse_result"] / "CoarseRunSummary.json"
    if coarse_summary.is_file():
        validate_reused_constraint(config, paths["coarse_result"])
        elapsed["coarse"] = 0.0
        write_state(state_path, target, "coarse", "reused", 0.0)
    else:
        command = [sys.executable, str(module / "run_simulations.py"),
                   str(config_path), "--workers", str(workers)]
        if paths["coarse_result"].exists():
            command.append("--resume")
        elapsed["coarse"] = run(command, f"{target['display_name']} coarse search",
                                dry_run)
        if not dry_run:
            write_state(state_path, target, "coarse", "complete", elapsed["coarse"])
    if stop_after == "coarse" or dry_run:
        return elapsed

    fine_config = paths["fine_plan"] / "FineRunConfig.json"
    if not fine_config.is_file():
        fine = config["fine_search"]
        command = [
            sys.executable, str(module / "generate_fine_plan.py"),
            "--coarse-config", str(config_path),
            "--coarse-result", str(paths["coarse_result"]),
            "--radius-mm", str(fine["radius_mm"]),
            "--spacing-mm", str(fine["spacing_mm"]),
            "--relative-orientations",
            *[str(value) for value in fine["relative_orientations_degrees"]],
            "--output", str(paths["fine_plan"]),
            "--simulation-output", str(paths["fine_result"]),
        ]
        run(command, f"{target['display_name']} fine plan")
    fine_summary = paths["fine_result"] / "CoarseRunSummary.json"
    fine_cfg = load_json(fine_config, "fine configuration")
    if resolve_coil_center_constraint(fine_cfg) != resolve_coil_center_constraint(config):
        raise ValueError("Coil-center constraint changed since fine planning; use a new output root")
    if fine_summary.is_file():
        validate_reused_constraint(fine_cfg, paths["fine_result"])
        elapsed["fine"] = 0.0
        write_state(state_path, target, "fine", "reused", 0.0)
    else:
        command = [sys.executable, str(module / "run_simulations.py"),
                   str(fine_config), "--workers", str(workers)]
        if paths["fine_result"].exists():
            command.append("--resume")
        elapsed["fine"] = run(command, f"{target['display_name']} fine search")
        write_state(state_path, target, "fine", "complete", elapsed["fine"])
    if stop_after == "fine":
        return elapsed

    if not enabled:
        selected = selected_pose_paths(root, config_path)
        best = load_json(selected['best_pose'], 'nominal fine winner')
        if str(best.get('passes_target_peak_ratio', '')).lower() not in ('true', '1'):
            raise ValueError('Nominal fine winner does not pass the configured peak-proximity gate')
        write_state(state_path, target, 'robustness', 'skipped_by_config', 0.0)
        (root / 'SelectionSummary.json').write_text(json.dumps({
            'selection_mode': selected['mode'],
            'selected_pose_source': str(selected['best_pose']),
            'simulation_source': str(selected['result']),
            'robustness_status': selected['robustness_status'],
            'additional_placement_error_simulations': 0,
        }, indent=2) + '\n')
        write_runtime_summary(target, paths, enabled=False)
        return elapsed

    robust_config = paths["robust_plan"] / "RobustRunConfig.json"
    if not robust_config.is_file():
        run([
            sys.executable, str(module / "generate_robustness_plan.py"),
            "--fine-config", str(fine_config),
            "--fine-result", str(paths["fine_result"]),
            "--output", str(paths["robust_plan"]),
            "--simulation-output", str(paths["robust_result"]),
        ], f"{target['display_name']} robustness plan")
    robust_summary = paths["robust_result"] / "CoarseRunSummary.json"
    if robust_summary.is_file():
        validate_reused_constraint(fine_cfg, paths["robust_result"], check_winner=False)
        elapsed["robustness_simulation"] = 0.0
    else:
        command = [sys.executable, str(module / "run_simulations.py"),
                   str(robust_config), "--workers", str(workers)]
        if paths["robust_result"].exists():
            command.append("--resume")
        elapsed["robustness_simulation"] = run(
            command, f"{target['display_name']} robustness simulation")
    robust_selection = paths["robust_analysis"] / "RobustnessSummary.json"
    if robust_selection.is_file():
        validate_reused_constraint(
            fine_cfg, paths["robust_result"], paths["robust_analysis"] / "BestRobustPose.json")
        elapsed["robustness_analysis"] = 0.0
    else:
        elapsed["robustness_analysis"] = run([
            sys.executable, str(module / "evaluate_robust_placements.py"),
            "--fine-result", str(paths["fine_result"]),
            "--robust-plan", str(paths["robust_plan"]),
            "--robust-result", str(paths["robust_result"]),
            "--output", str(paths["robust_analysis"]),
        ], f"{target['display_name']} robust selection")
    write_state(
        state_path, target, "robustness", "complete",
        elapsed["robustness_simulation"] + elapsed["robustness_analysis"])
    write_runtime_summary(target, paths)
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--network", action="append", default=[])
    parser.add_argument("--stop-after", choices=STAGE_ORDER, default="robustness")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("workers must be positive")
    manifest_path = require_file(args.manifest, "run manifest")
    manifest = load_json(manifest_path, "run manifest")
    from prepare_subject import validate_search_scalp
    validate_search_scalp(manifest)
    selected = set(args.network)
    targets = [item for item in manifest["targets"]
               if not selected or item["network"] in selected
               or item["display_name"] in selected]
    if not targets:
        raise ValueError("No manifest targets match --network")
    module = Path(__file__).resolve().parents[1] / "search"
    rows = []
    for target in targets:
        timing = run_target(target, module, args.workers, args.stop_after, args.dry_run)
        rows.append({"network": target["network"], **timing})
    if not args.dry_run:
        fields = sorted({key for row in rows for key in row})
        with (manifest_path.parent / "OrchestrationTiming.tsv").open(
                "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({"targets": rows, "stop_after": args.stop_after,
                      "dry_run": args.dry_run}, indent=2))


if __name__ == "__main__":
    main()
