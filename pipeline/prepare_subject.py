#!/usr/bin/env python3
"""Create a native-image CHARM head model and TANS surface derivatives."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from html import unescape
import json
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Sequence

import nibabel as nib
import numpy as np
import yaml


SURFACE_TYPES = ("midthickness", "white", "pial", "inflated")


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_scalp_manifest(manifest: dict) -> dict:
    scalp = Path(manifest['resolved_subject_adapter']['skin'])
    proof_path = scalp.with_suffix(scalp.suffix + '.json')
    if not proof_path.is_file():
        raise ValueError(
            f'Scalp extraction is unverified: {scalp}. Validate against scalp-only '
            'CHARM triangles before reusing this headmodel adapter or its grids.')
    proof = json.loads(proof_path.read_text())
    expected = ('tans-scalp-extraction-v3', 'validated', 2, 1005, 'exterior_skin')
    observed = tuple(proof.get(key) for key in (
        'schema_version', 'status', 'element_type', 'element_tag', 'surface_scope'))
    if observed != expected:
        raise ValueError('Scalp extraction provenance does not certify exterior-only skin; '
                         'regenerate scalp and downstream grids to exclude internal air cavities')
    if proof.get('source_head_mesh_sha256') != manifest['source']['head_mesh_sha256']:
        raise ValueError('Scalp extraction and subject adapter refer to different head meshes')
    if proof.get('surface_sha256') != sha256(scalp):
        raise ValueError('Scalp surface changed after validation; downstream grids must be reviewed')
    return proof


def validate_search_scalp(run_manifest: dict) -> None:
    config = yaml.safe_load(Path(run_manifest['configuration']).read_text())
    adapter = json.loads(Path(config['subject_adapter']['manifest']).read_text())
    proof = validate_scalp_manifest(adapter)
    if run_manifest.get('scalp_surface_sha256') != proof['surface_sha256']:
        raise ValueError(
            'Search plan lacks matching scalp provenance. Regenerate the grids and '
            'dependent simulations; changing the scalp file cannot repair an old search.')


def run(command: Sequence[str], label: str, cwd: Path | None = None) -> None:
    print(f"[subject-adapter] {label}", flush=True)
    completed = subprocess.run(
        command, cwd=None if cwd is None else str(cwd),
        capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(
            f"{label} failed with status {completed.returncode}:\n"
            f"{completed.stdout}{completed.stderr}")


def validate_32k_surface(path: Path) -> int:
    image = nib.load(str(path))
    points = next((array for array in image.darrays if array.intent == 1008), None)
    if points is None:
        raise ValueError(f"Surface has no pointset: {path}")
    xyz = np.asarray(points.data)
    if xyz.shape != (32492, 3) or np.any(~np.isfinite(xyz)):
        raise ValueError(f"Expected a finite 32k surface, got {xyz.shape}: {path}")
    return len(xyz)


def build_charm_command(charm_binary: Path, subject: str,
                        native_t1w: Path, native_t2w: Path) -> list:
    if native_t1w.name != "T1w.nii.gz":
        raise ValueError(
            "Native CHARM T1 input must be named T1w.nii.gz; ACPC/restore "
            "images are not valid for the TANS head model")
    if native_t2w.name != "T2w.nii.gz":
        raise ValueError(
            "Native CHARM T2 input must be named T2w.nii.gz; registered/restore "
            "images are not valid for the TANS head model")
    return [
        str(charm_binary), str(subject), str(native_t1w), str(native_t2w),
        "--forceqform",
    ]


def acpc_inverse_affine(subject_dir: Path, surface_output: Path) -> Path:
    transforms = subject_dir / 'anat/T1w/xfms'
    forward = require_file(transforms / 'acpc.mat', 'native-to-ACPC affine')
    matrix = np.loadtxt(forward)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
            or not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-10)
            or np.linalg.cond(matrix) > 1e12):
        raise ValueError(f'Invalid native-to-ACPC affine: {forward}')
    expected = np.linalg.inv(matrix)
    existing = transforms / 'acpc_inv.mat'
    if existing.is_file():
        inverse = np.loadtxt(existing)
        if inverse.shape != (4, 4) or not np.allclose(inverse, expected, atol=1e-5, rtol=0):
            raise ValueError('Existing ACPC inverse does not invert the forward transform')
        return existing.resolve()
    output = surface_output / 'acpc_inv.mat'
    converter = require_file(shutil.which('convert_xfm') or '/usr/local/fsl/bin/convert_xfm',
                             'FSL convert_xfm')
    run([str(converter), '-omat', str(output), '-inverse', str(forward)],
        'derive ACPC-to-native affine with FSL convert_xfm')
    np.testing.assert_allclose(np.loadtxt(output) @ matrix, np.eye(4), atol=1e-8, rtol=0)
    return output


def validate_completed_native_charm(charm: Path, command: list) -> dict:
    log = require_file(charm / 'charm_log.html', 'completed native CHARM log')
    text = unescape(log.read_text())
    options = [line.split('DEBUG: options:', 1)[1].strip()
               for line in text.splitlines() if 'DEBUG: options:' in line]
    starts = [line.split('charm run started:', 1)[1].strip()
              for line in text.splitlines() if 'charm run started:' in line]
    if (len(options) != 1 or shlex.split(options[0]) != command[1:]
            or len(starts) != 1 or 'INFO: charm run finished:' not in text):
        raise ValueError('Cannot verify successful CHARM with the exact native input images')
    started = datetime.strptime(starts[0], '%a %b %d %H:%M:%S %Y').timestamp()
    for value in command[2:4]:
        if Path(value).stat().st_mtime > started:
            raise ValueError('Native input changed since CHARM started; cannot resume')
    native = nib.load(command[2])
    saved = nib.load(str(require_file(charm / 'T1.nii.gz', 'CHARM native T1w')))
    if native.shape != saved.shape:
        raise ValueError('Completed CHARM T1w does not match native input shape')
    np.testing.assert_allclose(saved.affine, native.get_qform(), atol=1e-5, rtol=0)
    if not np.array_equal(np.asanyarray(saved.dataobj), np.asanyarray(native.dataobj)):
        raise ValueError('Completed CHARM T1w intensities differ from native input')
    return {'completion_log': str(log), 'completion_log_sha256': sha256(log),
            'exact_native_command_verified': True, 'native_t1w_voxels_verified': True,
            'native_inputs_predate_charm_start': True}


def prepare(config_path: str | Path, output_root: str | Path,
            resume_completed_charm: bool = False) -> dict:
    config_path = require_file(config_path, "configuration")
    config = yaml.safe_load(config_path.read_text())
    subject = str(config["subject"])
    adapter = config.get("subject_adapter", {})
    mode = str(adapter.get("mode", "native_t1w_t2w_charm"))
    if mode != "native_t1w_t2w_charm":
        raise ValueError(
            "prepare_subject.py requires "
            "subject_adapter.mode=native_t1w_t2w_charm")
    subject_dir = require_directory(adapter["subject_dir"], "subject directory")
    output_root = Path(output_root).expanduser().resolve()
    if output_root.exists() and not resume_completed_charm:
        raise FileExistsError(
            f"Refusing to overwrite subject adapter output: {output_root}")
    if resume_completed_charm and (output_root / 'SubjectAdapter.json').exists():
        raise FileExistsError('Subject adapter is already complete; use normal pipeline resume')
    output_root.mkdir(parents=True, exist_ok=resume_completed_charm)
    surface_output = output_root / "surfaces"
    surface_output.mkdir(exist_ok=resume_completed_charm)
    transform = acpc_inverse_affine(subject_dir, surface_output)

    native_t1w = require_file(
        adapter.get("native_t1w", subject_dir / "anat" / "T1w" / "T1w.nii.gz"),
        "native T1w")
    native_t2w = require_file(
        adapter.get("native_t2w", subject_dir / "anat" / "T2w" / "T2w.nii.gz"),
        "native T2w")
    expected_t1w = (subject_dir / "anat" / "T1w" / "T1w.nii.gz").resolve()
    expected_t2w = (subject_dir / "anat" / "T2w" / "T2w.nii.gz").resolve()
    if native_t1w != expected_t1w or native_t2w != expected_t2w:
        raise ValueError(
            "TANS CHARM inputs must be the subject's exact native images: "
            f"{expected_t1w} and {expected_t2w}; got {native_t1w} and "
            f"{native_t2w}")
    charm_binary = require_file(adapter["charm_binary"], "CHARM executable")
    simnibs_python = require_file(
        config["simulation"]["simnibs_python"], "SimNIBS Python")
    charm_command = build_charm_command(
        charm_binary, subject, native_t1w, native_t2w)
    recovery = None
    if resume_completed_charm:
        recovery = validate_completed_native_charm(output_root / f'm2m_{subject}', charm_command)
        print('[subject-adapter] verified completed native CHARM; resume surface adaptation only', flush=True)
    else:
        run(charm_command, "run CHARM on native T1w and T2w", cwd=output_root)
    charm = require_directory(output_root / f"m2m_{subject}", "new CHARM model")
    head_mesh = require_file(charm / f"{subject}.msh", "new CHARM mesh")
    charm_t1w = require_file(charm / "T1.nii.gz", "CHARM T1w")
    charm_t2w = require_file(charm / "T2_reg.nii.gz", "CHARM registered T2w")

    scalp = charm / "Skin.surf.gii"
    extractor = require_file(
        Path(__file__).resolve().parents[1] / "search" /
        "extract_charm_scalp.py", "CHARM scalp extractor")
    if resume_completed_charm and scalp.is_file():
        validate_scalp_manifest({'resolved_subject_adapter': {'skin': str(scalp)},
                                'source': {'head_mesh_sha256': sha256(head_mesh)}})
    else:
        run([str(simnibs_python), str(extractor), str(head_mesh), str(scalp)],
            "extract scalp from new CHARM mesh")

    workbench = require_file(
        adapter.get("workbench", "/usr/local/workbench/bin_linux64/wb_command"),
        "Connectome Workbench")
    source_surfaces = require_directory(
        subject_dir / "anat" / "T1w" / "fsaverage_LR32k",
        "HCP T1w 32k surfaces")
    flirt_source = require_file(
        subject_dir / "anat" / "T1w" / "T1w_acpc.nii.gz", "ACPC T1w")
    surfaces = {}
    surface_inputs = {}
    for hemisphere, structure in (("L", "CORTEX_LEFT"),
                                  ("R", "CORTEX_RIGHT")):
        side = "left" if hemisphere == "L" else "right"
        for surface_type in SURFACE_TYPES:
            source = require_file(
                source_surfaces /
                f"{subject}.{hemisphere}.{surface_type}.32k_fs_LR.surf.gii",
                f"{side} {surface_type} surface")
            destination = surface_output / (
                f"{subject}.{hemisphere}.{surface_type}.native.32k_fs_LR.surf.gii")
            run([
                str(workbench), "-surface-apply-affine", str(source), str(transform),
                str(destination), "-flirt", str(flirt_source), str(native_t1w),
            ], f"transform {side} {surface_type} surface")
            run([str(workbench), "-set-structure", str(destination), structure],
                f"label {side} {surface_type} surface")
            validate_32k_surface(destination)
            surfaces[f"{side}_{surface_type}"] = str(destination)
            surface_inputs[f"{side}_{surface_type}"] = {
                "path": str(source), "sha256": sha256(source)}
        roi = require_file(
            subject_dir / "anat" / "MNINonLinear" / "fsaverage_LR32k" /
            f"{subject}.{hemisphere}.atlasroi.32k_fs_LR.shape.gii",
            f"{side} cortical ROI")
        surfaces[f"{side}_roi"] = str(roi)
        surface_inputs[f"{side}_roi"] = {
            "path": str(roi), "sha256": sha256(roi)}

    resolved = {
        "mode": mode,
        "subject_dir": str(subject_dir),
        "head_mesh": str(head_mesh),
        "m2m_dir": str(charm),
        "skin": str(scalp),
        "t1w": str(charm_t1w),
        "surfaces": surfaces,
    }
    manifest = {
        "schema_version": 1,
        "subject": subject,
        "status": "validated",
        "completed_charm_recovery": recovery,
        "configuration": str(config_path),
        "source": {
            "native_t1w": str(native_t1w),
            "native_t1w_sha256": sha256(native_t1w),
            "native_t2w": str(native_t2w),
            "native_t2w_sha256": sha256(native_t2w),
            "charm_command": charm_command,
            "head_mesh_sha256": sha256(head_mesh),
            "charm_t1w_sha256": sha256(charm_t1w),
            "charm_t2w_sha256": sha256(charm_t2w),
            "acpc_inverse_affine": str(transform),
            "acpc_inverse_affine_sha256": sha256(transform),
            "acpc_forward_affine": str(subject_dir / 'anat/T1w/xfms/acpc.mat'),
            "acpc_forward_affine_sha256": sha256(subject_dir / 'anat/T1w/xfms/acpc.mat'),
            "surface_inputs": surface_inputs,
        },
        "resolved_subject_adapter": resolved,
    }
    validate_scalp_manifest(manifest)
    (output_root / "SubjectAdapter.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--output", required=True)
    parser.add_argument('--resume-completed-charm', action='store_true',
                        help='After review, reuse only a verified successful native CHARM run in this output')
    args = parser.parse_args()
    print(json.dumps(prepare(args.config, args.output, args.resume_completed_charm), indent=2))


if __name__ == "__main__":
    main()
