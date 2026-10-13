#!/usr/bin/env python3
"""Generate deterministic coarse grids that cover projected target territory."""
import argparse
import csv
import json
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy.io import savemat
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree


DEFAULT_POLICY_PATH = Path(__file__).with_name("default_search_policy.json")
DEFAULT_POLICY = json.loads(DEFAULT_POLICY_PATH.read_text())


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
    return image, points, triangles


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


def cortical_vector(path, structure, vertex_count):
    image = nib.load(str(path))
    data = np.asarray(image.get_fdata(), dtype=float)
    if data.shape[0] != 1:
        raise ValueError(f"Expected one CIFTI map in {path}, found {data.shape[0]}")
    output = np.full(vertex_count, np.nan, dtype=float)
    matches = 0
    for name, data_slice, model in image.header.get_axis(1).iter_structures():
        if name == structure:
            output[np.asarray(model.vertex, dtype=np.int64)] = data[0, data_slice]
            matches += 1
    if matches != 1:
        raise ValueError(f"Expected one {structure} brain model in {path}")
    return output


def aggregate_projections(projected_vertices, area_weights, weighted_weights):
    unique, inverse = np.unique(projected_vertices, return_inverse=True)
    area = np.bincount(inverse, weights=area_weights, minlength=len(unique))
    weighted = np.bincount(inverse, weights=weighted_weights, minlength=len(unique))
    counts = np.bincount(inverse, minlength=len(unique)).astype(int)
    return unique.astype(np.int64), area, weighted, counts


def coverage_semantics(map_mode):
    if map_mode == "weighted":
        return {
            "coverage_measure": "probability_weighted_surface_area",
            "density_definition": (
                "fraction of projected probability-weighted target surface area "
                "within coverage_radius_mm scalp-geodesic distance of at least "
                "one search center"),
        }
    if map_mode == "binary":
        return {
            "coverage_measure": "binary_target_surface_area",
            "density_definition": (
                "fraction of projected binary target surface area within "
                "coverage_radius_mm scalp-geodesic distance of at least one "
                "search center"),
        }
    raise ValueError("target map mode must be 'weighted' or 'binary'")


def select_design_clusters(cluster_rows, minimum_vertices, minimum_area_mm2,
                           minimum_relative_weighted_area_fraction=0.0):
    if not 0.0 <= minimum_relative_weighted_area_fraction <= 1.0:
        raise ValueError(
            "minimum relative weighted area fraction must be in [0, 1]")
    maximum_weighted_area = max(
        float(row.get("weighted_area_mm2", row["surface_area_mm2"]))
        for row in cluster_rows)
    relative_floor = (
        minimum_relative_weighted_area_fraction * maximum_weighted_area)
    selected = [
        row["cluster_id"] for row in cluster_rows
        if row["vertex_count"] >= minimum_vertices
        and row["surface_area_mm2"] >= minimum_area_mm2
        and float(row.get("weighted_area_mm2", row["surface_area_mm2"])) + 1e-12
        >= relative_floor
    ]
    if not selected:
        largest = max(cluster_rows, key=lambda row: (
            float(row.get("weighted_area_mm2", row["surface_area_mm2"])),
            row["surface_area_mm2"], row["vertex_count"], -row["cluster_id"]))
        selected = [largest["cluster_id"]]
    return selected


def weighted_percentile(values, weights, percentile):
    order = np.lexsort((np.arange(len(values)), values))
    cumulative = np.cumsum(weights[order])
    threshold = percentile / 100.0 * cumulative[-1]
    return float(values[order[min(np.searchsorted(cumulative, threshold), len(order) - 1)]])


def coverage_objective(distances, weights):
    return (float(np.max(distances)),
            weighted_percentile(distances, weights, 95.0),
            float(np.average(distances, weights=weights)))


def target_coverage_fraction(distances, weights, radius_mm):
    if radius_mm < 0:
        raise ValueError("coverage radius must be nonnegative")
    total = float(np.sum(weights))
    if total <= 0:
        raise ValueError("coverage weights must have positive total")
    return float(np.sum(weights[np.asarray(distances) <= radius_mm + 1e-8]) / total)


def objective_better(candidate, current, candidate_vertices, current_vertices):
    tolerance = 1e-8
    for new, old in zip(candidate, current):
        if new < old - tolerance:
            return True
        if new > old + tolerance:
            return False
    return tuple(sorted(candidate_vertices)) < tuple(sorted(current_vertices))


def deterministic_kcenter(graph, scalp_points, demand_vertices, demand_weights,
                          target_centroid, center_count, refinement_passes=8,
                          mandatory_vertices=None):
    if center_count < 1 or center_count > len(demand_vertices):
        raise ValueError("center_count must be between 1 and the unique demand count")
    demand_xyz = scalp_points[demand_vertices]
    if mandatory_vertices is None:
        seed_distance = np.linalg.norm(demand_xyz - target_centroid, axis=1)
        minimum = float(np.min(seed_distance))
        tied = np.flatnonzero(np.abs(seed_distance - minimum) <= 1e-10)
        seed_index = min(tied, key=lambda index: (-demand_weights[index],
                                                   demand_vertices[index]))
        selected = [int(demand_vertices[seed_index])]
    else:
        selected = list(dict.fromkeys(int(value) for value in mandatory_vertices))
        if not selected:
            raise ValueError("At least one mandatory centroid anchor is required")
        if any(value not in set(demand_vertices.tolist()) for value in selected):
            raise ValueError("Every mandatory centroid anchor must be a demand vertex")
    if len(selected) > center_count:
        raise ValueError("Mandatory centroid anchors exceed requested center count")
    locked_count = len(selected)
    distance_rows = [dijkstra(graph, indices=vertex, directed=False)
                     for vertex in selected]
    nearest = np.min(np.asarray(distance_rows), axis=0)
    while len(selected) < center_count:
        values = nearest[demand_vertices]
        farthest = float(np.max(values))
        tolerance = max(1.0, farthest) * np.finfo(float).eps * 16
        tied = np.flatnonzero(np.abs(values - farthest) <= tolerance)
        chosen_index = min(tied, key=lambda index: (-demand_weights[index],
                                                     demand_vertices[index]))
        vertex = int(demand_vertices[chosen_index])
        if vertex in selected:
            raise RuntimeError("Deterministic k-center selected a duplicate vertex")
        selected.append(vertex)
        row = dijkstra(graph, indices=vertex, directed=False)
        distance_rows.append(row)
        nearest = np.minimum(nearest, row)

    swaps = 0
    for _ in range(refinement_passes):
        changed = False
        matrix = np.asarray(distance_rows)
        assignments = np.argmin(matrix[:, demand_vertices], axis=0)
        for center_index in range(len(selected)):
            if center_index < locked_count:
                continue
            assigned = np.flatnonzero(assignments == center_index)
            if not len(assigned):
                continue
            centroid = np.average(demand_xyz[assigned], axis=0,
                                  weights=demand_weights[assigned])
            spatial = np.linalg.norm(demand_xyz[assigned] - centroid, axis=1)
            minimum = float(np.min(spatial))
            tied_local = assigned[np.abs(spatial - minimum) <= 1e-10]
            candidate_index = min(
                tied_local,
                key=lambda index: (-demand_weights[index], demand_vertices[index]))
            candidate_vertex = int(demand_vertices[candidate_index])
            if candidate_vertex == selected[center_index] or candidate_vertex in selected:
                continue
            candidate_row = dijkstra(graph, indices=candidate_vertex, directed=False)
            other = np.min(np.delete(matrix, center_index, axis=0), axis=0)
            proposed_nearest = np.minimum(other, candidate_row)
            current_objective = coverage_objective(
                np.min(matrix[:, demand_vertices], axis=0), demand_weights)
            proposed_objective = coverage_objective(
                proposed_nearest[demand_vertices], demand_weights)
            proposed_vertices = selected.copy()
            proposed_vertices[center_index] = candidate_vertex
            if objective_better(proposed_objective, current_objective,
                                proposed_vertices, selected):
                selected = proposed_vertices
                distance_rows[center_index] = candidate_row
                matrix[center_index] = candidate_row
                assignments = np.argmin(matrix[:, demand_vertices], axis=0)
                swaps += 1
                changed = True
        if not changed:
            break
    matrix = np.asarray(distance_rows)
    nearest = np.min(matrix[:, demand_vertices], axis=0)
    nearest_center = np.argmin(matrix[:, demand_vertices], axis=0)
    return np.asarray(selected, dtype=np.int64), nearest, nearest_center, matrix, swaps


def center_count_for_coverage(graph, demand_vertices, demand_weights,
                              mandatory_vertices, coverage_fraction,
                              coverage_radius_mm, minimum_centers=1,
                              maximum_centers=40):
    if not 0 < coverage_fraction <= 1:
        raise ValueError("coverage fraction must be in (0, 1]")
    if minimum_centers < 1 or maximum_centers < minimum_centers:
        raise ValueError("invalid minimum/maximum center limits")
    selected = list(dict.fromkeys(int(value) for value in mandatory_vertices))
    if not selected:
        raise ValueError("At least one mandatory centroid anchor is required")
    if len(selected) > maximum_centers:
        raise ValueError("Mandatory centroid anchors exceed maximum centers")
    rows = [dijkstra(graph, indices=vertex, directed=False)
            for vertex in selected]
    nearest = np.min(np.asarray(rows), axis=0)
    demand_set = set(int(value) for value in demand_vertices)
    if any(value not in demand_set for value in selected):
        raise ValueError("Every mandatory centroid anchor must be a demand vertex")
    while (len(selected) < minimum_centers or
           target_coverage_fraction(nearest[demand_vertices], demand_weights,
                                    coverage_radius_mm) < coverage_fraction):
        if len(selected) >= maximum_centers or len(selected) >= len(demand_vertices):
            break
        values = nearest[demand_vertices]
        available = np.asarray([vertex not in selected for vertex in demand_vertices])
        farthest = float(np.max(values[available]))
        tolerance = max(1.0, farthest) * np.finfo(float).eps * 16
        tied = np.flatnonzero(available & (np.abs(values - farthest) <= tolerance))
        chosen_index = min(tied, key=lambda index: (-demand_weights[index],
                                                     demand_vertices[index]))
        vertex = int(demand_vertices[chosen_index])
        selected.append(vertex)
        row = dijkstra(graph, indices=vertex, directed=False)
        rows.append(row)
        nearest = np.minimum(nearest, row)
    achieved = target_coverage_fraction(nearest[demand_vertices], demand_weights,
                                        coverage_radius_mm)
    return len(selected), achieved


def spacing_metrics(distance_matrix, selected):
    if len(selected) < 2:
        return None, []
    pairwise = distance_matrix[:, selected]
    values = [float(pairwise[i, j]) for i in range(len(selected))
              for j in range(i + 1, len(selected))]
    return min(values), values


def read_grid_vertices(path):
    with Path(path).open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return np.asarray([int(row["scalp_vertex"]) - 1 for row in rows], dtype=np.int64)


def baseline_coverage(graph, vertices, demand_vertices, demand_weights):
    distance = dijkstra(graph, indices=vertices, directed=False, min_only=True)
    values = distance[demand_vertices]
    objective = coverage_objective(values, demand_weights)
    return {"centers": len(vertices), "maximum_distance_mm": objective[0],
            "weighted_p95_distance_mm": objective[1],
            "weighted_mean_distance_mm": objective[2]}


def write_plan(output, skin_image, scalp_points, selected, demand_vertices,
               demand_area, demand_weights, demand_counts, nearest,
               nearest_center, candidate_vertices, target_points,
               target_centroid, strategy, minimum_spacing, objective, swaps,
               network, source_cluster_id, mandatory_anchor_vertices,
               design_cluster_ids, excluded_cluster_rows,
               target_map_mode,
               requested_coverage_fraction=None, coverage_radius_mm=None,
               achieved_coverage_fraction=None):
    output.mkdir(parents=True)
    rows = []
    for center_id, vertex in enumerate(selected, 1):
        rows.append({
            "center_id": center_id, "scalp_vertex": int(vertex + 1),
            "x": scalp_points[vertex, 0], "y": scalp_points[vertex, 1],
            "z": scalp_points[vertex, 2], "strategy": strategy,
            "source_cluster_id": source_cluster_id,
            "target_map_mode": target_map_mode,
            "network_name": network,
        })
    with (output / "SearchGrid.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    metric = np.zeros(len(scalp_points), dtype=np.float32)
    metric[selected] = 1
    shape = nib.gifti.GiftiImage(darrays=[nib.gifti.GiftiDataArray(
        metric, intent="NIFTI_INTENT_SHAPE")])
    nib.save(shape, output / "SearchGrid.shape.gii")

    nearest_target_ids = cKDTree(target_points).query(scalp_points[selected], k=1)[1]
    nearest_target_xyz = target_points[np.asarray(nearest_target_ids, dtype=int)]
    grid = {
        "strategy": strategy,
        "scalp_vertices": (selected + 1)[:, None],
        "xyz": scalp_points[selected],
        "candidate_scalp_vertices": (candidate_vertices + 1)[:, None],
        "source_cluster_id": np.full((len(selected), 1), source_cluster_id),
        "nearest_target_xyz": nearest_target_xyz,
        "requested_spacing_mm": minimum_spacing,
        "radius_or_margin_mm": 5.0,
        "target_centroid_xyz": target_centroid,
    }
    savemat(output / "SearchGrid.mat", {"grid": grid})

    with (output / "ProjectedTargetCoverage.tsv").open("w", newline="") as handle:
        fields = ["projected_scalp_vertex", "x", "y", "z",
                  "cortical_vertex_count", "cortical_area_mm2",
                  "weighted_area_mm2", "nearest_center_id",
                  "nearest_center_distance_mm"]
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        for index, vertex in enumerate(demand_vertices):
            writer.writerow({
                "projected_scalp_vertex": int(vertex + 1),
                "x": scalp_points[vertex, 0], "y": scalp_points[vertex, 1],
                "z": scalp_points[vertex, 2],
                "cortical_vertex_count": int(demand_counts[index]),
                "cortical_area_mm2": demand_area[index],
                "weighted_area_mm2": demand_weights[index],
                "nearest_center_id": int(nearest_center[index] + 1),
                "nearest_center_distance_mm": nearest[index],
            })
    semantics = coverage_semantics(target_map_mode)
    summary = {
        "strategy": strategy, "network": network, "map_mode": target_map_mode,
        **semantics,
        "source_cluster_id": source_cluster_id,
        "centers": len(selected), "unique_projected_target_vertices": len(demand_vertices),
        "represented_cortical_vertices": int(np.sum(demand_counts)),
        "minimum_pairwise_center_spacing_mm": minimum_spacing,
        "maximum_target_projection_distance_mm": objective[0],
        "weighted_p95_target_projection_distance_mm": objective[1],
        "weighted_mean_target_projection_distance_mm": objective[2],
        "local_refinement_swaps": swaps,
        "mandatory_centroid_anchor_scalp_vertices": [
            int(value + 1) for value in mandatory_anchor_vertices],
        "design_cluster_ids": [int(value) for value in design_cluster_ids],
        "excluded_components": excluded_cluster_rows,
        "excluded_micro_components": excluded_cluster_rows,
        "requested_target_weighted_coverage_fraction": requested_coverage_fraction,
        "requested_target_coverage_fraction": requested_coverage_fraction,
        "coverage_radius_mm": coverage_radius_mm,
        "achieved_target_weighted_coverage_fraction": achieved_coverage_fraction,
        "achieved_target_coverage_fraction": achieved_coverage_fraction,
        "selection_rule": (
            "deterministic scalp-geodesic farthest-first k-center with locked "
            "area-times-target-weight centroid projection anchors for every "
            "design-eligible component, followed by deterministic "
            "lexicographic max/p95/weighted-mean coverage refinement"),
        "tie_breaks": "larger aggregated weighted area, then lower scalp vertex ID",
        "candidate_centers": "unique nearest-scalp projections of eligible target vertices",
        "visualization_margin_mm": 5.0,
        "randomness_used": False,
    }
    (output / "CoverageSummary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weighted-target", required=True)
    parser.add_argument(
        "--target-map-mode", choices=("weighted", "binary"), default="weighted",
        help=("Interpret --weighted-target as probabilistic weights or a binary "
              "target support map. The option name is retained for compatibility."))
    parser.add_argument("--eligible-support", required=True)
    parser.add_argument("--cluster-labels", required=True)
    parser.add_argument("--vertex-area", required=True)
    parser.add_argument("--left-pial", required=True)
    parser.add_argument("--skin", required=True)
    parser.add_argument("--baseline-plan", action="append", default=[],
                        help="NAME=SearchGrid.tsv")
    parser.add_argument("--center-counts", nargs="*", type=int, default=[])
    parser.add_argument("--coverage-fractions", nargs="*", type=float,
                        default=[DEFAULT_POLICY["target_weighted_coverage_fraction"]],
                        help="Target-weighted projected-territory coverage targets in (0,1]")
    parser.add_argument("--coverage-radius-mm", type=float,
                        default=DEFAULT_POLICY["coverage_radius_mm"])
    parser.add_argument("--minimum-centers", type=int,
                        default=DEFAULT_POLICY["minimum_centers"])
    parser.add_argument("--maximum-centers", type=int,
                        default=DEFAULT_POLICY["maximum_centers"])
    parser.add_argument("--candidate-margin-mm", type=float,
                        default=DEFAULT_POLICY["candidate_margin_mm"])
    parser.add_argument("--network", default="Somatomotor_Hand")
    parser.add_argument("--source-cluster-id", type=int, default=1)
    parser.add_argument("--minimum-design-cluster-vertices", type=int,
                        default=DEFAULT_POLICY["minimum_design_cluster_vertices"])
    parser.add_argument("--minimum-design-cluster-area-mm2", type=float,
                        default=DEFAULT_POLICY["minimum_design_cluster_area_mm2"])
    parser.add_argument(
        "--minimum-design-cluster-relative-weighted-area-fraction", type=float,
        default=DEFAULT_POLICY[
            "minimum_design_cluster_relative_weighted_area_fraction"])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite coverage comparison: {output}")
    if args.candidate_margin_mm < 0:
        raise ValueError("candidate margin must be nonnegative")
    output.mkdir(parents=True)

    skin_image, scalp_points, scalp_triangles = load_surface(args.skin)
    _, pial_points, _ = load_surface(args.left_pial)
    support = cortical_vector(args.eligible_support,
                              "CIFTI_STRUCTURE_CORTEX_LEFT", len(pial_points))
    cluster_labels = cortical_vector(args.cluster_labels,
                                     "CIFTI_STRUCTURE_CORTEX_LEFT", len(pial_points))
    weighted = cortical_vector(args.weighted_target,
                               "CIFTI_STRUCTURE_CORTEX_LEFT", len(pial_points))
    area = cortical_vector(args.vertex_area,
                           "CIFTI_STRUCTURE_CORTEX_LEFT", len(pial_points))
    valid = np.isfinite(support) & np.isfinite(weighted) & np.isfinite(area)
    full_target = valid & (support == 1)
    if not np.any(full_target):
        raise ValueError("No eligible target vertices remain")
    if np.any(area[full_target] <= 0) or np.any(weighted[full_target] < 0):
        raise ValueError("Eligible surface areas and weights must be nonnegative")
    cluster_rows = []
    for cluster_id in sorted(np.unique(cluster_labels[full_target]).astype(int)):
        component = full_target & (cluster_labels == cluster_id)
        cluster_rows.append({
            "cluster_id": int(cluster_id),
            "vertex_count": int(np.count_nonzero(component)),
            "surface_area_mm2": float(np.sum(area[component])),
            "weighted_area_mm2": float(np.sum(
                area[component] * weighted[component])),
        })
    design_cluster_ids = select_design_clusters(
        cluster_rows, args.minimum_design_cluster_vertices,
        args.minimum_design_cluster_area_mm2,
        args.minimum_design_cluster_relative_weighted_area_fraction)
    maximum_weighted_area = max(
        row["weighted_area_mm2"] for row in cluster_rows)
    for row in cluster_rows:
        row["relative_weighted_area_fraction"] = (
            row["weighted_area_mm2"] / maximum_weighted_area)
    excluded_cluster_rows = [row for row in cluster_rows
                             if row["cluster_id"] not in design_cluster_ids]
    with (output / "ComponentDesignStatus.tsv").open("w", newline="") as handle:
        fields = [
            "cluster_id", "vertex_count", "surface_area_mm2",
            "weighted_area_mm2", "relative_weighted_area_fraction",
            "design_status",
        ]
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        for row in cluster_rows:
            absolute_pass = (
                row["vertex_count"] >= args.minimum_design_cluster_vertices
                and row["surface_area_mm2"] >=
                args.minimum_design_cluster_area_mm2)
            relative_pass = (
                row["relative_weighted_area_fraction"] + 1e-12 >=
                args.minimum_design_cluster_relative_weighted_area_fraction)
            if row["cluster_id"] in design_cluster_ids:
                status = "included"
            elif not absolute_pass:
                status = "excluded_absolute_size"
            elif not relative_pass:
                status = "excluded_relative_weighted_area"
            else:
                status = "excluded"
            writer.writerow({**row, "design_status": status})
    target = full_target & np.isin(cluster_labels, design_cluster_ids)
    cortical_vertices = np.flatnonzero(target)
    target_points = pial_points[cortical_vertices]
    area_weights = area[cortical_vertices]
    weighted_weights = area_weights * weighted[cortical_vertices]
    if not np.any(weighted_weights > 0):
        raise ValueError("Eligible target has no positive weighted area")
    target_centroid = np.average(target_points, axis=0, weights=weighted_weights)
    projected = cKDTree(scalp_points).query(target_points, k=1)[1]
    demand_vertices, demand_area, demand_weights, demand_counts = \
        aggregate_projections(projected, area_weights, weighted_weights)
    graph = mesh_graph(scalp_points, scalp_triangles)
    mandatory_anchor_vertices = []
    for cluster_id in design_cluster_ids:
        component = target & (cluster_labels == cluster_id)
        component_weights = area[component] * weighted[component]
        centroid = np.average(pial_points[component], axis=0,
                              weights=component_weights)
        anchor = int(cKDTree(scalp_points).query(centroid, k=1)[1])
        if anchor not in set(demand_vertices.tolist()):
            anchor = int(demand_vertices[np.argmin(
                np.linalg.norm(scalp_points[demand_vertices] - centroid, axis=1))])
        mandatory_anchor_vertices.append(anchor)
    mandatory_anchor_vertices = np.asarray(
        list(dict.fromkeys(mandatory_anchor_vertices)), dtype=np.int64)
    distance_to_demand = dijkstra(graph, indices=demand_vertices, directed=False,
                                  min_only=True, limit=args.candidate_margin_mm)
    candidate_vertices = np.flatnonzero(
        distance_to_demand <= args.candidate_margin_mm + 1e-8)

    comparison = []
    for definition in args.baseline_plan:
        if "=" not in definition:
            raise ValueError("--baseline-plan must use NAME=SearchGrid.tsv")
        name, path = definition.split("=", 1)
        vertices = read_grid_vertices(path)
        metrics = baseline_coverage(graph, vertices, demand_vertices, demand_weights)
        comparison.append({"strategy": name, **metrics})

    summaries = []
    for count in sorted(set(args.center_counts)):
        selected, nearest, nearest_center, distance_matrix, swaps = \
            deterministic_kcenter(graph, scalp_points, demand_vertices,
                                  demand_weights, target_centroid, count,
                                  mandatory_vertices=mandatory_anchor_vertices)
        minimum_spacing, _ = spacing_metrics(distance_matrix, selected)
        objective = coverage_objective(nearest, demand_weights)
        strategy = f"target_projection_coverage_k{count}"
        summary = write_plan(
            output / strategy, skin_image, scalp_points, selected, demand_vertices,
            demand_area, demand_weights, demand_counts, nearest, nearest_center,
            candidate_vertices, target_points, target_centroid, strategy,
            minimum_spacing, objective, swaps, args.network, args.source_cluster_id,
            mandatory_anchor_vertices, design_cluster_ids, excluded_cluster_rows,
            args.target_map_mode)
        summaries.append(summary)
        comparison.append({
            "strategy": strategy, "centers": count,
            "maximum_distance_mm": objective[0],
            "weighted_p95_distance_mm": objective[1],
            "weighted_mean_distance_mm": objective[2],
        })

    for fraction in sorted(set(args.coverage_fractions)):
        upper_count, _ = center_count_for_coverage(
            graph, demand_vertices, demand_weights, mandatory_anchor_vertices,
            fraction, args.coverage_radius_mm, args.minimum_centers,
            args.maximum_centers)
        selected = nearest = nearest_center = distance_matrix = None
        swaps = achieved = None
        counts_to_try = list(range(
            max(args.minimum_centers, len(mandatory_anchor_vertices)),
            upper_count + 1))
        counts_to_try.extend(range(upper_count + 1, args.maximum_centers + 1))
        for count in counts_to_try:
            selected, nearest, nearest_center, distance_matrix, swaps = \
                deterministic_kcenter(
                    graph, scalp_points, demand_vertices, demand_weights,
                    target_centroid, count,
                    mandatory_vertices=mandatory_anchor_vertices)
            achieved = target_coverage_fraction(
                nearest, demand_weights, args.coverage_radius_mm)
            if achieved + 1e-12 >= fraction:
                break
        if achieved + 1e-12 < fraction:
            raise RuntimeError(
                f"Could not achieve coverage fraction {fraction} with "
                f"maximumCenters={args.maximum_centers}; achieved {achieved}")
        minimum_spacing, _ = spacing_metrics(distance_matrix, selected)
        objective = coverage_objective(nearest, demand_weights)
        fraction_label = f"{fraction:.3f}".rstrip("0").rstrip(".").replace(".", "p")
        strategy = f"target_projection_coverage_d{fraction_label}"
        summary = write_plan(
            output / strategy, skin_image, scalp_points, selected, demand_vertices,
            demand_area, demand_weights, demand_counts, nearest, nearest_center,
            candidate_vertices, target_points, target_centroid, strategy,
            minimum_spacing, objective, swaps, args.network, args.source_cluster_id,
            mandatory_anchor_vertices, design_cluster_ids, excluded_cluster_rows,
            args.target_map_mode,
            requested_coverage_fraction=fraction,
            coverage_radius_mm=args.coverage_radius_mm,
            achieved_coverage_fraction=achieved)
        summaries.append(summary)
        comparison.append({
            "strategy": strategy, "centers": count,
            "maximum_distance_mm": objective[0],
            "weighted_p95_distance_mm": objective[1],
            "weighted_mean_distance_mm": objective[2],
        })

    fields = ["strategy", "centers", "maximum_distance_mm",
              "weighted_p95_distance_mm", "weighted_mean_distance_mm"]
    with (output / "CoverageComparison.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        writer.writerows(comparison)
    provenance = {
        "default_policy_file": str(DEFAULT_POLICY_PATH.resolve()),
        "default_policy": DEFAULT_POLICY,
        "target_map_mode": args.target_map_mode,
        "coverage_measure": coverage_semantics(args.target_map_mode)[
            "coverage_measure"],
        "weighted_target": str(Path(args.weighted_target).resolve()),
        "eligible_support": str(Path(args.eligible_support).resolve()),
        "cluster_labels": str(Path(args.cluster_labels).resolve()),
        "vertex_area": str(Path(args.vertex_area).resolve()),
        "left_pial": str(Path(args.left_pial).resolve()),
        "skin": str(Path(args.skin).resolve()),
        "candidate_margin_mm": args.candidate_margin_mm,
        "center_counts": sorted(set(args.center_counts)),
        "coverage_fractions": sorted(set(args.coverage_fractions)),
        "coverage_radius_mm": args.coverage_radius_mm,
        "minimum_centers": args.minimum_centers,
        "maximum_centers": args.maximum_centers,
        "minimum_design_cluster_vertices": args.minimum_design_cluster_vertices,
        "minimum_design_cluster_area_mm2": args.minimum_design_cluster_area_mm2,
        "minimum_design_cluster_relative_weighted_area_fraction":
            args.minimum_design_cluster_relative_weighted_area_fraction,
        "baseline_plans": args.baseline_plan,
        "outputs": summaries,
    }
    (output / "Provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"output": str(output), "comparison": comparison}, indent=2))


if __name__ == "__main__":
    main()
