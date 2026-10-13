#!/usr/bin/env python3
"""Benchmark a search-grid center through SimNIBS and historical fs_LR mapping."""
import argparse
import csv
import json
import subprocess
import time
from pathlib import Path

import nibabel as nib
import numpy as np
from simnibs import run_simnibs, sim_struct
from simulated_pose_geometry import fit_pose_matrices


def require_file(value, description):
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {description}: {path}")
    return path


def run_checked(command, stage):
    started = time.perf_counter()
    result = subprocess.run(command, text=True, capture_output=True)
    elapsed = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError(
            f"{stage} failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return elapsed


def load_surface(path):
    surface = nib.load(str(path))
    points = None
    triangles = None
    for array in surface.darrays:
        if array.intent == 1008:
            points = np.asarray(array.data, dtype=float)
        elif array.intent == 1009:
            triangles = np.asarray(array.data, dtype=np.int64)
    if points is None or triangles is None:
        raise ValueError(f"Surface lacks points or triangles: {path}")
    return points, triangles


def center_normal(points, triangles, vertex):
    incident = triangles[np.any(triangles == vertex, axis=1)]
    if not len(incident):
        raise ValueError(f"Scalp vertex {vertex + 1} has no incident triangles")
    a, b, c = points[incident[:, 0]], points[incident[:, 1]], points[incident[:, 2]]
    normal = np.sum(np.cross(b - a, c - a), axis=0)
    norm = np.linalg.norm(normal)
    if norm == 0:
        raise ValueError(f"Cannot calculate normal at scalp vertex {vertex + 1}")
    normal /= norm
    if np.dot(normal, points[vertex] - points.mean(axis=0)) < 0:
        normal *= -1
    return normal


def canonical_tangent(normal):
    for reference in (np.array([0.0, 0.0, 1.0]),
                      np.array([0.0, 1.0, 0.0]),
                      np.array([1.0, 0.0, 0.0])):
        tangent = reference - np.dot(reference, normal) * normal
        norm = np.linalg.norm(tangent)
        if norm > 1e-6:
            return tangent / norm
    raise ValueError("Cannot construct a scalp-tangent reference direction")


def rotate_about_axis(vector, axis, degrees):
    theta = np.deg2rad(degrees)
    return (vector * np.cos(theta) + np.cross(axis, vector) * np.sin(theta) +
            axis * np.dot(axis, vector) * (1.0 - np.cos(theta)))


def read_grid_row(path, center_id):
    with Path(path).open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    matches = [row for row in rows if int(row["center_id"]) == center_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one center_id={center_id}; found {len(matches)}")
    return matches[0]


def directory_bytes(path):
    return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--grid-tsv", required=True)
    parser.add_argument("--center-id", type=int, default=1)
    parser.add_argument("--orientation-degrees", type=float, nargs="+", default=[0.0])
    parser.add_argument("--head-mesh", required=True)
    parser.add_argument("--m2m-dir", required=True)
    parser.add_argument("--coil", required=True)
    parser.add_argument("--workbench", required=True)
    parser.add_argument("--skin", required=True)
    parser.add_argument("--left-midthickness", required=True)
    parser.add_argument("--right-midthickness", required=True)
    parser.add_argument("--left-white", required=True)
    parser.add_argument("--right-white", required=True)
    parser.add_argument("--left-pial", required=True)
    parser.add_argument("--right-pial", required=True)
    parser.add_argument("--left-roi", required=True)
    parser.add_argument("--right-roi", required=True)
    parser.add_argument("--distance-mm", type=float, default=1.0)
    parser.add_argument("--didt-aperus", type=float, default=1e6)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.subject.strip():
        parser.error("--subject must not be empty")

    file_fields = (
        "grid_tsv", "head_mesh", "coil", "skin", "left_midthickness",
        "right_midthickness", "left_white", "right_white", "left_pial",
        "right_pial", "left_roi", "right_roi")
    inputs = {field: require_file(getattr(args, field), field.replace("_", " "))
              for field in file_fields}
    workbench = require_file(args.workbench, "Connectome Workbench executable")
    m2m_dir = Path(args.m2m_dir).resolve()
    if not m2m_dir.is_dir():
        raise FileNotFoundError(f"Missing m2m directory: {m2m_dir}")
    output = Path(args.output).resolve()
    if output.exists() and not args.resume:
        raise FileExistsError(f"Refusing to overwrite benchmark: {output}")
    output.mkdir(parents=True, exist_ok=args.resume)

    grid_row = read_grid_row(inputs["grid_tsv"], args.center_id)
    scalp_vertex = int(grid_row["scalp_vertex"]) - 1
    points, triangles = load_surface(inputs["skin"])
    if scalp_vertex < 0 or scalp_vertex >= len(points):
        raise ValueError(f"Invalid one-based scalp vertex: {scalp_vertex + 1}")
    center = np.array([float(grid_row[key]) for key in ("x", "y", "z")])
    if not np.allclose(center, points[scalp_vertex], atol=1e-4):
        raise ValueError("Grid coordinates do not match the saved scalp vertex")
    normal = center_normal(points, triangles, scalp_vertex)
    orientations = [float(value) % 180.0 for value in args.orientation_degrees]
    if len(set(orientations)) != len(orientations):
        raise ValueError("Orientations must be unique over 180 degrees")

    session = sim_struct.SESSION()
    session.fnamehead = str(inputs["head_mesh"])
    session.subpath = str(m2m_dir)
    session.pathfem = str(output / "Simulation")
    session.open_in_gmsh = False
    session.map_to_surf = False
    session.map_to_fsavg = False
    session.map_to_vol = True
    session.map_to_mni = False
    session.fields = "e"
    tms = session.add_tmslist()
    tms.fnamecoil = str(inputs["coil"])
    placement_rows = []
    base_tangent = canonical_tangent(normal)
    for orientation in orientations:
        tangent = rotate_about_axis(base_tangent, normal, orientation)
        y_direction = center + 20.0 * tangent
        position = tms.add_position()
        position.centre = center
        position.pos_ydir = y_direction
        position.distance = args.distance_mm
        position.didt = args.didt_aperus
        placement_rows.append({
            "orientation_degrees": orientation,
            "tangent_x": tangent[0], "tangent_y": tangent[1], "tangent_z": tangent[2],
            "pos_ydir_x": y_direction[0], "pos_ydir_y": y_direction[1],
            "pos_ydir_z": y_direction[2],
        })

    fitted_matrices, geometry_validation = fit_pose_matrices(
        inputs['head_mesh'], center,
        [[row[f'pos_ydir_{axis}'] for axis in 'xyz'] for row in placement_rows],
        args.distance_mm, scalp_surface=inputs['skin'])
    for position, matrix in zip(tms.pos, fitted_matrices):
        position.matsimnibs = matrix

    volume_matches = sorted((output / "Simulation" / "subject_volumes").glob(
        "*_magnE.nii.gz"))
    reused_simulation_volumes = args.resume and len(volume_matches) == len(orientations)
    stage_rows = []
    if reused_simulation_volumes:
        stage_rows.append({"stage": "reused_simnibs_volume_export", "seconds": 0.0})
    else:
        if args.resume and output.exists() and any(output.iterdir()):
            raise RuntimeError(
                "Resume requires a complete set of existing orientation volumes; "
                f"found {len(volume_matches)} of {len(orientations)} in {output}")
        started = time.perf_counter()
        run_simnibs(session)
        stage_rows.append({"stage": "simnibs_and_volume_export",
                           "seconds": time.perf_counter() - started})
        volume_matches = sorted(
            (output / "Simulation" / "subject_volumes").glob("*_magnE.nii.gz"))
    if len(volume_matches) != len(orientations):
        raise RuntimeError(
            f"Expected {len(orientations)} magnE volume(s); found {len(volume_matches)}")
    if len(volume_matches) == 1:
        volume = volume_matches[0]
    else:
        volume = output / "magnE.all_orientations.nii.gz"
        seconds = run_checked(["fslmerge", "-t", str(volume)] +
                              [str(path) for path in volume_matches], "volume merge")
        stage_rows.append({"stage": "volume_merge", "seconds": seconds})
    metrics = {}
    for hemi in ("left", "right"):
        metric = output / f"magnE.{hemi[0].upper()}.32k_fs_LR.shape.gii"
        command = [str(workbench), "-volume-to-surface-mapping", str(volume),
                   str(inputs[f"{hemi}_midthickness"]), str(metric),
                   "-ribbon-constrained", str(inputs[f"{hemi}_white"]),
                   str(inputs[f"{hemi}_pial"]),
                   "-interpolate", "ENCLOSING_VOXEL"]
        seconds = run_checked(command, f"historical ribbon mapping ({hemi})")
        stage_rows.append({"stage": f"ribbon_mapping_{hemi}", "seconds": seconds})
        masked = output / f"magnE.{hemi[0].upper()}.32k_fs_LR.masked.shape.gii"
        seconds = run_checked([str(workbench), "-metric-mask", str(metric),
                               str(inputs[f"{hemi}_roi"]), str(masked)],
                              f"medial-wall masking ({hemi})")
        stage_rows.append({"stage": f"medial_wall_mask_{hemi}", "seconds": seconds})
        metrics[hemi] = masked
    dense = output / "magnE.dtseries.nii"
    seconds = run_checked([
        str(workbench), "-cifti-create-dense-timeseries", str(dense),
        "-left-metric", str(metrics["left"]), "-roi-left", str(inputs["left_roi"]),
        "-right-metric", str(metrics["right"]), "-roi-right", str(inputs["right_roi"])],
        "dense-timeseries creation")
    stage_rows.append({"stage": "dense_timeseries_creation", "seconds": seconds})

    total_seconds = sum(row["seconds"] for row in stage_rows)
    with (output / "Timing.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=("stage", "seconds"))
        writer.writeheader()
        writer.writerows(stage_rows)
    with (output / "Placements.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=list(placement_rows[0]))
        writer.writeheader()
        writer.writerows(placement_rows)
    provenance = {
        "subject": args.subject,
        "network": grid_row["network_name"],
        "map_mode": grid_row["target_map_mode"],
        "strategy": grid_row["strategy"],
        "center_id": args.center_id,
        "scalp_vertex_one_based": scalp_vertex + 1,
        "center_xyz": center.tolist(),
        "scalp_normal": normal.tolist(),
        "canonical_tangent": base_tangent.tolist(),
        "orientations_degrees": orientations,
        "simulated_pose_matrices": [matrix.tolist() for matrix in fitted_matrices],
        "geometry_validation": geometry_validation,
        "number_of_orientations": len(orientations),
        "distance_mm": args.distance_mm,
        "didt_aperus": args.didt_aperus,
        "inputs": {name: str(path) for name, path in inputs.items()},
        "workbench": str(workbench),
        "m2m_dir": str(m2m_dir),
        "total_seconds": total_seconds,
        "output_bytes": directory_bytes(output),
        "simnibs_executed": not reused_simulation_volumes,
        "simulation_volumes_reused": reused_simulation_volumes,
        "surface_space": "subject transformed fs_LR 32k",
        "volume_to_surface_method": (
            "historical Workbench ribbon-constrained with "
            "-interpolate ENCLOSING_VOXEL"),
    }
    (output / "Benchmark.json").write_text(json.dumps(provenance, indent=2))
    print(json.dumps({"output": str(output), "total_seconds": total_seconds,
                      "output_bytes": provenance["output_bytes"]}, indent=2))


if __name__ == "__main__":
    main()
