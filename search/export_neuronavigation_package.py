#!/usr/bin/env python3
"""Export a completed two-stage TANS prescription for Brainsight navigation."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

import nibabel as nib
import numpy as np
from simnibs import brainsight
from simnibs.mesh_tools import mesh_io
from simulated_pose_geometry import saved_pose_matrix
from selected_pose import selected_pose_paths

from pose_prescription import (
    canonicalize_pose_matrix,
    prescribed_orientation,
    resolve_handle_policy,
)


def require_file(path, label):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def read_json(path, label):
    return json.loads(require_file(path, label).read_text())


def read_tsv(path, label):
    with require_file(path, label).open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def normalized_target_name(value):
    cleaned = "".join(character if character.isalnum() else "-" for character in value)
    return "-".join(part for part in cleaned.split("-") if part)


def find_placement(rows, orientation_degrees, tolerance=1e-8):
    matches = [
        row for row in rows
        if abs(float(row["orientation_degrees"]) - float(orientation_degrees)) <= tolerance
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one saved placement at {orientation_degrees:g} degrees; "
            f"found {len(matches)}")
    return matches[0]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matrix_from_saved_pose(target_root, best_pose, config, variant='selected'):
    selected = selected_pose_paths(target_root, variant=variant)
    center_dir = selected['result'] / f"Center_{int(best_pose['center_id']):05d}"
    benchmark = read_json(center_dir / "Benchmark.json", "selected-center benchmark")
    placements = read_tsv(center_dir / "Placements.tsv", "selected-center placements")
    placement = find_placement(placements, float(best_pose["orientation_degrees"]))

    saved_center = np.asarray(benchmark["center_xyz"], dtype=float)
    selected_center = np.asarray(
        [best_pose[key] for key in ("x", "y", "z")], dtype=float)
    if not np.allclose(saved_center, selected_center, atol=1e-5):
        raise ValueError(
            "Selected pose coordinates do not match its simulation benchmark")
    if int(benchmark["scalp_vertex_one_based"]) != int(best_pose["scalp_vertex"]):
        raise ValueError("Selected scalp vertex does not match its simulation benchmark")

    pos_ydir = np.asarray([
        placement["pos_ydir_x"], placement["pos_ydir_y"],
        placement["pos_ydir_z"]], dtype=float)
    head_mesh = require_file(config["head_mesh"], "SimNIBS head mesh")
    from simulated_pose_geometry import fit_pose_matrices
    matrices, _ = fit_pose_matrices(
        head_mesh, saved_center, [pos_ydir], float(benchmark['distance_mm']),
        scalp_surface=config['skin'])
    matrix = np.asarray(matrices[0])
    recorded = saved_pose_matrix(benchmark, float(best_pose['orientation_degrees']))
    if not np.allclose(matrix, recorded, atol=1e-8, rtol=0):
        raise ValueError('Navigation pose does not match the exact simulated transform')
    matrix = recorded
    if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
        raise ValueError("SimNIBS did not return a finite 4x4 pose transform")
    if not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-6):
        raise ValueError("Export pose rotation is not orthonormal")
    if np.linalg.det(matrix[:3, :3]) < 0.999999:
        raise ValueError("Export pose rotation is not right-handed")
    return matrix, benchmark, placement, head_mesh


def validate_brainsight_roundtrip(path, expected_matrix):
    targets, samples = brainsight().read(str(path))
    positions = list(targets.pos) + list(samples.pos)
    if len(positions) != 1:
        raise ValueError(
            f"Brainsight round trip returned {len(positions)} positions instead of one")
    observed = np.asarray(positions[0].matsimnibs)
    error = float(np.max(np.abs(observed - expected_matrix)))
    if error > 1e-4:
        raise ValueError(f"Brainsight round-trip transform error is {error:g}")
    return error


def export_package(target_root, output=None, t1w=None, variant='selected', preview=None):
    target_root = Path(target_root).resolve()
    if not target_root.is_dir():
        raise FileNotFoundError(f"Missing target root: {target_root}")
    output = Path(output or target_root / "09_Neuronavigation").resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite neuronavigation package: {output}")

    selected = selected_pose_paths(target_root, variant=variant)
    best_path = selected['best_pose']
    best = json.loads(best_path.read_text())
    config_path = selected['config']
    config = json.loads(config_path.read_text())
    subject = str(config["subject"])
    target = normalized_target_name(target_root.name)
    simulated_matrix, benchmark, placement, head_mesh = matrix_from_saved_pose(
        target_root, best, config, variant=variant)
    handle_policy = resolve_handle_policy(config)
    matrix, handle_decision = canonicalize_pose_matrix(
        simulated_matrix, handle_policy)
    simulated_orientation = float(best["orientation_degrees"])
    prescription_orientation = prescribed_orientation(
        simulated_orientation, handle_decision["flip_applied"])

    default_t1w = Path(config["m2m_dir"]) / "T1.nii.gz"
    t1w_source = require_file(t1w or default_t1w, "subject-space T1w")
    image = nib.load(str(t1w_source))
    if not np.all(np.isfinite(image.affine)):
        raise ValueError("T1w affine contains non-finite values")

    output.mkdir(parents=True)
    stem = f"{subject}_{target}"
    if variant != 'selected':
        stem += f"_{selected['variant'].capitalize()}"
    t1w_output = output / f"{subject}_T1w.nii.gz"
    trajectory_output = output / f"{stem}_OptimalTrajectory_Brainsight.txt"
    matrix_output = output / f"{stem}_OptimalPose_matsimnibs.txt"
    pose_output = output / f"{stem}_OptimalPose.json"
    preview_output = output / f"{stem}_OptimalPoseOverview.png"
    readme_output = output / "README.txt"
    manifest_output = output / "NeuronavigationManifest.json"

    shutil.copy2(t1w_source, t1w_output)
    np.savetxt(matrix_output, matrix, fmt="%.10f", delimiter="\t")
    brainsight().write(
        matrix, str(trajectory_output), names=[f"{stem}_TANS"],
        overwrite=False)
    roundtrip_error = validate_brainsight_roundtrip(trajectory_output, matrix)
    exported_pose = dict(best)
    exported_pose['pose_variant'] = selected['variant']
    exported_pose["prescription"] = {
        "simulated_orientation_degrees": simulated_orientation,
        "prescribed_orientation_degrees": prescription_orientation,
        "handle_orientation_policy": handle_policy,
        **handle_decision,
    }
    pose_output.write_text(json.dumps(exported_pose, indent=2) + "\n")

    preview_source = (require_file(preview, 'pose-specific preview') if preview else
                      target_root / "08_Reports" / "OptimalPose" / "OptimalPoseOverview.png")
    if variant != 'selected' and preview is None:
        preview_source = preview_source.parent / selected['variant'] / preview_source.name
    if preview_source.is_file():
        shutil.copy2(preview_source, preview_output)

    readme_output.write_text(
        "TANS NEURONAVIGATION EXPORT\n"
        "=============================\n\n"
        f"Subject: {subject}\n"
        f"Target: {target}\n"
        f"Hardware coil: {config.get('coil_hardware_name', Path(config['coil']).stem)}\n"
        f"SimNIBS coil model: {config['coil']}\n"
        f"Prescription: {selected['mode']} selected center with practical handle orientation\n"
        f"Placement-error robustness: {selected['robustness_status']}\n"
        f"Simulated orientation: {simulated_orientation:g} degrees\n"
        f"Prescribed orientation: {prescription_orientation:g} degrees\n"
        f"180-degree handle flip applied: {handle_decision['flip_applied']}\n"
        "Brainsight coordinate system: NIfTI:Aligned\n"
        f"Anatomical image: {t1w_output.name}\n"
        f"Trajectory: {trajectory_output.name}\n"
        f"SimNIBS transform: {matrix_output.name}\n\n"
        "Import the subject-named T1w and the Brainsight trajectory together. "
        "Before treatment, visually confirm scalp contact, hemisphere, center, "
        "and handle direction against the included optimal-pose overview.\n")

    package_files = [
        t1w_output, trajectory_output, matrix_output, pose_output, readme_output]
    if preview_output.is_file():
        package_files.append(preview_output)
    manifest = {
        "schema_version": "tans-neuronavigation-export-v2",
        "status": "coordinate_roundtrip_validated",
        "selection_mode": selected['mode'],
        "pose_variant": selected['variant'],
        "robustness_status": selected['robustness_status'],
        "subject": subject,
        "target": target,
        "coordinate_system": "NIfTI:Aligned",
        "coil_model": config["coil"],
        "coil_hardware_name": config.get(
            "coil_hardware_name", Path(config["coil"]).stem.replace("_", " ")),
        "coil_calibration_id": (
            config.get("coil_calibration") or {}).get("id"),
        "selected_pose": {
            "scalp_vertex_one_based": int(best["scalp_vertex"]),
            "scalp_center_xyz": [float(best[key]) for key in ("x", "y", "z")],
            "simulated_orientation_degrees": simulated_orientation,
            "prescribed_orientation_degrees": prescription_orientation,
            "orientation_degrees": prescription_orientation,
            "handle_flip_applied": handle_decision["flip_applied"],
            "handle_orientation_policy": handle_policy,
            "simulated_physical_handle_direction_ras": handle_decision[
                "simulated_physical_handle_direction_ras"],
            "prescribed_physical_handle_direction_ras": handle_decision[
                "prescribed_physical_handle_direction_ras"],
            "coil_scalp_distance_mm": float(benchmark["distance_mm"]),
            "pos_ydir_xyz": [float(placement[key]) for key in (
                "pos_ydir_x", "pos_ydir_y", "pos_ydir_z")],
            "simulated_matsimnibs": simulated_matrix.tolist(),
            "prescribed_matsimnibs": matrix.tolist(),
            "matsimnibs": matrix.tolist(),
        },
        "validation": {
            "brainsight_export_readback_max_abs_error": roundtrip_error,
            "rotation_determinant": float(np.linalg.det(matrix[:3, :3])),
            "requires_visual_import_confirmation": True,
        },
        "sources": {
            "target_root": str(target_root),
            "selected_pose": str(best_path),
            "selected_config": str(config_path),
            "best_robust_pose": str(best_path) if selected['variant'] == 'robust' else None,
            "robustness_config": str(config_path) if selected['variant'] == 'robust' else None,
            "head_mesh": str(head_mesh),
            "t1w": str(t1w_source),
        },
        "files": {},
    }
    manifest_output.write_text(json.dumps(manifest, indent=2) + "\n")
    package_files.append(manifest_output)
    manifest["files"] = {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in package_files if path != manifest_output
    }
    manifest_output.write_text(json.dumps(manifest, indent=2) + "\n")

    result = {
        "output": str(output),
        "t1w": str(t1w_output),
        "brainsight_trajectory": str(trajectory_output),
        "matsimnibs_transform": str(matrix_output),
        "manifest": str(manifest_output),
        "roundtrip_max_abs_error": roundtrip_error,
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--output")
    parser.add_argument("--t1w")
    parser.add_argument('--pose-variant', choices=('selected', 'fine', 'robust'), default='selected')
    parser.add_argument('--preview')
    args = parser.parse_args()
    print(json.dumps(export_package(
        args.target_root, output=args.output, t1w=args.t1w,
        variant=args.pose_variant, preview=args.preview), indent=2))


if __name__ == "__main__":
    main()
