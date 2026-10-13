"""Offline, depth-correct pial preview payloads; never calculate dose scores.

Place this module alongside the existing two-stage QC helpers, or include that
directory on PYTHONPATH. The numerical dose lookup remains the source of truth.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path

import nibabel as nib
import numpy as np
from PIL import Image, ImageDraw

from pose_prescription import canonicalize_pose_matrix, resolve_handle_policy


SCHEMA = "tans-dose-hotspot-preview-v1"
BACKGROUND = (255, 255, 255, 255)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def png_uri(array):
    stream = io.BytesIO()
    Image.fromarray(np.asarray(array, dtype=np.uint8)).save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def camera_basis(outward_normal):
    normal = np.asarray(outward_normal, dtype=float)
    if normal.shape != (3,) or not np.all(np.isfinite(normal)):
        raise ValueError("Camera normal must be a finite three-vector")
    length = np.linalg.norm(normal)
    if length <= 0:
        raise ValueError("Camera normal must have nonzero length")
    normal = normal / length
    # The anatomical superior direction stays upward, as in the existing QC.
    up = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(normal, up)) > 0.995:
        up = np.array([0.0, 1.0, 0.0])
    right = np.cross(up, normal)
    right /= np.linalg.norm(right)
    up = np.cross(normal, right)
    return np.column_stack((right, up, normal))


def project(points, origin, basis, bounds, resolution):
    transformed = (np.asarray(points) - origin) @ basis
    low, high = bounds
    pixels = transformed.copy()
    pixels[:, 0] = (transformed[:, 0] - low[0]) / (high[0] - low[0]) * resolution
    pixels[:, 1] = (high[1] - transformed[:, 1]) / (high[1] - low[1]) * resolution
    return pixels


def vertex_shading(points, triangles, basis):
    faces = points[triangles]
    face_normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    normals = np.zeros_like(points)
    for corner in range(3):
        np.add.at(normals, triangles[:, corner], face_normals)
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-15)
    light = basis @ np.array([-0.35, 0.40, 0.85])
    light /= np.linalg.norm(light)
    return np.clip(0.40 + 0.48 * np.maximum(normals @ light, 0), 0.30, 0.92)


def rasterize_cortex(pixels, triangles, fields, labels, shade, resolution):
    """Z-buffer both hemispheres together, interpolating only the visible face.

    Larger camera depth is closer. Missing field values do not remove geometry;
    they only disable the colored threshold overlay on that face.
    """
    shape = (resolution, resolution)
    zbuffer = np.full(shape, -np.inf)
    image_field = np.full(shape, np.nan, dtype=np.float32)
    image_labels = np.zeros(shape, dtype=np.uint8)
    image_gray = np.zeros(shape, dtype=float)
    for triangle in triangles:
        xyz = pixels[triangle]
        xmin, ymin = np.maximum(np.floor(xyz[:, :2].min(axis=0)).astype(int), 0)
        xmax, ymax = np.minimum(np.ceil(xyz[:, :2].max(axis=0)).astype(int), resolution - 1)
        if xmin > xmax or ymin > ymax:
            continue
        a, b, c = xyz
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denominator) < 1e-12:
            continue
        x, y = np.meshgrid(np.arange(xmin, xmax + 1) + .5,
                           np.arange(ymin, ymax + 1) + .5)
        w0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
        w1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
        weights = np.stack((w0, w1, 1 - w0 - w1), axis=-1)
        depth = weights @ xyz[:, 2]
        region = np.s_[ymin:ymax + 1, xmin:xmax + 1]
        visible = (weights.min(axis=-1) >= -1e-8) & (depth > zbuffer[region])
        if not np.any(visible):
            continue
        zbuffer[region][visible] = depth[visible]
        image_gray[region][visible] = (weights @ shade[triangle])[visible]
        chosen = np.argmax(weights, axis=-1)
        image_labels[region][visible] = labels[triangle][chosen][visible]
        values = fields[triangle]
        image_field[region][visible] = (
            (weights @ values)[visible] if np.all(np.isfinite(values)) else np.nan)
    return image_field, image_labels, image_gray, np.isfinite(zbuffer)


def palette_from_image(image, priors=None):
    if priors is not None:
        from render_optimal_pose_qc import priors_palette
        return priors_palette(priors)
    axis = image.header.get_axis(0)
    if not isinstance(axis, nib.cifti2.LabelAxis):
        raise ValueError("A categorical CIFTI label map or an explicit priors file is required")
    return {int(label): (str(value[0]), np.asarray(value[1][:3], dtype=float))
            for label, value in axis.label[0].items() if int(label) > 0}


def load_source(source, subject, priors=None):
    from simulated_pose_geometry import saved_pose_matrix
    from render_optimal_pose_qc import (
        aligned_surface_values, load_coil_mesh, load_gifti_surface,
    )
    root = Path(source["target_root"])
    cfg_path = root / "CoarseRunConfig.json"
    cfg = json.loads(cfg_path.read_text())
    field_path = Path(source["reference_field"])
    benchmark_path = field_path.parent / "Benchmark.json"
    benchmark = json.loads(benchmark_path.read_text())
    if cfg["subject"] != subject or benchmark["subject"] != subject:
        raise ValueError("Dose preview subject does not match the source configuration")
    index = int(source["reference_map_index_zero_based"])
    orientations = np.asarray(benchmark["orientations_degrees"], dtype=float)
    if not 0 <= index < len(orientations) or not np.isclose(
            orientations[index], float(source["selected_pose"]["orientation_degrees"]), atol=1e-8):
        raise ValueError("Dose preview map does not match its selected orientation")
    reference = float(source["reference_didt_a_per_us"])
    if not np.isfinite(reference) or reference <= 0 or not np.isclose(
            reference, float(benchmark["didt_aperus"]) / 1e6, rtol=1e-10):
        raise ValueError("Dose preview reference dI/dt does not match its simulation")
    if int(benchmark["center_id"]) != int(source["selected_pose"]["center_id"]):
        raise ValueError("Dose preview center does not match its selected pose")
    field_image = nib.load(str(field_path))
    if field_image.shape[0] != len(orientations):
        raise ValueError("Dose preview E-field map count does not match the simulation")
    label_path = Path(cfg["network_label_map"])
    labels_image = nib.load(str(label_path))
    palette = palette_from_image(labels_image, priors)
    if any(label > 255 or label < 1 for label in palette):
        raise ValueError("Dose preview supports categorical labels 1-255")
    points_all, triangles_all, fields_all, labels_all, surfaces = [], [], [], [], []
    offset = 0
    structures = {name: (section, model) for name, section, model in
                  field_image.header.get_axis(1).iter_structures()}
    for hemi, key in (("LEFT", "left_pial"), ("RIGHT", "right_pial")):
        surface_path = Path(cfg["surfaces"][key])
        recorded = benchmark["inputs"].get(key)
        if not recorded or Path(recorded).resolve() != surface_path.resolve():
            raise ValueError("Pial surface differs from the simulated surface mapping")
        points, triangles = load_gifti_surface(surface_path)
        structure = f"CIFTI_STRUCTURE_CORTEX_{hemi}"
        section, model = structures[structure]
        vertex_ids = np.asarray(model.vertex, dtype=int)
        if model.nvertices[structure] != len(points) or np.any(vertex_ids < 0):
            raise ValueError("Cortical vertex indexing does not match the pial surface")
        field = np.full(len(points), np.nan)
        # Read just the selected map, without loading every simulation orientation.
        field[vertex_ids] = np.asarray(field_image.dataobj[index, section], dtype=float)
        if np.any(~np.isfinite(field[vertex_ids])) or np.any(field[vertex_ids] < 0):
            raise ValueError("Selected cortical E-field contains invalid magnitudes")
        labels = np.zeros(len(points), dtype=np.uint8)
        raw_labels = aligned_surface_values(labels_image, structure, vertex_ids)
        if np.any(raw_labels != np.round(raw_labels)) or np.any(raw_labels < 0) or np.any(raw_labels > 255):
            raise ValueError("Invalid categorical network labels")
        if any(int(label) not in palette for label in np.unique(raw_labels) if label > 0):
            raise ValueError("A cortical network label is absent from the color palette")
        labels[vertex_ids] = raw_labels.astype(np.uint8)
        points_all.append(points)
        triangles_all.append(triangles + offset)
        fields_all.append(field)
        labels_all.append(labels)
        surfaces.append({"hemisphere": hemi, "path": str(surface_path),
                         "sha256": sha256_file(surface_path), "vertex_count": len(points)})
        offset += len(points)
    simulated = saved_pose_matrix(benchmark, orientations[index])
    prescribed, handle = canonicalize_pose_matrix(simulated, resolve_handle_policy(cfg))
    coil_path = Path(cfg["coil"]).with_suffix(".stl")
    if Path(benchmark["inputs"]["coil"]).resolve() != Path(cfg["coil"]).resolve():
        raise ValueError("Coil model does not match the simulated coil")
    (coil_points, coil_triangles), _ = load_coil_mesh(
        coil_path, prescribed[:3, 3], prescribed[:3, 3] + prescribed[:3, 1],
        -prescribed[:3, 2], float(cfg["distance_mm"]), pose_matrix=prescribed)
    return {
        "points": np.vstack(points_all), "triangles": np.vstack(triangles_all),
        "fields": np.concatenate(fields_all), "labels": np.concatenate(labels_all),
        "palette": palette, "pose": prescribed, "coil_points": coil_points,
        "coil_triangles": coil_triangles, "reference_didt": reference,
        "provenance": {"subject": subject, "network": source["network"],
                       "selection_mode": source["selection_mode"],
                       "field_path": str(field_path), "field_sha256": sha256_file(field_path),
                       "map_index_zero_based": index, "configuration": str(cfg_path),
                       "configuration_sha256": sha256_file(cfg_path), "surfaces": surfaces,
                       "coil_stl": str(coil_path), "coil_stl_sha256": sha256_file(coil_path),
                       "coil_hardware_name": source["calibration"]["coil_hardware_name"],
                       "simulated_pose_matrix": simulated.tolist(),
                       "prescribed_pose_matrix": prescribed.tolist(), "handle_decision": handle,
                       "label_map": str(label_path), "label_map_sha256": sha256_file(label_path),
                       "prior_palette": str(priors) if priors is not None else "CIFTI label table"},
    }


def render_source(source, subject, priors=None, resolution=512):
    from render_optimal_pose_qc import projected_surface_outline_segments
    resolution = int(resolution)
    if not 128 <= resolution <= 1024:
        raise ValueError("Dose preview resolution must be between 128 and 1024")
    data = load_source(source, subject, priors)
    points, triangles = data["points"], data["triangles"]
    origin = data["pose"][:3, 3]
    normal = -data["pose"][:3, 2]
    basis = camera_basis(normal)
    projected = (points - origin) @ basis
    low, high = projected[:, :2].min(axis=0), projected[:, :2].max(axis=0)
    center = (low + high) / 2
    halfwidth = float(np.max(high - low)) * .56
    bounds = (center - halfwidth, center + halfwidth)
    pixels = project(points, origin, basis, bounds, resolution)
    global_peak = int(np.nanargmax(data["fields"]))
    field, labels, gray, occupied = rasterize_cortex(
        pixels, triangles, data["fields"], data["labels"],
        vertex_shading(points, triangles, basis), resolution)
    base = np.empty((resolution, resolution, 4), dtype=np.uint8)
    base[:] = BACKGROUND
    base[occupied, :3] = np.round(gray[occupied, None] * 255).astype(np.uint8)
    network = np.zeros_like(base)
    visible_labels = set(int(value) for value in np.unique(labels) if value > 0)
    palette = []
    for label, (name, rgb) in data["palette"].items():
        if label not in visible_labels:
            continue
        color = np.round(np.asarray(rgb) * 255).astype(np.uint8)
        network[labels == label] = np.r_[color, 255]
        palette.append({"label": int(label), "name": name,
                        "color": "#" + "".join(f"{value:02x}" for value in color)})
    outline = Image.new("RGBA", (resolution, resolution), (0, 0, 0, 0))
    segments = projected_surface_outline_segments(
        data["coil_points"], data["coil_triangles"], normal, resolution=768)
    draw = ImageDraw.Draw(outline)
    for segment in segments:
        xy = project(segment, origin, basis, bounds, resolution)[:, :2]
        draw.line([tuple(point) for point in xy], fill=(25, 32, 42, 210), width=2)
    data["provenance"].update({
        "camera": "orthographic along prescribed coil outward normal; superior-up",
        "camera_basis_ras": basis.tolist(), "camera_origin_ras": origin.tolist(),
        "view_bounds_mm": [bound.tolist() for bound in bounds],
        "bilateral_shared_depth_buffer": True, "fields_recomputed": False,
        "field_sampling": "linear barycentric interpolation on visible pial triangles",
        "categorical_sampling": "nearest barycentric triangle vertex; no color blending",
        "missing_field_policy": "retain opaque gray cortex; never show threshold overlay",
        "occlusion_policy": "only nearest pial face contributes field and network pixels",
        "visible_cortex_pixels": int(np.count_nonzero(occupied)),
        "finite_field_pixels": int(np.count_nonzero(np.isfinite(field))),
        "view_approximation_only": True,
    })
    return {
        "schema_version": SCHEMA, "width": resolution, "height": resolution,
        "focus_pixel": pixels[global_peak, :2].tolist(),
        "focus_kind": "projected global peak of recorded bilateral cortical reference field",
        "peak_reference_efield_v_per_m": float(data["fields"][global_peak]),
        "pixel_size_mm": (2 * halfwidth) / resolution,
        "base_gray_png": png_uri(base), "network_png": png_uri(network),
        "coil_outline_png": png_uri(outline),
        "field_f32le_base64": base64.b64encode(field.astype("<f4").tobytes()).decode("ascii"),
        "network_index_u8_base64": base64.b64encode(labels.tobytes()).decode("ascii"),
        "reference_didt_a_per_us": data["reference_didt"], "palette": palette,
        "threshold_rule": "finite(E_reference) and E_reference * treatment_didt / reference_didt >= absolute_threshold",
        "annotation": "Modeled pial hotspot preview. Surface interpolation is for display only; numerical scores use the original cortical samples. Hidden cortex is not visible in this view.",
        "provenance": data["provenance"],
    }


def attach_hotspot_previews(lookup, priors=None, resolution=512):
    """Return a shallow copy with preview payloads; never mutate input scores."""
    previews = {name: render_source(source, lookup["subject"], priors, resolution)
                for name, source in lookup["treatment_sources"].items()}
    return {**lookup, "hotspot_previews": previews}


def decoded_png(uri):
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGBA"))


def preview_image(payload, absolute_threshold, treatment_didt):
    """Reference implementation of the HTML canvas threshold operation."""
    threshold, didt = float(absolute_threshold), float(treatment_didt)
    if not np.isfinite(threshold) or threshold < 0 or not np.isfinite(didt) or didt < 0:
        raise ValueError("Preview threshold and dI/dt must be finite and nonnegative")
    shape = (payload["height"], payload["width"])
    field = np.frombuffer(base64.b64decode(payload["field_f32le_base64"]), dtype="<f4").reshape(shape)
    output, colored = decoded_png(payload["base_gray_png"]).copy(), decoded_png(payload["network_png"])
    active = np.isfinite(field) & (field.astype(float) * didt / payload["reference_didt_a_per_us"] >= threshold)
    active &= colored[:, :, 3] > 0
    output[active] = colored[active]
    return Image.alpha_composite(Image.fromarray(output), Image.fromarray(decoded_png(payload["coil_outline_png"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lookup", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--priors", type=Path)
    parser.add_argument("--resolution", type=int, default=512)
    parser.add_argument("--rmt", type=float, default=45)
    args = parser.parse_args()
    original = json.loads(args.lookup.read_text())
    enriched = attach_hotspot_previews(original, args.priors, args.resolution)
    args.output.mkdir(parents=True, exist_ok=True)
    entry = next(row for row in original["entries"] if row["rmt_mso"] == args.rmt)
    for name, payload in enriched["hotspot_previews"].items():
        output = args.output / f"{name}_HotspotPreview.json"
        output.write_text(json.dumps(payload, separators=(",", ":")))
        selected = entry["treatments"][name]
        if selected:
            preview_image(payload, entry["absolute_threshold_v_per_m"],
                          selected["treatment_didt_a_per_us"]).save(args.output / f"{name}_HotspotPreview.png")
        print(json.dumps({"target": name, "payload_bytes": output.stat().st_size,
                          "reference_rmt_mso": args.rmt, "selected_dose": selected,
                          "preview": str(output)}, indent=2))


if __name__ == "__main__":
    main()
