#!/usr/bin/env python3
"""Evaluate fine-pose finalists under sampled placement perturbations."""
import argparse
import csv
import json
from pathlib import Path
import time

import numpy as np

from generate_robustness_plan import circular_angle_difference
from coil_center_constraint import CoilCenterConstraint, validate_reused_constraint


def read_tsv(path):
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def lower_tail_mean(values, fraction=0.10):
    values = np.sort(np.asarray(values, dtype=float))
    count = max(1, int(np.ceil(fraction * len(values))))
    return float(np.mean(values[:count]))


def centers_within_radius(rows, radius):
    # Use measured displacement, not the requested ring name.
    return {int(row['robust_center_id']) for row in rows
            if float(row['actual_geodesic_radius_mm']) <= float(radius) + 1e-8}


def main():
    started = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument("--fine-result", required=True)
    parser.add_argument("--robust-plan", required=True)
    parser.add_argument("--robust-result", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite robustness analysis: {output}")
    output.mkdir(parents=True)

    plan = Path(args.robust_plan).resolve()
    result = Path(args.robust_result).resolve()
    cfg = json.loads((plan / "RobustRunConfig.json").read_text())
    validate_reused_constraint(cfg, args.fine_result)
    coil_constraint = CoilCenterConstraint(cfg)
    policy = cfg["robustness"]
    perturbations = read_tsv(plan / "Perturbations.tsv")
    robust_scores = read_tsv(result / "CoarsePoseScores.tsv")
    fine_scores = read_tsv(Path(args.fine_result) / "CoarsePoseScores.tsv")
    score_lookup = {
        (int(row["center_id"]), float(row["orientation_degrees"])):
        float(row["mean_on_target_score"]) for row in robust_scores}
    fine_lookup = {
        (int(row["center_id"]), float(row["orientation_degrees"])): row
        for row in fine_scores}
    finalist_ids = sorted({int(row["finalist_id"]) for row in perturbations})
    rows = []
    finalist_metadata = {}
    for finalist_id in finalist_ids:
        member_rows = [row for row in perturbations
                       if int(row["finalist_id"]) == finalist_id]
        nominal = next(row for row in member_rows
                       if float(row["requested_radius_mm"]) == 0.0)
        fine_key = (int(nominal["fine_center_id"]),
                    float(nominal["fine_orientation_degrees"]))
        coil_check = coil_constraint.evaluate_saved(
            result, nominal["robust_center_id"], fine_key[1])
        if not coil_check["passes_coil_center_constraint"]:
            raise ValueError("Robustness finalist coil center is outside the anatomical zone")
        finalist_metadata[finalist_id] = {
            "fine_row": fine_lookup[fine_key],
            "robust_center_id": int(nominal["robust_center_id"]),
        }
        for radius in policy["position_error_radii_mm"]:
            center_ids = centers_within_radius(member_rows, radius)
            for angle_tolerance in policy["orientation_error_degrees"]:
                values = [
                    score for (center_id, angle), score in score_lookup.items()
                    if center_id in center_ids and circular_angle_difference(
                        angle, fine_key[1]) <= float(angle_tolerance) + 1e-8]
                if not values:
                    raise RuntimeError(
                        f"No perturbation scores for finalist {finalist_id}")
                rows.append({
                    "finalist_id": finalist_id,
                    "fine_center_id": fine_key[0],
                    "fine_orientation_degrees": fine_key[1],
                    "nominal_mean_on_target_score": float(
                        nominal["nominal_mean_on_target_score"]),
                    "position_error_mm": float(radius),
                    "orientation_error_degrees": float(angle_tolerance),
                    "perturbed_pose_samples": len(values),
                    "perturbation_mean_score": float(np.mean(values)),
                    "perturbation_standard_deviation": float(np.std(values)),
                    "perturbation_10th_percentile": float(np.quantile(values, 0.10)),
                    "lower_tail_mean_10_percent": lower_tail_mean(values, 0.10),
                    "worst_case_score": float(np.min(values)),
                })
    default_radius = float(policy["default_position_error_mm"])
    default_angle = float(policy["default_orientation_error_degrees"])
    default_rows = [row for row in rows
                    if row["position_error_mm"] == default_radius
                    and row["orientation_error_degrees"] == default_angle]
    default_rows.sort(key=lambda row: (
        -row["lower_tail_mean_10_percent"],
        -row["perturbation_mean_score"],
        -row["nominal_mean_on_target_score"], row["finalist_id"]))
    winner_metrics = default_rows[0]
    metadata = finalist_metadata[winner_metrics["finalist_id"]]
    fine_row = metadata["fine_row"]
    winner = {
        **fine_row,
        "center_id": metadata["robust_center_id"],
        "fine_center_id": int(fine_row["center_id"]),
        "orientation_degrees": float(fine_row["orientation_degrees"]),
        "mean_on_target_score": float(fine_row["mean_on_target_score"]),
        "robustness": winner_metrics,
        "selection_rule": (
            f"among spatially distinct fine poses within {100 * float(policy['nominal_relative_floor']):g}% "
            "of the nominal maximum, maximize the lower-tail mean of sampled scores "
            f"under <={default_radius:g} mm measured scalp displacement and "
            f"<={default_angle:g} degree orientation error; ties: perturbation mean, "
            "nominal score, finalist ID"),
    }
    fields = list(rows[0])
    with (output / "RobustnessSensitivity.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (output / "BestRobustPose.json").write_text(
        json.dumps(winner, indent=2) + "\n")
    summary = {
        "network": cfg["network"], "default_position_error_mm": default_radius,
        "default_orientation_error_degrees": default_angle,
        "selection_metric": policy["selection_metric"],
        "winner_finalist_id": winner_metrics["finalist_id"],
        "winner_fine_center_id": winner["fine_center_id"],
        "winner_robust_center_id": winner["center_id"],
        "winner_orientation_degrees": winner["orientation_degrees"],
        "winner_nominal_score": winner["mean_on_target_score"],
        "winner_lower_tail_mean": winner_metrics["lower_tail_mean_10_percent"],
        "winner_perturbation_mean": winner_metrics["perturbation_mean_score"],
        "analysis_wall_seconds": time.perf_counter() - started,
        "sensitivity_table": "RobustnessSensitivity.tsv",
    }
    (output / "RobustnessSummary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
