"""Require a nominal coil's inward axis to enter the configured cortical zone."""

import hashlib
import json
from pathlib import Path

import nibabel as nib
import numpy as np

from simulated_pose_geometry import saved_pose_matrix


METHOD = "coil_axis_first_pial_intersection_v1"
MASK_THRESHOLD = 0.5


def resolve_coil_center_constraint(config, target=None):
    """Target settings override study settings; omission means enabled."""
    options = {"enabled": True}
    for source in (config, target or {}):
        supplied = source.get("coil_center_constraint", {})
        if not isinstance(supplied, dict) or set(supplied) - {"enabled"}:
            raise ValueError("coil_center_constraint accepts only the boolean option enabled")
        options.update(supplied)
    if not isinstance(options["enabled"], bool):
        raise ValueError("coil_center_constraint.enabled must be a boolean")
    return options


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def first_ray_hit(origin, direction, triangle_points):
    """Nearest forward ray/triangle intersection, including either face winding."""
    origin = np.asarray(origin, dtype=float)
    direction = np.asarray(direction, dtype=float)
    length = np.linalg.norm(direction)
    if (origin.shape != (3,) or direction.shape != (3,) or
            not np.isfinite(origin).all() or not np.isfinite(direction).all() or
            length <= 0):
        raise ValueError("Coil ray requires a finite origin and nonzero direction")
    direction = direction / length
    triangles = np.asarray(triangle_points, dtype=float)
    edge1 = triangles[:, 1] - triangles[:, 0]
    edge2 = triangles[:, 2] - triangles[:, 0]
    cross = np.cross(direction, edge2)
    determinant = np.einsum("ij,ij->i", edge1, cross)
    valid = np.abs(determinant) > 1e-12
    inverse = np.zeros(len(triangles))
    inverse[valid] = 1.0 / determinant[valid]
    offset = origin - triangles[:, 0]
    u = np.einsum("ij,ij->i", offset, cross) * inverse
    q = np.cross(offset, edge1)
    v = (q @ direction) * inverse
    distance = np.einsum("ij,ij->i", edge2, q) * inverse
    tolerance = 1e-9
    valid &= ((u >= -tolerance) & (v >= -tolerance) &
              (u + v <= 1 + tolerance) & (distance > tolerance))
    if not valid.any():
        return None
    index = int(np.argmin(np.where(valid, distance, np.inf)))
    weights = np.clip([1.0 - u[index] - v[index], u[index], v[index]], 0, 1)
    weights /= weights.sum()
    return {"triangle_index": index, "distance_mm": float(distance[index]),
            "xyz": origin + distance[index] * direction, "weights": weights}


class CoilCenterConstraint:
    def __init__(self, config):
        self.enabled = resolve_coil_center_constraint(config)["enabled"]
        self.provenance = {"enabled": self.enabled, "method": METHOD,
                           "mask_threshold": MASK_THRESHOLD}
        self._cache = {}
        if self.enabled:
            path = config.get("scoring", {}).get("anatomical_mask")
            if not path:
                raise ValueError("Enabled coil_center_constraint requires scoring.anatomical_mask")
            image = nib.load(str(path))
            values = np.asarray(image.get_fdata(), dtype=float)
            if (values.ndim != 2 or values.shape[0] != 1 or
                    not np.isfinite(values).all() or
                    not np.isin(values, [0.0, 1.0]).all() or not values.any()):
                raise ValueError("Coil-center anatomical mask must be nonempty, explicitly binary, and single-map")
            axis = image.header.get_axis(1)
            self.provenance["anatomical_mask"] = str(Path(path).resolve())
            self.provenance["anatomical_mask_sha256"] = file_hash(path)
            self.provenance["surfaces"] = {}
            triangles_xyz, masks, hemispheres, face_ids = [], [], [], []
            for hemi, prefix, structure in (
                    ("L", "left", "CIFTI_STRUCTURE_CORTEX_LEFT"),
                    ("R", "right", "CIFTI_STRUCTURE_CORTEX_RIGHT")):
                surface_path = config.get("surfaces", {}).get(prefix + "_pial")
                if not surface_path:
                    raise ValueError("Coil-center constraint requires both native pial surfaces")
                surface = nib.load(str(surface_path))
                points = np.asarray(surface.get_arrays_from_intent(1008)[0].data, dtype=float)
                faces = np.asarray(surface.get_arrays_from_intent(1009)[0].data, dtype=int)
                if (not np.isfinite(points).all() or not len(faces) or
                        faces.min() < 0 or faces.max() >= len(points)):
                    raise ValueError("Invalid native pial geometry for coil-center constraint")
                mask = np.zeros(len(points))
                for name, data_slice, model in axis.iter_structures():
                    if name == structure:
                        if model.nvertices[structure] != len(points):
                            raise ValueError("Coil-center mask and native pial vertex counts differ")
                        mask[np.asarray(model.vertex, dtype=int)] = values[0, data_slice]
                triangles_xyz.append(points[faces])
                masks.append(mask[faces])
                hemispheres.extend([hemi] * len(faces))
                face_ids.extend(range(len(faces)))
                self.provenance["surfaces"][hemi] = {
                    "path": str(Path(surface_path).resolve()), "sha256": file_hash(surface_path)}
            self._triangles = np.concatenate(triangles_xyz)
            self._masks = np.concatenate(masks)
            if not self._masks.any():
                raise ValueError("Coil-center mask has no cortical support on the supplied surfaces")
            self._hemispheres = hemispheres
            self._face_ids = face_ids
        self.signature = hashlib.sha256(json.dumps(
            self.provenance, sort_keys=True).encode()).hexdigest()
        self.provenance["signature"] = self.signature

    def evaluate(self, matrix):
        row = {"coil_center_constraint_enabled": self.enabled,
               "coil_center_constraint_signature": self.signature,
               "passes_coil_center_constraint": True,
               "coil_axis_hit_status": "disabled",
               "coil_axis_hit_hemisphere": "", "coil_axis_hit_triangle": "",
               "coil_axis_hit_x": "", "coil_axis_hit_y": "", "coil_axis_hit_z": "",
               "coil_axis_hit_distance_mm": "", "coil_axis_hit_mask_value": ""}
        if not self.enabled:
            return row
        matrix = np.asarray(matrix, dtype=float)
        key = matrix[:3, [2, 3]].tobytes()
        if key in self._cache:
            return self._cache[key].copy()
        hit = first_ray_hit(matrix[:3, 3], matrix[:3, 2], self._triangles)
        row["passes_coil_center_constraint"] = False
        row["coil_axis_hit_status"] = "no_pial_intersection"
        if hit is not None:
            index = hit["triangle_index"]
            mask_value = float(hit["weights"] @ self._masks[index])
            inside = mask_value >= MASK_THRESHOLD
            row.update({
                "passes_coil_center_constraint": bool(inside),
                "coil_axis_hit_status": "inside_anatomical_zone" if inside else "outside_anatomical_zone",
                "coil_axis_hit_hemisphere": self._hemispheres[index],
                "coil_axis_hit_triangle": self._face_ids[index],
                "coil_axis_hit_distance_mm": hit["distance_mm"],
                "coil_axis_hit_mask_value": mask_value,
                **{f"coil_axis_hit_{axis}": float(value) for axis, value in zip("xyz", hit["xyz"])},
            })
        self._cache[key] = row.copy()
        return row

    def evaluate_saved(self, result, center_id, orientation):
        if not self.enabled:
            return self.evaluate(None)
        benchmark = Path(result) / f"Center_{int(center_id):05d}" / "Benchmark.json"
        return self.evaluate(saved_pose_matrix(json.loads(benchmark.read_text()), float(orientation)))


def validate_reused_constraint(config, result, best_path=None, *, check_winner=True):
    """Do not silently reuse selections produced without the current geometry gate."""
    constraint = CoilCenterConstraint(config)
    if not constraint.enabled:
        return
    result = Path(result)
    record = result / "CoilCenterConstraint.json"
    if (not record.is_file() or
            json.loads(record.read_text()).get("signature") != constraint.signature):
        raise ValueError("Completed search lacks the current coil-center constraint check; replan/rescore into a new output root")
    if not check_winner:
        return
    best = json.loads(Path(best_path or result / "BestCoarsePose.json").read_text())
    check = constraint.evaluate_saved(result, best["center_id"], best["orientation_degrees"])
    if not check["passes_coil_center_constraint"]:
        raise ValueError("Selected coil center is outside the configured anatomical zone")
