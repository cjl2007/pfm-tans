"""Exact SimNIBS pose geometry shared by simulation, QC, and navigation."""

import numpy as np
from pathlib import Path
from extract_charm_scalp import extract_scalp, validate_surface, sha256


def fit_pose_matrices(head_mesh, center, y_directions, distance_mm, scalp_surface=None):
    from simnibs.mesh_tools import mesh_io

    mesh = mesh_io.read_msh(str(head_mesh))
    scalp = extract_scalp(mesh)
    if scalp_surface is not None:
        validate_surface(scalp_surface, scalp)
    center = np.asarray(center, dtype=float)
    displacement = float(np.min(np.linalg.norm(scalp.nodes.node_coord - center, axis=1)))
    if displacement > 0.05:
        raise ValueError(
            f'Requested center is {displacement:.3f} mm from exterior skin; '
            'refusing to simulate a silently reprojected grid center.')
    matrices = [scalp.calc_matsimnibs(
        center.copy(), np.asarray(direction, dtype=float).copy(), distance_mm,
        msh_surf=scalp) for direction in y_directions]
    for matrix in matrices:
        contact = matrix[:3, 3] + distance_mm * matrix[:3, 2]
        if not np.allclose(contact, center, atol=0.05, rtol=0):
            raise ValueError('SimNIBS coil contact does not match requested scalp center')
    return matrices, {'status': 'validated', 'scalp_tag': 1005,
                      'surface_scope': 'exterior_skin',
                      'partition_method': 'simnibs.Msh.partition_skin_surface',
                      'scalp_surface_sha256': sha256(scalp_surface) if scalp_surface else None,
                      'requested_center_to_scalp_mm': displacement,
                      'maximum_permitted_projection_mm': 0.05}


def saved_pose_matrix(benchmark, orientation_degrees):
    angles = np.asarray(benchmark['orientations_degrees'], dtype=float)
    indices = np.flatnonzero(np.isclose(angles, orientation_degrees, atol=1e-8, rtol=0))
    if len(indices) != 1:
        raise ValueError('Selected orientation is ambiguous in saved pose geometry')
    matrices = benchmark.get('simulated_pose_matrices')
    if matrices is None or len(matrices) != len(angles):
        raise ValueError('Exact simulated pose transforms are missing; do not approximate coil geometry')
    matrix = np.asarray(matrices[int(indices[0])], dtype=float)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all() or
            not np.allclose(matrix[3], [0, 0, 0, 1]) or
            not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-6) or
            not np.isclose(np.linalg.det(matrix[:3, :3]), 1.0, atol=1e-6)):
        raise ValueError('Saved simulated pose is not a valid rigid transform')
    return matrix.copy()
