#!/usr/bin/env python3
"""Generate bounded positional/orientation perturbations around fine finalists."""
import argparse
import csv
import json
from pathlib import Path
import time

import nibabel as nib
import numpy as np
from scipy.io import savemat
from scipy.optimize import linear_sum_assignment
from scipy.sparse.csgraph import dijkstra

from generate_fine_plan import load_surface, mesh_graph
from coil_center_constraint import validate_reused_constraint


def read_tsv(path):
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def circular_angle_difference(first, second, period=180.0):
    difference = abs(float(first) - float(second)) % period
    return min(difference, period - difference)


def tsv_boolean(value):
    return str(value).strip().lower() in {"1", "true", "yes"}


def vertex_normals(points, triangles):
    normals = np.zeros_like(points, dtype=float)
    face_normals = np.cross(
        points[triangles[:, 1]] - points[triangles[:, 0]],
        points[triangles[:, 2]] - points[triangles[:, 0]])
    for column in range(3):
        np.add.at(normals, triangles[:, column], face_normals)
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1.0
    return normals / lengths[:, None]


def tangent_basis(normal):
    helper = np.asarray([0.0, 0.0, 1.0])
    if abs(np.dot(normal, helper)) > 0.9:
        helper = np.asarray([0.0, 1.0, 0.0])
    first = np.cross(helper, normal)
    first /= np.linalg.norm(first)
    second = np.cross(normal, first)
    second /= np.linalg.norm(second)
    return first, second


def choose_finalists(score_rows, points, graph, relative_floor,
                     maximum_finalists, minimum_separation_mm):
    gate_eligible = [
        row for row in score_rows
        if ("passes_target_peak_ratio" not in row or
            tsv_boolean(row["passes_target_peak_ratio"])) and
           ("eligible_for_nominal_selection" not in row or
            tsv_boolean(row["eligible_for_nominal_selection"]))]
    if not gate_eligible:
        return []
    best = max(float(row["mean_on_target_score"]) for row in gate_eligible)
    eligible = [row for row in gate_eligible
                if float(row["mean_on_target_score"]) + 1e-12 >=
                relative_floor * best]
    eligible.sort(key=lambda row: (
        -float(row["mean_on_target_score"]), int(row["center_id"]),
        float(row["orientation_degrees"])))
    selected = []
    distance_cache = {}
    for row in eligible:
        vertex = int(row["scalp_vertex"]) - 1
        keep = True
        for prior in selected:
            prior_vertex = int(prior["scalp_vertex"]) - 1
            key = prior_vertex
            if key not in distance_cache:
                distance_cache[key] = dijkstra(
                    graph, indices=prior_vertex, directed=False,
                    limit=minimum_separation_mm)
            close_in_space = (
                distance_cache[key][vertex] < minimum_separation_mm - 1e-8)
            close_in_angle = circular_angle_difference(
                row["orientation_degrees"], prior["orientation_degrees"]) <= 5.0
            if close_in_space and close_in_angle:
                keep = False
                break
        if keep:
            selected.append(row)
            if len(selected) == maximum_finalists:
                break
    return selected


def directional_ring(points, graph, normals, anchor, radius_mm, samples):
    if not np.isfinite(radius_mm) or radius_mm <= 0 or samples < 1:
        raise ValueError('A positive finite radius and sample count are required')
    distances = dijkstra(
        graph, indices=anchor, directed=False, limit=radius_mm + 1e-8)
    candidates = np.flatnonzero(
        (distances > 1e-8) & (distances <= radius_mm + 1e-8))
    if not len(candidates):
        raise ValueError(f'No distinct scalp vertices within {radius_mm:g} mm of anchor {anchor + 1}')
    first, second = tangent_basis(normals[anchor])
    centered = points[candidates] - points[anchor]
    centered -= (centered @ normals[anchor])[:, None] * normals[anchor]
    lengths = np.linalg.norm(centered, axis=1)
    lengths[lengths == 0] = 1.0
    directions = centered / lengths[:, None]
    # The FEM skin mesh may have fewer than eight distinct vertices in a small
    # error disk. Never enlarge that disk or count duplicates as extra samples.
    count = min(int(samples), len(candidates))
    angles = 2 * np.pi * np.arange(count) / count
    desired = np.cos(angles)[:, None] * first + np.sin(angles)[:, None] * second
    angular_cost = 1.0 - desired @ directions.T
    radial_cost = (radius_mm - distances[candidates]) / radius_mm
    rows, columns = linear_sum_assignment(angular_cost + radial_cost[None, :])
    selected = candidates[columns[np.argsort(rows)]].astype(int).tolist()
    return selected, distances


def ring_diagnostics(points, normals, anchor, selected, distances, radius_mm, requested):
    first, second = tangent_basis(normals[anchor])
    offsets = points[selected] - points[anchor]
    angles = np.mod(np.degrees(np.arctan2(offsets @ second, offsets @ first)), 360.)
    ordered = np.sort(angles)
    maximum_gap = float(np.diff(np.r_[ordered, ordered[0] + 360.]).max())
    return {'requested_radius_mm': radius_mm,
            'requested_samples': requested, 'actual_samples': len(selected),
            'mesh_limited': len(selected) < requested,
            'minimum_actual_radius_mm': float(distances[selected].min()),
            'maximum_actual_radius_mm': float(distances[selected].max()),
            'maximum_azimuth_gap_degrees': maximum_gap,
            'actual_azimuth_degrees': angles.tolist()}


def main():
    started = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument("--fine-config", required=True)
    parser.add_argument("--fine-result", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--simulation-output", required=True)
    args = parser.parse_args()

    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite robustness plan: {output}")
    cfg = json.loads(Path(args.fine_config).read_text())
    validate_reused_constraint(cfg, args.fine_result)
    policy = cfg["robustness"]
    score_rows = read_tsv(Path(args.fine_result) / "CoarsePoseScores.tsv")
    skin_image, points, triangles = load_surface(cfg["skin"])
    graph = mesh_graph(points, triangles)
    normals = vertex_normals(points, triangles)
    finalists = choose_finalists(
        score_rows, points, graph, float(policy["nominal_relative_floor"]),
        int(policy["maximum_finalists"]),
        float(policy["minimum_finalist_separation_mm"]))
    if not finalists:
        raise RuntimeError("No robustness finalists were selected")

    vertex_to_center = {}
    grid_rows = []
    perturbations = []
    sampling_diagnostics = []

    def add_center(vertex):
        if vertex not in vertex_to_center:
            center_id = len(grid_rows) + 1
            vertex_to_center[vertex] = center_id
            grid_rows.append({
                "center_id": center_id, "scalp_vertex": vertex + 1,
                "x": points[vertex, 0], "y": points[vertex, 1],
                "z": points[vertex, 2],
                "strategy": "robustness_perturbation_validation",
                "source_cluster_id": "NaN", "target_map_mode": cfg["map_mode"],
                "network_name": cfg["network"],
            })
        return vertex_to_center[vertex]

    for finalist_id, finalist in enumerate(finalists, 1):
        anchor = int(finalist["scalp_vertex"]) - 1
        center_id = add_center(anchor)
        perturbations.append({
            "finalist_id": finalist_id,
            "fine_center_id": int(finalist["center_id"]),
            "fine_orientation_degrees": float(finalist["orientation_degrees"]),
            "robust_center_id": center_id, "scalp_vertex": anchor + 1,
            "requested_radius_mm": 0.0, "actual_geodesic_radius_mm": 0.0,
            "azimuth_degrees": "NaN",
            "actual_azimuth_degrees": "NaN",
            "nominal_mean_on_target_score": float(
                finalist["mean_on_target_score"]),
        })
        for radius in policy["position_error_radii_mm"]:
            ring, distances = directional_ring(
                points, graph, normals, anchor, float(radius),
                int(policy["azimuth_samples_per_radius"]))
            diagnostic = ring_diagnostics(points, normals, anchor, ring, distances,
                float(radius), int(policy['azimuth_samples_per_radius']))
            sampling_diagnostics.append({'finalist_id': finalist_id, **diagnostic})
            if diagnostic['maximum_azimuth_gap_degrees'] >= 180.:
                raise ValueError(f'Insufficient directional scalp coverage: {sampling_diagnostics[-1]}')
            for direction_index, vertex in enumerate(ring):
                perturbations.append({
                    "finalist_id": finalist_id,
                    "fine_center_id": int(finalist["center_id"]),
                    "fine_orientation_degrees": float(
                        finalist["orientation_degrees"]),
                    "robust_center_id": add_center(vertex),
                    "scalp_vertex": vertex + 1,
                    "requested_radius_mm": float(radius),
                    "actual_geodesic_radius_mm": float(distances[vertex]),
                    "azimuth_degrees": (
                        360.0 * direction_index / len(ring)),
                    "actual_azimuth_degrees": diagnostic['actual_azimuth_degrees'][direction_index],
                    "nominal_mean_on_target_score": float(
                        finalist["mean_on_target_score"]),
                })

    output.mkdir(parents=True)
    grid_path = output / "SearchGrid.tsv"
    with grid_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t",
                                fieldnames=list(grid_rows[0]))
        writer.writeheader()
        writer.writerows(grid_rows)
    with (output / "Perturbations.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t",
                                fieldnames=list(perturbations[0]))
        writer.writeheader()
        writer.writerows(perturbations)

    selected = np.asarray([int(row["scalp_vertex"]) - 1 for row in grid_rows])
    metric = np.zeros(len(points), dtype=np.float32)
    metric[selected] = 1
    nib.save(nib.gifti.GiftiImage(darrays=[nib.gifti.GiftiDataArray(
        metric, intent="NIFTI_INTENT_SHAPE")]), output / "SearchGrid.shape.gii")
    savemat(output / "SearchGrid.mat", {
        "scalp_vertices_one_based": selected[:, None] + 1,
        "xyz": points[selected],
        "position_error_radii_mm": policy["position_error_radii_mm"],
    })

    maximum_angle = max(float(value) for value in
                        policy["orientation_error_degrees"])
    relative_angles = np.arange(-maximum_angle, maximum_angle + 0.1, 5.0)
    orientations = sorted({
        float((float(row["orientation_degrees"]) + delta) % 180.0)
        for row in finalists for delta in relative_angles})
    robust_cfg = json.loads(json.dumps(cfg))
    robust_cfg["strategy"] = "robustness_perturbation_validation"
    robust_cfg["grid_tsv"] = str(grid_path)
    robust_cfg["orientations_degrees"] = orientations
    robust_cfg["output_root"] = str(Path(args.simulation_output).resolve())
    robust_cfg["robustness_plan"] = str(output)
    robust_cfg['robustness']['position_sampling_method'] = 'bounded_unique_vertex_angular_assignment_v1'
    robust_cfg.setdefault("scoring", {})[
        "enforce_target_peak_ratio_for_selection"] = False
    robust_cfg_path = output / "RobustRunConfig.json"
    robust_cfg_path.write_text(json.dumps(robust_cfg, indent=2) + "\n")
    summary = {
        "network": cfg["network"], "finalists": len(finalists),
        "unique_centers": len(grid_rows), "orientations": orientations,
        "poses": len(grid_rows) * len(orientations),
        "position_error_radii_mm": policy["position_error_radii_mm"],
        "azimuth_samples_per_radius": policy["azimuth_samples_per_radius"],
        "position_sampling_method": 'bounded_unique_vertex_angular_assignment_v1',
        "sampling_diagnostics": sampling_diagnostics,
        "orientation_error_degrees": policy["orientation_error_degrees"],
        "nominal_relative_floor": policy["nominal_relative_floor"],
        "planning_wall_seconds": time.perf_counter() - started,
        "finalist_fine_pose_ids": [{
            "fine_center_id": int(row["center_id"]),
            "orientation_degrees": float(row["orientation_degrees"]),
            "nominal_mean_on_target_score": float(row["mean_on_target_score"]),
        } for row in finalists],
    }
    (output / "RobustPlanSummary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print(json.dumps({**summary, "config": str(robust_cfg_path)}, indent=2))


if __name__ == "__main__":
    main()
