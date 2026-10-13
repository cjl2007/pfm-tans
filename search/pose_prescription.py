#!/usr/bin/env python3
"""Canonicalize magnitude-equivalent TMS poses for practical prescription."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np


MODES = ("as_simulated", "prefer_posterior_inferior")


def resolve_handle_policy(config: Mapping[str, object] | None) -> dict:
    """Return and validate the handle-orientation prescription policy."""
    prescription = dict((config or {}).get("prescription", {}))
    raw = dict(prescription.get("handle_orientation", {}))
    mode = str(raw.get("mode", "as_simulated"))
    if mode not in MODES:
        raise ValueError(
            f"handle_orientation.mode must be one of {MODES}; got {mode!r}")
    equivalent = bool(raw.get(
        "coil_180_degree_magnitude_equivalent", False))
    if mode != "as_simulated" and not equivalent:
        raise ValueError(
            "Automatic handle canonicalization requires an explicit "
            "coil_180_degree_magnitude_equivalent: true assertion")
    local_axis = str(raw.get("physical_handle_local_axis", "negative_y"))
    if local_axis != "negative_y":
        raise ValueError(
            "Only physical_handle_local_axis: negative_y is currently supported")
    posterior_weight = float(raw.get("posterior_weight", 1.0))
    inferior_weight = float(raw.get("inferior_weight", 1.0))
    if posterior_weight < 0 or inferior_weight < 0:
        raise ValueError("Handle-direction preference weights cannot be negative")
    if mode != "as_simulated" and posterior_weight + inferior_weight <= 0:
        raise ValueError("At least one handle-direction preference weight must be positive")
    return {
        "mode": mode,
        "coordinate_system": "NIfTI_RAS",
        "physical_handle_local_axis": local_axis,
        "coil_180_degree_magnitude_equivalent": equivalent,
        "posterior_weight": posterior_weight,
        "inferior_weight": inferior_weight,
    }


def choose_handle_direction(y_axis, policy: Mapping[str, object]) -> dict:
    """Choose between the simulated handle and its 180-degree alternative."""
    y_axis = np.asarray(y_axis, dtype=float)
    if y_axis.shape != (3,) or not np.all(np.isfinite(y_axis)):
        raise ValueError("Coil Y axis must be a finite three-vector")
    length = float(np.linalg.norm(y_axis))
    if length <= 0:
        raise ValueError("Coil Y axis has zero length")
    y_axis /= length

    # SimNIBS coil meshes used here extend the physical handle along local -Y.
    simulated_handle = -y_axis
    preference = np.array([
        0.0,
        -float(policy["posterior_weight"]),
        -float(policy["inferior_weight"]),
    ])
    current_score = float(np.dot(simulated_handle, preference))
    alternate_score = -current_score
    flip = bool(
        policy["mode"] == "prefer_posterior_inferior"
        and alternate_score > current_score + 1e-12)
    prescribed_handle = -simulated_handle if flip else simulated_handle
    return {
        "flip_applied": flip,
        "rotation_about_coil_normal_degrees": 180.0 if flip else 0.0,
        "simulated_physical_handle_direction_ras": simulated_handle.tolist(),
        "prescribed_physical_handle_direction_ras": prescribed_handle.tolist(),
        "simulated_preference_score": current_score,
        "prescribed_preference_score": alternate_score if flip else current_score,
    }


def canonicalize_pose_matrix(matrix, policy: Mapping[str, object]) -> tuple[np.ndarray, dict]:
    """Postmultiply a pose by Rz(180) when the alternative handle is preferred."""
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
        raise ValueError("Pose transform must be a finite 4x4 matrix")
    decision = choose_handle_direction(matrix[:3, 1], policy)
    prescribed = matrix.copy()
    if decision["flip_applied"]:
        local_flip = np.diag([-1.0, -1.0, 1.0, 1.0])
        prescribed = matrix @ local_flip
    if not np.allclose(prescribed[:3, 3], matrix[:3, 3], atol=1e-12):
        raise ValueError("Handle canonicalization changed the coil center")
    if not np.allclose(prescribed[:3, 2], matrix[:3, 2], atol=1e-12):
        raise ValueError("Handle canonicalization changed the coil normal")
    return prescribed, decision


def prescribed_orientation(simulated_degrees: float, flip_applied: bool) -> float:
    """Represent the physical prescription over a full 0-360 degree turn."""
    return float((float(simulated_degrees) + (180.0 if flip_applied else 0.0)) % 360.0)
