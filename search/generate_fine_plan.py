#!/usr/bin/env python3
"""Generate a deterministic local scalp grid around the winning coarse pose."""
import argparse
import csv
import json
import re
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy.io import savemat
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from coil_center_constraint import validate_reused_constraint


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


def sample(graph, candidates, anchor, spacing):
    selected = [anchor]
    minimum = dijkstra(graph, indices=anchor, directed=False)
    while True:
        remaining = np.setdiff1d(candidates, np.asarray(selected), assume_unique=True)
        if not len(remaining):
            break
        values = minimum[remaining]
        farthest = np.max(values)
        if farthest < spacing:
            break
        tolerance = max(1.0, abs(farthest)) * np.finfo(float).eps * 8
        tied = remaining[np.abs(values - farthest) <= tolerance]
        vertex = int(np.min(tied))
        selected.append(vertex)
        minimum = np.minimum(minimum, dijkstra(graph, indices=vertex, directed=False))
    return np.asarray(selected, dtype=np.int64)


def output_slug(network):
    slug = re.sub(r"[^a-z0-9]+", "_", network.lower()).strip("_")
    if not slug:
        raise ValueError("Network name cannot produce an empty output slug")
    return slug


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coarse-config", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--coarse-result")
    source.add_argument("--best-pose")
    parser.add_argument("--radius-mm", type=float, default=12.0)
    parser.add_argument("--spacing-mm", type=float, default=5.0)
    parser.add_argument("--relative-orientations", type=float, nargs="+",
                        default=[-20, -10, 0, 10, 20])
    parser.add_argument("--output", required=True)
    parser.add_argument("--simulation-output",
                        help="Explicit output directory for fine simulations")
    args = parser.parse_args()
    if args.radius_mm <= 0 or args.spacing_mm <= 0:
        raise ValueError("Radius and spacing must be positive")
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite fine plan: {output}")
    output.mkdir(parents=True)

    cfg = json.loads(Path(args.coarse_config).read_text())
    if args.best_pose:
        best_path = Path(args.best_pose).resolve()
        best = json.loads(best_path.read_text())
        parent_coarse_result = str(best_path.parent)
    else:
        parent = Path(args.coarse_result).resolve()
        best_path = parent / "BestCoarsePose.json"
        best = json.loads(best_path.read_text())
        parent_coarse_result = str(parent)
    validate_reused_constraint(cfg, parent_coarse_result, best_path)
    surface_image, points, triangles = load_surface(cfg["skin"])
    anchor = int(best["scalp_vertex"]) - 1
    if anchor < 0 or anchor >= len(points):
        raise ValueError("Winning scalp vertex is outside the skin surface")
    graph = mesh_graph(points, triangles)
    distance_from_anchor = dijkstra(graph, indices=anchor, directed=False,
                                    limit=args.radius_mm)
    candidates = np.flatnonzero(distance_from_anchor <= args.radius_mm)
    selected = sample(graph, candidates, anchor, args.spacing_mm)

    pair_distances = []
    violations = []
    for i, vertex in enumerate(selected):
        distances = dijkstra(graph, indices=vertex, directed=False)
        for j in range(i + 1, len(selected)):
            distance = float(distances[selected[j]])
            if np.isfinite(distance):
                pair_distances.append(distance)
                if distance + 1e-6 < args.spacing_mm:
                    violations.append((i + 1, j + 1, distance))
    if violations:
        raise RuntimeError(f"Fine grid contains spacing violations: {violations}")

    rows = []
    for center_id, vertex in enumerate(selected, 1):
        rows.append({
            "center_id": center_id, "scalp_vertex": int(vertex + 1),
            "x": points[vertex, 0], "y": points[vertex, 1], "z": points[vertex, 2],
            "strategy": "fine_local_after_coarse_winner",
            "source_cluster_id": "NaN", "target_map_mode": cfg["map_mode"],
            "network_name": cfg["network"], "parent_coarse_center_id": best["center_id"],
            "parent_coarse_orientation_degrees": best["orientation_degrees"],
            "distance_from_parent_mm": distance_from_anchor[vertex],
        })
    grid_tsv = output / "SearchGrid.tsv"
    with grid_tsv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    savemat(output / "SearchGrid.mat", {
        "scalp_vertices_one_based": selected[:, None] + 1,
        "xyz": points[selected], "parent_coarse_center_id": best["center_id"],
        "parent_coarse_orientation_degrees": best["orientation_degrees"],
        "radius_mm": args.radius_mm, "spacing_mm": args.spacing_mm})
    metric = np.zeros(len(points), dtype=np.float32)
    metric[selected] = 1
    shape = nib.gifti.GiftiImage(darrays=[nib.gifti.GiftiDataArray(
        metric, intent="NIFTI_INTENT_SHAPE")])
    nib.save(shape, output / "SearchGrid.shape.gii")

    orientations = sorted({float((best["orientation_degrees"] + value) % 180)
                           for value in args.relative_orientations})
    fine_cfg = dict(cfg)
    fine_cfg["strategy"] = "fine_local_after_coarse_winner"
    fine_cfg["grid_tsv"] = str(grid_tsv)
    fine_cfg["orientations_degrees"] = orientations
    fine_cfg["output_root"] = str(
        Path(args.simulation_output).resolve() if args.simulation_output else
        output.parent / f"{output_slug(cfg['network'])}_{cfg['map_mode']}_fine")
    fine_cfg["parent_coarse_result"] = parent_coarse_result
    fine_cfg["parent_coarse_pose"] = str(best_path)
    fine_cfg["fine_parameters"] = {
        "radius_mm": args.radius_mm, "spacing_mm": args.spacing_mm,
        "relative_orientations_degrees": args.relative_orientations}
    (output / "FineRunConfig.json").write_text(json.dumps(fine_cfg, indent=2))
    summary = {
        "centers": len(selected), "orientations": orientations,
        "poses": len(selected) * len(orientations),
        "parent_coarse_center_id": best["center_id"],
        "parent_coarse_orientation_degrees": best["orientation_degrees"],
        "parent_coarse_pose": str(best_path),
        "radius_mm": args.radius_mm, "spacing_mm": args.spacing_mm,
        "minimum_pairwise_spacing_mm": min(pair_distances) if pair_distances else None,
        "spacing_violations": len(violations),
        "tie_break": "lowest scalp vertex ID at equal geodesic maximin distance",
    }
    (output / "FinePlanSummary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
