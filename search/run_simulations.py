#!/usr/bin/env python3
"""Run and score resumable TANS coarse, fine, and robustness simulations."""
import argparse
import concurrent.futures
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from coil_center_constraint import (
    CoilCenterConstraint, resolve_coil_center_constraint, validate_reused_constraint,
)


def require_file(path, label):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def directory_bytes(path):
    return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())


def read_grid(path):
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    ids = [int(row["center_id"]) for row in rows]
    if ids != list(range(1, len(rows) + 1)):
        raise ValueError("Grid center IDs must be contiguous and one-based")
    return rows


def load_surface(path):
    image = nib.load(str(path))
    points = triangles = None
    for array in image.darrays:
        if array.intent == 1008:
            points = np.asarray(array.data, dtype=float)
        elif array.intent == 1009:
            triangles = np.asarray(array.data, dtype=np.int64)
    if points is None or triangles is None:
        raise ValueError(f"Surface lacks points or triangles: {path}")
    return points, triangles


def mesh_graph(points, triangles):
    edges = np.vstack((triangles[:, [0, 1]], triangles[:, [1, 2]],
                       triangles[:, [2, 0]]))
    edges.sort(axis=1)
    edges = np.unique(edges, axis=0)
    weights = np.linalg.norm(points[edges[:, 0]] - points[edges[:, 1]], axis=1)
    row = np.concatenate((edges[:, 0], edges[:, 1]))
    col = np.concatenate((edges[:, 1], edges[:, 0]))
    return csr_matrix((np.tile(weights, 2), (row, col)),
                      shape=(len(points), len(points)))


def validate_coarse_center_constraint(cfg, grid_rows):
    constraint = cfg.get("coarse_center_constraint")
    if not constraint:
        raise ValueError(
            "TANS requires coarse_center_constraint; an unconstrained anatomical "
            "scout is not permitted")
    expected_type = "geodesic_radius_from_target_patch_centroid_projection"
    if constraint.get("type") != expected_type:
        raise ValueError(
            f"coarse_center_constraint.type must be {expected_type!r}")
    maximum = float(constraint["maximum_distance_mm"])
    if not np.isfinite(maximum) or maximum <= 0:
        raise ValueError("maximum coarse-center distance must be finite and positive")
    points, triangles = load_surface(require_file(cfg["skin"], "skin surface"))
    anchor = int(constraint["anchor_scalp_vertex"]) - 1
    if anchor < 0 or anchor >= len(points):
        raise ValueError("Target-centroid scalp anchor is outside the skin surface")
    vertices = np.asarray([int(row["scalp_vertex"]) - 1 for row in grid_rows], dtype=int)
    if np.any((vertices < 0) | (vertices >= len(points))):
        raise ValueError("A coarse-grid vertex is outside the skin surface")
    coordinates = np.asarray([[float(row[key]) for key in ("x", "y", "z")]
                              for row in grid_rows])
    coordinate_error = np.linalg.norm(coordinates - points[vertices], axis=1)
    if np.max(coordinate_error) > 1e-5:
        raise ValueError(
            "Coarse-grid coordinates do not match their declared skin vertices; "
            f"maximum error={np.max(coordinate_error):.6g} mm")
    graph = mesh_graph(points, triangles)
    distance = dijkstra(graph, indices=anchor, directed=False,
                        limit=maximum + 1e-6)
    center_distances = distance[vertices]
    violations = ~np.isfinite(center_distances) | (center_distances > maximum + 1e-6)
    if np.any(violations):
        bad = [int(grid_rows[index]["center_id"])
               for index in np.flatnonzero(violations)]
        raise ValueError(
            f"Coarse centers {bad} exceed the {maximum:g} mm target-centroid "
            "geodesic-radius constraint")
    return {
        "type": expected_type,
        "centroid_mode": constraint.get("centroid_mode", "unspecified"),
        "target_cluster_id": constraint.get("target_cluster_id"),
        "anchor_scalp_vertex": anchor + 1,
        "maximum_allowed_distance_mm": maximum,
        "maximum_observed_distance_mm": float(np.max(center_distances)),
        "center_distances_mm": [float(value) for value in center_distances],
        "coordinate_match_maximum_error_mm": float(np.max(coordinate_error)),
        "verified": True,
    }


def validate_projection_coverage_policy(cfg, grid_rows):
    policy = cfg.get("coarse_grid_policy")
    if not policy:
        raise ValueError("Default projection coverage requires coarse_grid_policy")
    default_policy_path = Path(__file__).with_name("default_search_policy.json")
    authoritative_policy_path = Path(
        policy.get("policy_file", default_policy_path)).resolve()
    locked = json.loads(require_file(
        authoritative_policy_path, "authoritative search policy").read_text())
    checked_fields = (
        "policy_name", "policy_status", "target_weighted_coverage_fraction",
        "coverage_radius_mm", "candidate_margin_mm",
        "minimum_design_cluster_vertices", "minimum_design_cluster_area_mm2",
        "minimum_design_cluster_relative_weighted_area_fraction",
        "minimum_centers", "maximum_centers", "mandatory_centroid_anchor",
        "scoring_target_scope", "minimum_target_to_global_peak_ratio",
        "enforce_target_peak_ratio_for_nominal_selection",
        "report_full_network_scores",
        "surface_distance", "sampling", "randomness_used")
    mismatches = [field for field in checked_fields
                  if policy.get(field) != locked.get(field)]
    if mismatches:
        raise ValueError(f"Default coarse-grid policy mismatch: {mismatches}")
    summary_path = require_file(policy.get("coverage_summary"), "coverage summary")
    summary = json.loads(summary_path.read_text())
    map_mode = cfg.get("map_mode", "weighted")
    if summary.get("map_mode", "weighted") != map_mode:
        raise ValueError(
            "Coverage summary map mode does not match simulation map mode")
    requested = float(policy["target_weighted_coverage_fraction"])
    achieved = float(summary.get(
        "achieved_target_coverage_fraction",
        summary["achieved_target_weighted_coverage_fraction"]))
    if achieved + 1e-12 < requested:
        raise ValueError(
            f"Projection coverage {achieved:.6f} is below requested {requested:.6f}")
    if int(summary["centers"]) != len(grid_rows):
        raise ValueError("Coverage summary center count does not match search grid")
    anchors = [int(value) for value in
               summary["mandatory_centroid_anchor_scalp_vertices"]]
    vertices = [int(row["scalp_vertex"]) for row in grid_rows]
    if not anchors or any(vertex not in vertices for vertex in anchors):
        raise ValueError("A mandatory component-centroid anchor is absent from the grid")
    points, _ = load_surface(require_file(cfg["skin"], "skin surface"))
    indices = np.asarray(vertices, dtype=int) - 1
    if np.any((indices < 0) | (indices >= len(points))):
        raise ValueError("A default coarse-grid vertex is outside the skin surface")
    coordinates = np.asarray([[float(row[key]) for key in ("x", "y", "z")]
                              for row in grid_rows])
    coordinate_error = np.linalg.norm(coordinates - points[indices], axis=1)
    if np.max(coordinate_error) > 1e-5:
        raise ValueError("Default grid coordinates do not match declared skin vertices")
    return {
        "type": "target_projection_coverage_default",
        "policy_name": locked["policy_name"],
        "policy_file": str(authoritative_policy_path),
        "map_mode": map_mode,
        "coverage_measure": summary.get(
            "coverage_measure", "probability_weighted_surface_area"),
        "requested_target_weighted_coverage_fraction": requested,
        "requested_target_coverage_fraction": requested,
        "achieved_target_weighted_coverage_fraction": achieved,
        "achieved_target_coverage_fraction": achieved,
        "coverage_radius_mm": float(policy["coverage_radius_mm"]),
        "centers": len(grid_rows),
        "mandatory_centroid_anchor_scalp_vertices": anchors,
        "coordinate_match_maximum_error_mm": float(np.max(coordinate_error)),
        "verified": True,
    }


def normalized_name(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def matlab_prctile(values, percentiles):
    """Match MATLAB prctile's linear interpolation for finite vector data."""
    values = np.asarray(values, dtype=float).reshape(-1)
    percentiles = np.asarray(percentiles, dtype=float)
    if values.size == 0 or np.any(~np.isfinite(values)):
        raise ValueError("Percentiles require a nonempty finite vector")
    if np.any((percentiles < 0) | (percentiles > 100)):
        raise ValueError("Percentiles must lie in [0, 100]")
    ordered = np.sort(values)
    positions = percentiles / 100.0 * ordered.size + 0.5
    positions = np.clip(positions, 1.0, float(ordered.size))
    lower = np.floor(positions).astype(int)
    upper = np.ceil(positions).astype(int)
    fraction = positions - lower
    return ((1.0 - fraction) * ordered[lower - 1] +
            fraction * ordered[upper - 1])


def binary_target_from_label_map(label_path, target_image, network_name):
    label_image = nib.load(str(label_path))
    label_axis = label_image.header.get_axis(0)
    candidates = []
    for key, (name, _) in label_axis.label[0].items():
        if normalized_name(name) == normalized_name(network_name):
            candidates.append((int(key), name))
    if len(candidates) != 1:
        raise ValueError(
            f"Network name {network_name!r} resolved to {len(candidates)} label entries: "
            f"{candidates}")
    target_label, resolved_name = candidates[0]
    source_data = np.asarray(label_image.get_fdata(), dtype=float)[0]
    source_axis = label_image.header.get_axis(1)
    source = {name: (model.vertex, source_data[data_slice])
              for name, data_slice, model in source_axis.iter_structures()
              if hasattr(model, "vertex")}
    target_axis = target_image.header.get_axis(1)
    binary = np.zeros(target_image.shape[1], dtype=float)
    for name, data_slice, model in target_axis.iter_structures():
        if name not in source:
            raise ValueError(f"Network label map lacks target structure {name}")
        source_vertices, source_values = source[name]
        lookup = np.full(max(source_vertices.max(), model.vertex.max()) + 1, np.nan)
        lookup[source_vertices] = source_values
        values = lookup[model.vertex]
        if np.any(~np.isfinite(values)):
            raise ValueError(f"Network label cortical indexing mismatch for {name}")
        binary[data_slice] = values == target_label
    return binary, target_label, resolved_name


def apply_scoring_domain(target, binary_target, scoring_cfg, mask_values=None,
                         component_values=None):
    """Apply explicit scoring-domain restrictions without changing the denominator."""
    target = np.asarray(target, dtype=float).copy()
    binary = (None if binary_target is None else
              np.asarray(binary_target, dtype=float).copy())
    constrain_to_anatomy = bool(
        scoring_cfg.get("constrain_target_to_anatomical_mask", False))
    restrict_weights_to_support = bool(
        scoring_cfg.get("restrict_weights_to_binary_support", False))
    restrict_to_components = bool(
        scoring_cfg.get("restrict_target_to_design_components", False))

    anatomical_mask = np.ones(target.shape, dtype=bool)
    if constrain_to_anatomy:
        if mask_values is None:
            raise ValueError(
                "An anatomical mask is required when "
                "scoring.constrain_target_to_anatomical_mask is true")
        mask_values = np.asarray(mask_values, dtype=float).reshape(-1)
        if mask_values.shape != target.shape:
            raise ValueError("Scoring anatomical mask CIFTI dimensions differ")
        if not np.all(np.isfinite(mask_values)):
            raise ValueError("Scoring anatomical mask contains nonfinite values")
        if not np.all(np.isin(np.unique(mask_values), [0.0, 1.0])):
            raise ValueError(
                "Scoring anatomical mask must be explicitly binary (0 or 1)")
        anatomical_mask = mask_values == 1.0
        if not np.any(anatomical_mask):
            raise ValueError("Scoring anatomical mask is empty")
        target *= anatomical_mask
        if binary is not None:
            binary *= anatomical_mask

    if restrict_weights_to_support:
        if binary is None:
            raise ValueError(
                "A binary network label map is required when "
                "scoring.restrict_weights_to_binary_support is true")
        target *= binary > 0

    if restrict_to_components:
        component_ids = [int(value) for value in
                         scoring_cfg.get("design_component_ids", [])]
        if not component_ids:
            raise ValueError(
                "scoring.design_component_ids is required when "
                "scoring.restrict_target_to_design_components is true")
        if component_values is None:
            raise ValueError(
                "Component labels are required when "
                "scoring.restrict_target_to_design_components is true")
        component_values = np.asarray(component_values, dtype=float).reshape(-1)
        if component_values.shape != target.shape:
            raise ValueError("Scoring component-label CIFTI dimensions differ")
        if not np.all(np.isfinite(component_values)):
            raise ValueError("Scoring component labels contain nonfinite values")
        component_mask = np.isin(component_values, component_ids)
        if not np.any(component_mask):
            raise ValueError("Configured design components are absent from labels")
        target *= component_mask
        if binary is not None:
            binary *= component_mask

    if not np.any(target != 0):
        raise ValueError("No weighted target vertices remain in scoring domain")
    if binary is not None and not np.any(binary != 0):
        raise ValueError("No binary target vertices remain in scoring domain")

    if restrict_to_components:
        target_scope = "retained_eligible_components"
    elif constrain_to_anatomy and restrict_weights_to_support:
        target_scope = "binary_network_support_within_anatomical_mask"
    elif constrain_to_anatomy:
        target_scope = "network_weights_within_anatomical_mask"
    elif restrict_weights_to_support:
        target_scope = "binary_network_support"
    else:
        target_scope = "full_network"
    return target, binary, anatomical_mask, target_scope


def target_to_global_peak_ratio(field, target):
    """Return maximum E-field in positive target support divided by global max."""
    field = np.asarray(field, dtype=float).reshape(-1)
    target = np.asarray(target, dtype=float).reshape(-1)
    if field.shape != target.shape:
        raise ValueError("E-field and target dimensions differ")
    support = target > 0
    if not np.any(support):
        raise ValueError("Peak-ratio target support is empty")
    global_maximum = float(np.max(field))
    target_maximum = float(np.max(field[support]))
    ratio = 0.0 if global_maximum <= 0 else target_maximum / global_maximum
    return ratio, target_maximum, global_maximum


def center_command(cfg, center_id, output, resume=False):
    script = Path(__file__).with_name("benchmark_pose_batch.py").resolve()
    surfaces = cfg["surfaces"]
    command = [
        cfg["simnibs_python"], str(script),
        "--subject", cfg["subject"],
        "--grid-tsv", cfg["grid_tsv"],
        "--center-id", str(center_id),
        "--orientation-degrees", *[str(value) for value in cfg["orientations_degrees"]],
        "--head-mesh", cfg["head_mesh"],
        "--m2m-dir", cfg["m2m_dir"],
        "--coil", cfg["coil"],
        "--workbench", cfg.get(
            "workbench", "/usr/local/workbench/bin_linux64/wb_command"),
        "--skin", cfg["skin"],
        "--left-midthickness", surfaces["left_midthickness"],
        "--right-midthickness", surfaces["right_midthickness"],
        "--left-white", surfaces["left_white"],
        "--right-white", surfaces["right_white"],
        "--left-pial", surfaces["left_pial"],
        "--right-pial", surfaces["right_pial"],
        "--left-roi", surfaces["left_roi"],
        "--right-roi", surfaces["right_roi"],
        "--distance-mm", str(cfg["distance_mm"]),
        "--didt-aperus", str(cfg["didt_aperus"]),
        "--output", str(output),
    ]
    if resume:
        command.append("--resume")
    return command


def validate_reused_center(cfg, center_id, benchmark):
    from simulated_pose_geometry import sha256
    geometry = benchmark.get('geometry_validation', {})
    if (geometry.get('status') != 'validated' or
            geometry.get('surface_scope') != 'exterior_skin' or
            geometry.get('scalp_surface_sha256') != sha256(cfg['skin'])):
        raise ValueError('Cached simulation lacks matching exterior-skin validation; '
                         'do not reuse it without explicit geometry revalidation')
    row = read_grid(Path(cfg['grid_tsv']))[center_id - 1]
    if (int(row['scalp_vertex']) != benchmark['scalp_vertex_one_based'] or
            not np.array_equal([float(row[key]) for key in 'xyz'], benchmark['center_xyz']) or
            benchmark['orientations_degrees'] != cfg['orientations_degrees'] or
            benchmark['distance_mm'] != cfg['distance_mm'] or
            benchmark['didt_aperus'] != cfg['didt_aperus']):
        raise ValueError('Cached simulation does not match the requested pose batch')
    for key in ('head_mesh', 'coil', 'skin'):
        if Path(benchmark['inputs'][key]).resolve() != Path(cfg[key]).resolve():
            raise ValueError(f'Cached simulation uses a different {key}')


def run_center(cfg, center_id, root, resume):
    center_dir = root / f"Center_{center_id:05d}"
    benchmark = center_dir / "Benchmark.json"
    dense = center_dir / "magnE.dtseries.nii"
    if resume and benchmark.is_file() and dense.is_file():
        validate_reused_center(cfg, center_id, json.loads(benchmark.read_text()))
        return {"center_id": center_id, "status": "reused", "returncode": 0}
    if center_dir.exists() and not resume:
        raise RuntimeError(
            f"Center {center_id} has incomplete output; move it aside before resuming: "
            f"{center_dir}")
    log = root / f"Center_{center_id:05d}.log"
    started = time.perf_counter()
    with log.open("w") as handle:
        result = subprocess.run(center_command(
            cfg, center_id, center_dir, resume=resume),
                                stdout=handle, stderr=subprocess.STDOUT, text=True)
    elapsed = time.perf_counter() - started
    if result.returncode or not benchmark.is_file() or not dense.is_file():
        raise RuntimeError(
            f"Center {center_id} failed with status {result.returncode}; see {log}")
    raw_simulation_bytes_pruned = 0
    pruning_wall_seconds = 0.0
    keep_raw = bool(cfg.get("retention", {}).get(
        "keep_raw_simulation_workdir", True))
    simulation_dir = center_dir / "Simulation"
    if not keep_raw and simulation_dir.is_dir():
        raw_simulation_bytes_pruned = directory_bytes(simulation_dir)
        cleanup_started = time.perf_counter()
        shutil.rmtree(simulation_dir)
        pruning_wall_seconds = time.perf_counter() - cleanup_started
    return {"center_id": center_id, "status": "completed", "returncode": 0,
            "wall_seconds": elapsed, "log": str(log),
            "raw_simulation_bytes_pruned": raw_simulation_bytes_pruned,
            "pruning_wall_seconds": pruning_wall_seconds}


def score_outputs(cfg, grid_rows, root, coil_constraint=None):
    coil_constraint = coil_constraint or CoilCenterConstraint(cfg)
    enforce_coil_center = (coil_constraint.enabled and
                          cfg.get("strategy") != "robustness_perturbation_validation")
    target_image = nib.load(cfg["target_map"])
    target = np.asarray(target_image.get_fdata(), dtype=float).reshape(-1)
    area = np.asarray(nib.load(cfg["vertex_area"]).get_fdata(), dtype=float).reshape(-1)
    if target.shape != area.shape:
        raise ValueError("Target and vertex-area CIFTI dimensions differ")
    if not np.all(np.isfinite(target)):
        raise ValueError("Target map contains nonfinite values")
    if not np.all(np.isfinite(area) & (area > 0)):
        raise ValueError("Vertex-area map contains nonpositive or nonfinite values")
    thresholds = np.asarray(cfg["hotspot_percentiles"], dtype=float)
    orientations = cfg["orientations_degrees"]
    binary_target = None
    target_label = None
    resolved_network_name = cfg["network"]
    if cfg.get("network_label_map"):
        binary_target, target_label, resolved_network_name = binary_target_from_label_map(
            cfg["network_label_map"], target_image, cfg["network"])
    scoring_cfg = cfg.get("scoring", {})
    full_target = target.copy()
    full_binary_target = (None if binary_target is None else binary_target.copy())
    constrain_to_anatomy = bool(
        scoring_cfg.get("constrain_target_to_anatomical_mask", False))
    scoring_mask_path = scoring_cfg.get("anatomical_mask")
    mask_values = None
    if constrain_to_anatomy:
        if not scoring_mask_path:
            raise ValueError(
                "scoring.anatomical_mask is required when "
                "scoring.constrain_target_to_anatomical_mask is true")
        require_file(scoring_mask_path, "scoring anatomical mask")
        mask_values = np.asarray(
            nib.load(scoring_mask_path).get_fdata(), dtype=float).reshape(-1)
    component_values = None
    if scoring_cfg.get("restrict_target_to_design_components", False):
        component_path = scoring_cfg.get("component_labels")
        if not component_path:
            raise ValueError(
                "scoring.component_labels is required when retained-component "
                "scoring is enabled")
        require_file(component_path, "scoring component labels")
        component_values = np.asarray(
            nib.load(component_path).get_fdata(), dtype=float).reshape(-1)
    target, binary_target, scoring_mask, target_scope = apply_scoring_domain(
        target, binary_target, scoring_cfg, mask_values, component_values)
    minimum_peak_ratio = float(
        scoring_cfg.get("minimum_target_to_global_peak_ratio", 0.0))
    if not 0.0 <= minimum_peak_ratio <= 1.0:
        raise ValueError("minimum target/global peak ratio must be in [0, 1]")
    enforce_peak_ratio = bool(
        scoring_cfg.get("enforce_target_peak_ratio_for_selection", False))
    report_full_network = bool(
        scoring_cfg.get("report_full_network_scores", True))
    rows = []
    for grid_row in grid_rows:
        center_id = int(grid_row["center_id"])
        dense = root / f"Center_{center_id:05d}" / "magnE.dtseries.nii"
        field = np.asarray(nib.load(str(dense)).get_fdata(), dtype=float)
        if field.shape != (len(orientations), len(target)):
            raise ValueError(f"Unexpected E-field dimensions for center {center_id}: {field.shape}")
        for orientation_index, orientation in enumerate(orientations):
            coil_check = coil_constraint.evaluate_saved(root, center_id, orientation)
            scores = []
            binary_scores = []
            full_weighted_scores = []
            full_binary_scores = []
            ratio, target_maximum, global_maximum = target_to_global_peak_ratio(
                field[orientation_index], target)
            passes_peak_ratio = ratio + 1e-12 >= minimum_peak_ratio
            cutoffs = matlab_prctile(field[orientation_index], thresholds)
            for percentile, cutoff in zip(thresholds, cutoffs):
                hotspot = field[orientation_index] > cutoff
                denominator = area[hotspot].sum()
                scores.append(0.0 if denominator == 0 else
                              float(np.sum(area[hotspot] * target[hotspot]) / denominator))
                if binary_target is not None:
                    binary_scores.append(0.0 if denominator == 0 else float(
                        np.sum(area[hotspot] * binary_target[hotspot]) / denominator))
                if report_full_network:
                    full_weighted_scores.append(0.0 if denominator == 0 else float(
                        np.sum(area[hotspot] * full_target[hotspot]) / denominator))
                    if full_binary_target is not None:
                        full_binary_scores.append(0.0 if denominator == 0 else float(
                            np.sum(area[hotspot] * full_binary_target[hotspot]) /
                            denominator))
            rows.append({
                "center_id": center_id,
                "scalp_vertex": grid_row["scalp_vertex"],
                "x": grid_row["x"], "y": grid_row["y"], "z": grid_row["z"],
                "orientation_degrees": orientation,
                "mean_on_target_score": float(np.mean(scores)),
                "minimum_threshold_score": min(scores),
                "maximum_threshold_score": max(scores),
                "mean_binary_on_target_score": (
                    float(np.mean(binary_scores)) if binary_scores else np.nan),
                "mean_full_network_weighted_score": (
                    float(np.mean(full_weighted_scores))
                    if full_weighted_scores else np.nan),
                "mean_full_network_binary_score": (
                    float(np.mean(full_binary_scores))
                    if full_binary_scores else np.nan),
                "binary_score_at_99_5_percent": (
                    binary_scores[int(np.argmin(np.abs(thresholds - 99.5)))]
                    if binary_scores else np.nan),
                "target_scope": target_scope,
                "target_to_global_peak_ratio": ratio,
                "target_peak_efield_v_per_m": target_maximum,
                "global_peak_efield_v_per_m": global_maximum,
                "minimum_target_to_global_peak_ratio": minimum_peak_ratio,
                "passes_target_peak_ratio": passes_peak_ratio,
                "eligible_for_nominal_selection": (
                    (passes_peak_ratio or not enforce_peak_ratio) and
                    (coil_check["passes_coil_center_constraint"] or not enforce_coil_center)),
                **coil_check,
                "coil_center_constraint_enforced_for_selection": enforce_coil_center,
                "resolved_network_name": resolved_network_name,
                "binary_network_label": target_label,
                "scoring_anatomical_mask": (
                    str(Path(scoring_mask_path).resolve())
                    if constrain_to_anatomy else ""),
                "scoring_anatomical_mask_vertices": int(scoring_mask.sum()),
                "scoring_target_vertices": int(np.count_nonzero(target)),
                "weights_restricted_to_binary_support": bool(
                    scoring_cfg.get("restrict_weights_to_binary_support", False)),
                "design_component_ids": ",".join(
                    str(int(value)) for value in
                    scoring_cfg.get("design_component_ids", [])),
            })
    rows.sort(key=lambda row: (
        -int(row["eligible_for_nominal_selection"]),
        -row["mean_on_target_score"], row["center_id"],
        row["orientation_degrees"]))
    with (root / "CoarsePoseScores.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (root / "CoilCenterConstraint.json").write_text(json.dumps({
        **coil_constraint.provenance,
        "enforced_for_selection": enforce_coil_center,
        "poses_checked": len(rows) if coil_constraint.enabled else 0,
        "poses_passing_geometry": sum(bool(row["passes_coil_center_constraint"]) for row in rows),
        "poses_eligible_for_selection": sum(bool(row["eligible_for_nominal_selection"]) for row in rows),
    }, indent=2) + "\n")
    if not any(row["eligible_for_nominal_selection"] for row in rows):
        raise RuntimeError(
            "No simulated pose passes all enabled selection constraints: "
            f"coil center over anatomical zone={enforce_coil_center}, "
            f"target/global peak ratio >= {minimum_peak_ratio:g} enforced={enforce_peak_ratio}. "
            "See CoarsePoseScores.tsv and CoilCenterConstraint.json; no winner selected.")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    config_path = require_file(args.config, "configuration")
    cfg = json.loads(config_path.read_text())
    cfg["coil_center_constraint"] = resolve_coil_center_constraint(cfg)
    coil_constraint = CoilCenterConstraint(cfg)
    required = {
        "simnibs_python": "SimNIBS Python", "grid_tsv": "search grid",
        "head_mesh": "head mesh", "coil": "coil", "skin": "skin surface",
        "target_map": "target map", "vertex_area": "vertex area"}
    for field, label in required.items():
        require_file(cfg[field], label)
    for field, value in cfg["surfaces"].items():
        require_file(value, field.replace("_", " "))
    grid_rows = read_grid(Path(cfg["grid_tsv"]))
    if cfg.get("strategy") == "target_centroid_local_coarse":
        center_constraint = validate_coarse_center_constraint(cfg, grid_rows)
    elif cfg.get("strategy") == "target_projection_coverage_default":
        center_constraint = validate_projection_coverage_policy(cfg, grid_rows)
    else:
        center_constraint = {
            "verified": False,
            "not_applicable_reason": (
                "coarse centroid-radius constraint applies only to the "
                "target_centroid_local_coarse stage"),
        }
    root = Path(cfg["output_root"]).resolve()
    if root.exists() and not args.resume:
        raise FileExistsError(f"Refusing to overwrite simulation results: {root}")
    if (root / "CoarseRunSummary.json").is_file():
        validate_reused_constraint(
            cfg, root, check_winner=cfg.get("strategy") != "robustness_perturbation_validation")
    root.mkdir(parents=True, exist_ok=True)
    (root / "ResolvedConfig.json").write_text(json.dumps(cfg, indent=2))

    started = time.perf_counter()
    status_rows = []
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_center, cfg, int(row["center_id"]), root,
                                   args.resume): int(row["center_id"])
                   for row in grid_rows}
        for future in concurrent.futures.as_completed(futures):
            center_id = futures[future]
            try:
                result = future.result()
                status_rows.append(result)
                print(f"[search] center {center_id:05d} {result['status']}", flush=True)
            except Exception as exc:
                failures.append({"center_id": center_id, "error": str(exc)})
                print(f"[search] center {center_id:05d} FAILED: {exc}", flush=True)
    current_attempt_wall_seconds = time.perf_counter() - started
    recovery_path = root / "RecoveryMetadata.json"
    recovery = (json.loads(recovery_path.read_text())
                if recovery_path.is_file() else {})
    prior_wall_seconds = float(recovery.get("prior_failed_attempt_wall_seconds", 0.0))
    validated_path = root / 'PriorValidatedWork.json'
    validated_work = json.loads(validated_path.read_text()) if validated_path.is_file() else {}
    validated_seconds = float(validated_work.get('wall_seconds', 0.0))
    wall_seconds = current_attempt_wall_seconds + prior_wall_seconds + validated_seconds
    failures_path = root / "Failures.json"
    if failures:
        failures_path.write_text(json.dumps(failures, indent=2))
        raise RuntimeError(f"{len(failures)} center(s) failed; see {root / 'Failures.json'}")
    if failures_path.is_file():
        failures_path.unlink()

    scores = score_outputs(cfg, grid_rows, root, coil_constraint)
    best = dict(scores[0])
    target_scope = scores[0]["target_scope"]
    if bool(cfg.get("scoring", {}).get(
            "enforce_target_peak_ratio_for_selection", False)):
        best["selection_rule"] = (
            f"among poses passing target/global peak ratio >= "
            f"{best['minimum_target_to_global_peak_ratio']:g}, maximize mean "
            f"target-only score ({target_scope}) over configured hotspot "
            "percentiles; ties: lower center ID, then lower orientation")
    else:
        best["selection_rule"] = (
            f"maximize mean target-only score ({target_scope}) over configured "
            "hotspot percentiles without discarding peak-ratio failures; ties: "
            "lower center ID, then lower orientation")
    if best["coil_center_constraint_enforced_for_selection"]:
        best["selection_rule"] = (
            "require first inward coil-axis pial intersection inside the anatomical zone; " +
            best["selection_rule"])
    (root / "BestCoarsePose.json").write_text(json.dumps(best, indent=2))
    selection_rows = [row for row in scores
                      if row["eligible_for_nominal_selection"]]
    best_binary = max(selection_rows, key=lambda row: (
        row["mean_binary_on_target_score"], -row["center_id"],
        -float(row["orientation_degrees"])))
    best_binary = dict(best_binary)
    best_binary["selection_rule"] = (
        f"maximum mean binary network occupancy ({target_scope}) over configured "
        "hotspot percentiles; ties: lower center ID, then lower orientation")
    if best_binary["coil_center_constraint_enforced_for_selection"]:
        best_binary["selection_rule"] = (
            "require first inward coil-axis pial intersection inside the anatomical zone; " +
            best_binary["selection_rule"])
    (root / "BestBinaryPose.json").write_text(json.dumps(best_binary, indent=2))
    summary = {
        "subject": cfg["subject"], "network": cfg["network"],
        "map_mode": cfg["map_mode"], "strategy": cfg["strategy"],
        "centers": len(grid_rows), "orientations_per_center": len(cfg["orientations_degrees"]),
        "poses": len(grid_rows) * len(cfg["orientations_degrees"]),
        "workers": args.workers, "wall_seconds": wall_seconds,
        "current_attempt_wall_seconds": current_attempt_wall_seconds,
        "prior_failed_attempt_wall_seconds": prior_wall_seconds,
        "prior_validated_simulation_wall_seconds": validated_seconds,
        "reused_validated_simulations": bool(validated_work),
        "resumed_after_partial_failure": bool(recovery),
        "wall_hours": wall_seconds / 3600.0, "output_bytes": directory_bytes(root),
        "raw_simulation_workdirs_retained": bool(
            cfg.get("retention", {}).get("keep_raw_simulation_workdir", True)),
        "raw_simulation_bytes_pruned": sum(
            int(row.get("raw_simulation_bytes_pruned", 0))
            for row in status_rows),
        "pruning_wall_seconds": sum(
            float(row.get("pruning_wall_seconds", 0.0))
            for row in status_rows),
        "best_center_id": best["center_id"],
        "best_orientation_degrees": best["orientation_degrees"],
        "best_mean_on_target_score": best["mean_on_target_score"],
        "best_target_to_global_peak_ratio": best[
            "target_to_global_peak_ratio"],
        "best_binary_center_id": best_binary["center_id"],
        "best_binary_orientation_degrees": best_binary["orientation_degrees"],
        "best_mean_binary_on_target_score": best_binary["mean_binary_on_target_score"],
        "target_scope": target_scope,
        "resolved_network_name": scores[0]["resolved_network_name"],
        "binary_network_label": scores[0]["binary_network_label"],
        "scoring_constrained_to_anatomical_mask": bool(
            cfg.get("scoring", {}).get(
                "constrain_target_to_anatomical_mask", False)),
        "scoring_anatomical_mask": scores[0]["scoring_anatomical_mask"],
        "weights_restricted_to_binary_support": scores[0][
            "weights_restricted_to_binary_support"],
        "scoring_target_vertices": scores[0]["scoring_target_vertices"],
        "scoring_design_component_ids": scores[0]["design_component_ids"],
        "minimum_target_to_global_peak_ratio": scores[0][
            "minimum_target_to_global_peak_ratio"],
        "target_peak_ratio_enforced_for_selection": bool(
            cfg.get("scoring", {}).get(
                "enforce_target_peak_ratio_for_selection", False)),
        "full_network_scores_reported": bool(
            cfg.get("scoring", {}).get("report_full_network_scores", True)),
        "target_score_only": True, "avoidance_used": False,
        "tolerability_used": False,
        "surface_space": "subject transformed fs_LR 32k",
        "volume_to_surface_method": (
            "historical Workbench ribbon-constrained with "
            "-interpolate ENCLOSING_VOXEL"),
        "coarse_center_constraint": center_constraint,
        "coil_center_constraint": coil_constraint.provenance,
        "coil_center_constraint_enforced_for_selection": best[
            "coil_center_constraint_enforced_for_selection"],
    }
    (root / "CoarseRunSummary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
