#!/usr/bin/env python3
"""Render QC for an optimal two-stage TANS pose without changing its data."""
import argparse
import csv
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_tans_qc")
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams.update({
    "font.family": "Arial",
    "font.sans-serif": ["Arial"],
})
import matplotlib.pyplot as plt
from matplotlib import cm, colors
from matplotlib import patheffects
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection
import nibabel as nib
import numpy as np
from PIL import Image, ImageDraw
from scipy.io import loadmat
import trimesh

from pose_prescription import canonicalize_pose_matrix, prescribed_orientation, resolve_handle_policy
from simulated_pose_geometry import saved_pose_matrix

from run_simulations import matlab_prctile


PAPER_BG = "#f7f8fa"
INK = "#17202a"
TARGET_EDGE = "#ffffff"
COIL_AXIS_OUTWARD_MM = 20.0
COIL_AXIS_INWARD_MM = 40.0


def require_file(path, label):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def load_gifti_surface(path):
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


def load_gifti_metric(path):
    image = nib.load(str(path))
    if not image.darrays:
        raise ValueError(f"GIFTI metric contains no data arrays: {path}")
    return np.asarray(image.darrays[0].data, dtype=float).reshape(-1)


def vertex_cluster(points, triangles, voxel_mm):
    """Deterministic visualization-only surface simplification."""
    keys = np.floor((points - points.min(axis=0)) / voxel_mm).astype(np.int64)
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    counts = np.bincount(inverse)
    reduced = np.column_stack([
        np.bincount(inverse, weights=points[:, column]) / counts
        for column in range(3)])
    faces = inverse[triangles]
    faces = faces[(faces[:, 0] != faces[:, 1]) &
                  (faces[:, 1] != faces[:, 2]) &
                  (faces[:, 2] != faces[:, 0])]
    faces = np.unique(np.sort(faces, axis=1), axis=0)
    return reduced, faces


def cifti_structure(image, structure, map_index=0):
    axis = image.header.get_axis(1)
    data = np.asarray(image.get_fdata(), dtype=float)
    for name, data_slice, model in axis.iter_structures():
        if name == structure:
            return np.asarray(model.vertex, dtype=np.int64), data[map_index, data_slice]
    raise ValueError(f"CIFTI does not contain {structure}: {image.get_filename()}")


def aligned_surface_values(image, structure, vertex_ids, map_index=0):
    source_vertices, source_values = cifti_structure(image, structure, map_index)
    lookup = np.full(int(max(source_vertices.max(), vertex_ids.max())) + 1, np.nan)
    lookup[source_vertices] = source_values
    values = lookup[vertex_ids]
    if np.any(~np.isfinite(values)):
        raise ValueError(f"CIFTI cortical indexing mismatch for {structure}")
    return values


def priors_palette(path):
    priors = loadmat(path, squeeze_me=True, struct_as_record=False)["Priors"]
    labels = [str(value) for value in np.atleast_1d(priors.NetworkLabels)]
    rgb = np.asarray(priors.NetworkColors, dtype=float)
    return {index + 1: (labels[index], rgb[index]) for index in range(len(labels))}


def load_coil_mesh(stl_path, center, y_direction, outward_normal, distance_mm,
                   pose_matrix=None):
    coil = trimesh.load_mesh(str(stl_path), process=False)
    if isinstance(coil, trimesh.Scene):
        coil = trimesh.util.concatenate(tuple(coil.geometry.values()))
    center = np.asarray(center, dtype=float)
    normal = np.asarray(outward_normal, dtype=float)
    normal /= np.linalg.norm(normal)
    z_axis = -normal
    y_axis = np.asarray(y_direction, dtype=float) - center
    y_axis -= z_axis * np.dot(y_axis, z_axis)
    y_axis /= np.linalg.norm(y_axis)
    x_axis = np.cross(y_axis, z_axis)
    affine = np.eye(4)
    affine[:3, 0] = x_axis
    affine[:3, 1] = y_axis
    affine[:3, 2] = z_axis
    affine[:3, 3] = center + distance_mm * normal
    if pose_matrix is not None:
        affine = np.asarray(pose_matrix, dtype=float)
    vertices = np.c_[np.asarray(coil.vertices), np.ones(len(coil.vertices))]
    transformed = (affine @ vertices.T).T[:, :3]
    return vertex_cluster(transformed, np.asarray(coil.faces), 1.2), affine


def surface_collection(ax, points, triangles, facecolors, alpha=1.0,
                       edgecolor="none", linewidth=0.0, zorder=1):
    collection = Poly3DCollection(points[triangles], linewidths=linewidth,
                                  edgecolors=edgecolor, zorder=zorder)
    collection.set_antialiased(False)
    collection.set_facecolor(facecolors)
    collection.set_alpha(alpha)
    ax.add_collection3d(collection)
    return collection


def gray_surface(ax, points, triangles, color="#aeb5bd", alpha=1.0):
    light = np.array([-0.4, -0.8, 0.9])
    a, b, c = points[triangles[:, 0]], points[triangles[:, 1]], points[triangles[:, 2]]
    normal = np.cross(b - a, c - a)
    length = np.linalg.norm(normal, axis=1)
    length[length == 0] = 1
    normal /= length[:, None]
    intensity = np.clip(0.58 + 0.35 * (normal @ (light / np.linalg.norm(light))), 0.25, 1)
    base = np.asarray(colors.to_rgb(color))
    rgba = np.c_[np.clip(base[None, :] * intensity[:, None], 0, 1), np.ones(len(triangles))]
    return surface_collection(ax, points, triangles, rgba, alpha=alpha)


def data_surface(ax, points, triangles, values, cmap, norm, background="#aeb5bd",
                 alpha=1.0, mask=None):
    triangle_values = values[triangles]
    counts = np.sum(np.isfinite(triangle_values), axis=1)
    face_values = np.full(len(triangles), np.nan)
    valid = counts > 0
    face_values[valid] = np.nansum(triangle_values[valid], axis=1) / counts[valid]
    rgba = np.tile(np.r_[colors.to_rgb(background), 1.0], (len(triangles), 1))
    if mask is None:
        active = np.isfinite(face_values)
    else:
        active = np.sum(mask[triangles], axis=1) >= 2
    rgba[active] = cmap(norm(face_values[active]))
    return surface_collection(ax, points, triangles, rgba, alpha=alpha)


def combined_cortex_surface(ax, surfaces, norm, palette=None):
    """Sort both hemispheres in a single opaque collection, without duplicate faces."""
    points, triangles, values = [], [], []
    offset = 0
    for xyz, faces, data in surfaces:
        points.append(xyz)
        triangles.append(faces + offset)
        values.append(data)
        offset += len(xyz)
    xyz, faces, data = np.vstack(points), np.vstack(triangles), np.concatenate(values)
    if palette is None:
        collection = data_surface(ax, xyz, faces, data, cm.turbo, norm)
    else:
        collection = network_surface(ax, xyz, faces, data, data > 0, palette)
    collection.set_zorder(2)
    return collection


def network_surface(ax, points, triangles, labels, hotspot, palette,
                    anatomy_values=None):
    if anatomy_values is None:
        rgba = np.tile(
            np.r_[colors.to_rgb("#aeb5bd"), 1.0], (len(triangles), 1))
    else:
        triangle_anatomy = np.nanmean(anatomy_values[triangles], axis=1)
        finite = np.isfinite(triangle_anatomy)
        low, high = np.nanpercentile(triangle_anatomy[finite], [2, 98])
        scaled = np.full(len(triangles), 0.5)
        if high > low:
            scaled[finite] = np.clip(
                (triangle_anatomy[finite] - low) / (high - low), 0.0, 1.0)
        shade = 0.55 + 0.30 * scaled
        rgba = np.c_[shade, shade, shade, np.ones(len(triangles))]
    active = np.sum(hotspot[triangles], axis=1) >= 2
    for face_index in np.flatnonzero(active):
        values = labels[triangles[face_index]][hotspot[triangles[face_index]]]
        values = values[values > 0].astype(int)
        if len(values):
            label = int(np.bincount(values).argmax())
            rgba[face_index, :3] = palette[label][1]
    return surface_collection(ax, points, triangles, rgba)


def sulcal_facecolors(triangles, anatomy_values):
    """Return a stable grayscale anatomical background for an inflated mesh."""
    triangle_anatomy = np.nanmean(anatomy_values[triangles], axis=1)
    finite = np.isfinite(triangle_anatomy)
    low, high = np.nanpercentile(triangle_anatomy[finite], [2, 98])
    scaled = np.full(len(triangles), 0.5)
    if high > low:
        scaled[finite] = np.clip(
            (triangle_anatomy[finite] - low) / (high - low), 0.0, 1.0)
    shade = 0.55 + 0.30 * scaled
    return np.c_[shade, shade, shade, np.ones(len(triangles))]


def weighted_target_facecolors(triangles, target_weights, anatomy_values,
                               target_color):
    """Blend native 0-1 target weights over sulcal anatomy without renormalizing."""
    rgba = sulcal_facecolors(triangles, anatomy_values)
    triangle_weights = np.nanmean(target_weights[triangles], axis=1)
    triangle_weights = np.clip(triangle_weights, 0.0, 1.0)
    active = triangle_weights > 0.0
    blend = triangle_weights[active, None]
    canonical = np.asarray(colors.to_rgb(target_color), dtype=float)
    rgba[active, :3] = (
        (1.0 - blend) * rgba[active, :3] + blend * canonical[None, :])
    return rgba


def weighted_target_surface(ax, points, triangles, target_weights,
                            anatomy_values, target_color):
    rgba = weighted_target_facecolors(
        triangles, target_weights, anatomy_values, target_color)
    return surface_collection(ax, points, triangles, rgba)


def draw_peak_marker(ax, center, style):
    """Draw a neutral marker that remains distinct from canonical network colors."""
    common = dict(depthshade=False, clip_on=False)
    if style == "bullseye":
        ax.scatter(*center, s=290, facecolors="#ffffff", edgecolors="#ffffff",
                   linewidths=2.5, zorder=20, **common)
        ax.scatter(*center, s=220, facecolors="none", edgecolors=INK,
                   linewidths=2.3, zorder=21, **common)
        ax.scatter(*center, s=92, facecolors="#ffffff", edgecolors=INK,
                   linewidths=1.4, zorder=22, **common)
        ax.scatter(*center, s=20, facecolors=INK, edgecolors="none",
                   zorder=23, **common)
    elif style == "x":
        ax.scatter(*center, s=300, marker="X", facecolors="#ffffff",
                   edgecolors="#ffffff", linewidths=3.2, zorder=20, **common)
        ax.scatter(*center, s=185, marker="X", facecolors=INK,
                   edgecolors="#c8cdd2", linewidths=1.0, zorder=21, **common)
    elif style == "crosshair":
        ax.scatter(*center, s=250, marker="o", facecolors="none",
                   edgecolors="#ffffff", linewidths=4.2, zorder=20, **common)
        ax.scatter(*center, s=205, marker="o", facecolors="none",
                   edgecolors=INK, linewidths=1.8, zorder=21, **common)
        ax.scatter(*center, s=210, marker="+", color=INK,
                   linewidths=2.6, zorder=22, **common)
    else:
        raise ValueError(f"Unsupported peak marker: {style}")


def coil_axis_geometry(pose_matrix):
    """Use the saved coil origin and local +Z (inward) axis in native RAS mm."""
    affine = np.asarray(pose_matrix, dtype=float)
    center = affine[:3, 3].copy()
    inward = affine[:3, 2].copy()
    inward /= np.linalg.norm(inward)
    segment = np.asarray([
        center - COIL_AXIS_OUTWARD_MM * inward,
        center + COIL_AXIS_INWARD_MM * inward,
    ])
    return center, inward, segment


def draw_coil_placement(ax, context, technician=False):
    """Keep the coil center and its geometric normal distinct from the peak."""
    center = context["coil_center"]
    if not technician:
        segment = context["coil_axis_segment"]
        line, = ax.plot(*segment.T, color=INK, linewidth=1.8,
                        linestyle=(0, (1, 2.2)), dash_capstyle="round",
                        zorder=26)
        line.set_path_effects([
            patheffects.Stroke(linewidth=4.0, foreground="white"),
            patheffects.Normal(),
        ])
    size = 230 if technician else 150
    for color, width, order in (("white", 5.2, 27), (INK, 2.4, 28)):
        ax.scatter(*center, s=size, marker="+", color=color,
                   linewidths=width, depthshade=False, clip_on=False,
                   zorder=order)


def format_3d(ax, all_points, title=None, crop=None, view=(7, 180),
              equal_limits=True, zoom=1.0):
    ax.set_facecolor(PAPER_BG)
    ax.set_proj_type("ortho")
    ax.view_init(elev=float(view[0]), azim=float(view[1]))
    if crop is None:
        low, high = all_points.min(axis=0), all_points.max(axis=0)
    else:
        low, high = np.asarray(crop[0]), np.asarray(crop[1])
    span = np.maximum(high - low, 1.0)
    center = (low + high) / 2.0
    if equal_limits:
        radius = np.max(span) / 2.0
        ax.set_xlim(center[0] - radius, center[0] + radius)
        ax.set_ylim(center[1] - radius, center[1] + radius)
        ax.set_zlim(center[2] - radius, center[2] + radius)
        ax.set_box_aspect((1, 1, 1), zoom=zoom)
    else:
        padding = 0.035 * span
        ax.set_xlim(low[0] - padding[0], high[0] + padding[0])
        ax.set_ylim(low[1] - padding[1], high[1] + padding[1])
        ax.set_zlim(low[2] - padding[2], high[2] + padding[2])
        ax.set_box_aspect(span, zoom=zoom)
    ax.axis("off")
    if title:
        ax.set_title(title, fontsize=15, fontweight="bold", color=INK, pad=2)


def vertex_normal(points, triangles, vertex_id):
    adjacent = triangles[np.any(triangles == int(vertex_id), axis=1)]
    if not len(adjacent):
        raise ValueError(f"No triangles touch peak vertex {vertex_id}")
    a, b, c = points[adjacent[:, 0]], points[adjacent[:, 1]], points[adjacent[:, 2]]
    normals = np.cross(b - a, c - a)
    normal = normals.sum(axis=0)
    length = np.linalg.norm(normal)
    if length == 0:
        raise ValueError(f"Could not estimate normal at peak vertex {vertex_id}")
    normal /= length
    if np.dot(normal, points[vertex_id] - points.mean(axis=0)) < 0:
        normal *= -1.0
    return normal


def view_from_direction(direction):
    direction = np.asarray(direction, dtype=float)
    direction /= np.linalg.norm(direction)
    elev = np.degrees(np.arcsin(np.clip(direction[2], -1.0, 1.0)))
    azim = np.degrees(np.arctan2(direction[1], direction[0]))
    return float(elev), float(azim)


def mask_boundary_segments(points, triangles, mask):
    """Return within-triangle contour segments for a binary vertex mask."""
    triangle_mask = mask[triangles]
    mixed = triangles[(triangle_mask.sum(axis=1) > 0) &
                      (triangle_mask.sum(axis=1) < 3)]
    segments = []
    for triangle in mixed:
        crossings = []
        for first, second in ((triangle[0], triangle[1]),
                              (triangle[1], triangle[2]),
                              (triangle[2], triangle[0])):
            if bool(mask[first]) != bool(mask[second]):
                crossings.append((points[first] + points[second]) / 2.0)
        if len(crossings) == 2:
            segments.append(crossings)
    return np.asarray(segments, dtype=float)


def draw_target_outline(ax, points, triangles, target_mask, halo=True):
    segments = mask_boundary_segments(points, triangles, target_mask)
    if not len(segments):
        return
    if halo:
        halo_collection = Line3DCollection(
            segments, colors="#26313a", linewidths=1.8, zorder=17)
        ax.add_collection3d(halo_collection)
    outline = Line3DCollection(segments, colors=TARGET_EDGE, linewidths=1.15,
                               zorder=18)
    ax.add_collection3d(outline)


def draw_black_target_outline(ax, points, triangles, target_mask):
    """Draw an unambiguous target-support border for probability-map views."""
    segments = mask_boundary_segments(points, triangles, target_mask)
    if not len(segments):
        return
    outline = Line3DCollection(
        segments, colors=INK, linewidths=1.65, zorder=18)
    ax.add_collection3d(outline)


def draw_hotspot_outline(ax, points, triangles, hotspot_mask):
    """Draw a high-contrast boundary for the complete suprathreshold E-field."""
    segments = mask_boundary_segments(points, triangles, hotspot_mask)
    if not len(segments):
        return
    outer = Line3DCollection(
        segments, colors="#20252a", linewidths=2.5, zorder=17)
    inner = Line3DCollection(
        segments, colors="#ffffff", linewidths=1.25, zorder=18)
    ax.add_collection3d(outer)
    ax.add_collection3d(inner)


def crop_from_points(points, margin_mm):
    points = np.asarray(points, dtype=float)
    if not len(points):
        raise ValueError("Cannot define a crop from an empty point set")
    return points.min(axis=0) - margin_mm, points.max(axis=0) + margin_mm


def projected_surface_outline_segments(points, triangles, view_direction,
                                       resolution=640):
    """Rasterize the projected mesh and return only its visible perimeter."""
    direction = np.asarray(view_direction, dtype=float)
    direction /= np.linalg.norm(direction)
    helper = np.asarray([0.0, 0.0, 1.0])
    if abs(np.dot(helper, direction)) > 0.9:
        helper = np.asarray([0.0, 1.0, 0.0])
    horizontal = np.cross(helper, direction)
    horizontal /= np.linalg.norm(horizontal)
    vertical = np.cross(direction, horizontal)
    origin = points.mean(axis=0)
    centered = points - origin
    projected = np.c_[centered @ horizontal, centered @ vertical]
    low = projected.min(axis=0)
    high = projected.max(axis=0)
    padding = np.maximum((high - low) * 0.025, 0.5)
    low -= padding
    high += padding

    width = height = int(resolution)
    pixels = np.empty_like(projected)
    pixels[:, 0] = (projected[:, 0] - low[0]) / (high[0] - low[0]) * (width - 1)
    pixels[:, 1] = (high[1] - projected[:, 1]) / (high[1] - low[1]) * (height - 1)
    raster = Image.new("L", (width, height), 0)
    drawer = ImageDraw.Draw(raster)
    for triangle in triangles:
        drawer.polygon([tuple(pixels[index]) for index in triangle], fill=255)
    occupied = np.asarray(raster, dtype=float) / 255.0

    contour_figure, contour_axis = plt.subplots(figsize=(1, 1), dpi=50)
    x_values = np.linspace(low[0], high[0], width)
    y_values = np.linspace(high[1], low[1], height)
    contour_set = contour_axis.contour(
        x_values, y_values, occupied, levels=[0.5])
    paths = [path for path in contour_set.allsegs[0] if len(path) > 2]
    plt.close(contour_figure)
    if not paths:
        raise ValueError("Could not determine projected coil perimeter")

    front_depth = np.max(centered @ direction) + 0.75
    segments = []
    for path in paths:
        path_3d = (origin + path[:, :1] * horizontal + path[:, 1:] * vertical +
                   front_depth * direction)
        segments.extend(np.stack((path_3d[:-1], path_3d[1:]), axis=1))
    return np.asarray(segments)


def triangles_in_crop(points, triangles, crop):
    low, high = np.asarray(crop[0]), np.asarray(crop[1])
    centroids = points[triangles].mean(axis=1)
    keep = np.all((centroids >= low) & (centroids <= high), axis=1)
    if not np.any(keep):
        raise ValueError("Surface crop contains no triangles")
    return triangles[keep]


def local_surface_patch(points, triangles, focus_points, margin_mm=10.0):
    center = np.median(focus_points, axis=0)
    radius = np.max(np.linalg.norm(focus_points - center, axis=1)) + margin_mm
    centroids = points[triangles].mean(axis=1)
    keep = np.linalg.norm(centroids - center, axis=1) <= radius
    local = triangles[keep]
    if not len(local):
        raise ValueError("Local surface patch contains no triangles")
    local_points = points[np.unique(local)]
    crop = (local_points.min(axis=0) - 1.0, local_points.max(axis=0) + 1.0)
    return local, crop


def composition_rows(field_by_hemi, area_by_hemi, label_by_hemi, percentiles):
    all_field = np.concatenate(list(field_by_hemi.values()))
    rows = []
    for percentile, threshold in zip(percentiles, matlab_prctile(all_field, percentiles)):
        area_by_label = {}
        total = 0.0
        for hemi in field_by_hemi:
            hotspot = field_by_hemi[hemi] > threshold
            areas = area_by_hemi[hemi][hotspot]
            labels = label_by_hemi[hemi][hotspot].astype(int)
            total += float(areas.sum())
            for label in np.unique(labels):
                area_by_label[label] = area_by_label.get(label, 0.0) + float(
                    areas[labels == label].sum())
        rows.append((percentile, threshold, total, area_by_label))
    return rows


def weighted_target_score(field_by_hemi, area_by_hemi, target_by_hemi, percentile):
    threshold = matlab_prctile(
        np.concatenate(list(field_by_hemi.values())), [percentile])[0]
    numerator = denominator = 0.0
    for hemi in field_by_hemi:
        hotspot = field_by_hemi[hemi] > threshold
        denominator += float(area_by_hemi[hemi][hotspot].sum())
        numerator += float(np.sum(area_by_hemi[hemi][hotspot] *
                                  target_by_hemi[hemi][hotspot]))
    return (0.0 if denominator == 0 else numerator / denominator), threshold


def weighted_scoring_mask(anatomical_mask, labels, target_label, target_values,
                          restrict_to_binary_support, label_map_available):
    mask = np.asarray(anatomical_mask, dtype=bool).copy()
    if not restrict_to_binary_support:
        return mask
    if label_map_available:
        return mask & (np.asarray(labels) == target_label)
    return mask & (np.asarray(target_values) != 0)


def save_bar_figure(rows, palette, target_label, weighted_score, binary_score, output):
    labels = sorted({label for _, _, _, areas in rows for label in areas if label in palette})
    average_areas = {
        label: float(np.mean([areas.get(label, 0.0) / total if total else 0.0
                              for _, _, total, areas in rows]))
        for label in labels}
    display_rows = [("Average", np.nan, 1.0, average_areas)] + rows
    fig, ax = plt.subplots(figsize=(14, 7), facecolor=PAPER_BG)
    ax.set_facecolor(PAPER_BG)
    y = np.arange(len(display_rows))
    left = np.zeros(len(display_rows))
    for label in labels:
        values = np.array([100 * areas.get(label, 0.0) / total if total else 0
                           for _, _, total, areas in display_rows])
        name, color = palette[label]
        edge = INK if label == target_label else PAPER_BG
        width = 1.4 if label == target_label else 0.35
        ax.barh(y, values, left=left, color=color, edgecolor=edge,
                linewidth=width, height=0.72, label=name)
        left += values
    ax.set_yticks(y, [percentile if isinstance(percentile, str)
                      else f"{percentile:.1f}%"
                      for percentile, _, _, _ in display_rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("% of E-field hotspot cortical surface area", fontsize=13)
    ax.set_ylabel("E-field percentile threshold", fontsize=13)
    ax.set_title(f"99.0-99.9% hotspot network composition\n"
                 f"99.5% restricted weighted on-target = {weighted_score:.3f}; "
                 f"restricted binary on-target = {100 * binary_score:.1f}%",
                 fontsize=17, fontweight="bold", color=INK)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color="#d9dde2", linewidth=0.7, zorder=0)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False,
              fontsize=9)
    fig.tight_layout()
    fig.savefig(output, dpi=220, facecolor=PAPER_BG)
    plt.close(fig)


def draw_threshold_composition(ax, rows, palette, target_label):
    """Draw 99.0-99.9% network composition as narrow stacked columns."""
    labels = sorted({label for _, _, _, areas in rows
                     for label in areas if label in palette and label > 0})
    x = np.arange(len(rows))
    bottom = np.zeros(len(rows), dtype=float)
    for label in labels:
        values = np.asarray([
            100.0 * areas.get(label, 0.0) / total if total else 0.0
            for _, _, total, areas in rows])
        edge = INK if label == target_label else PAPER_BG
        linewidth = 1.1 if label == target_label else 0.25
        ax.bar(x, values, bottom=bottom, width=0.82,
               color=palette[label][1], edgecolor=edge, linewidth=linewidth)
        bottom += values
    percentiles = np.asarray([row[0] for row in rows])
    tick_indices = np.arange(0, len(rows), 2)
    ax.set_xlim(-0.65, len(rows) - 0.35)
    ax.set_ylim(0, 100)
    ax.set_xticks(tick_indices, [f"{percentiles[index]:.1f}"
                                 for index in tick_indices], rotation=90)
    ax.set_yticks([0, 50, 100], ["0", "50", "100%"])
    ax.tick_params(labelsize=6.5, length=2, pad=1)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#8b949e")
    ax.set_title("Hotspot network\ncomposition", fontsize=8.5,
                 fontweight="bold", color=INK, pad=2)
    ax.set_xlabel("E-field percentile", fontsize=7, labelpad=1)


def draw_single_composition(ax, row, palette, target_label):
    """Draw one vertical stacked bar for the displayed hotspot threshold."""
    percentile, _, total, areas = row
    labels = sorted(label for label in areas if label in palette and label > 0)
    bottom = 0.0
    for label in labels:
        value = 100.0 * areas[label] / total if total else 0.0
        edge = INK if label == target_label else PAPER_BG
        linewidth = 1.8 if label == target_label else 0.6
        ax.bar([0], [value], bottom=[bottom], width=0.62,
               color=palette[label][1], edgecolor=edge, linewidth=linewidth)
        bottom += value
    ax.set_xlim(-0.7, 0.7)
    ax.set_ylim(0, 100)
    ax.set_xticks([])
    ax.set_yticks([0, 50, 100], ["0", "50", "100%"])
    ax.tick_params(labelsize=8, length=2, pad=2)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#8b949e")
    ax.set_title(f"Composition\n{percentile:.1f}%", fontsize=10,
                 fontweight="bold", color=INK, pad=4)


def draw_horizontal_composition(ax, row, palette, target_label, title=None):
    """Draw one horizontal stacked bar without changing its axis geometry."""
    ax.set_facecolor(PAPER_BG)
    percentile, _, total, areas = row
    labels = sorted(label for label in areas if label in palette and label > 0)
    left = 0.0
    target_segment = None
    for label in labels:
        value = 100.0 * areas[label] / total if total else 0.0
        edge = INK if label == target_label else PAPER_BG
        linewidth = 1.8 if label == target_label else 0.6
        ax.barh([0], [value], left=[left], height=0.58,
                color=palette[label][1], edgecolor=edge, linewidth=linewidth)
        if label == target_label:
            target_segment = (left, value, palette[label][1])
        left += value
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.7, 0.7)
    ax.set_yticks([])
    ax.set_xticks([0, 25, 50, 75, 100], ["0", "25", "50", "75", "100%"])
    ax.tick_params(labelsize=8, length=2, pad=2)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#8b949e")
    if title is None:
        title = f"Hotspot network composition | {percentile:.1f}% threshold"
    ax.set_title(title, fontsize=10, fontweight="bold", color=INK, pad=3)
    if target_segment is not None:
        segment_left, segment_width, segment_color = target_segment
        rgb = np.asarray(colors.to_rgb(segment_color), dtype=float)
        luminance = float(np.dot(rgb, [0.2126, 0.7152, 0.0722]))
        text_color = INK if luminance > 0.58 else "#ffffff"
        if segment_width >= 8.0:
            ax.text(segment_left + segment_width / 2.0, 0,
                    f"TARGET  {segment_width:.1f}%", ha="center", va="center",
                    fontsize=7.2, fontweight="bold", color=text_color,
                    clip_on=True)
        elif segment_width >= 2.0:
            ax.annotate(
                "TARGET", xy=(segment_left + segment_width / 2.0, 0.28),
                xytext=(segment_left + segment_width / 2.0, 0.57),
                ha="center", va="bottom", fontsize=6.5, fontweight="bold",
                color=INK,
                arrowprops=dict(arrowstyle="-", color=INK, lw=0.8))


def target_weight_colormap(target_color):
    return colors.LinearSegmentedColormap.from_list(
        "native_target_weight", ["#d9dde1", colors.to_hex(target_color)])


def draw_target_weight_scale(ax, target_color, title=None):
    """Draw the fixed native 0-1 scale used by weighted Panel C."""
    ax.set_facecolor(PAPER_BG)
    gradient = np.linspace(0.0, 1.0, 512)[None, :]
    ax.imshow(gradient, extent=(0, 1, -0.25, 0.25), aspect="auto",
              cmap=target_weight_colormap(target_color), vmin=0.0, vmax=1.0)
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.45, 0.45)
    ax.set_yticks([])
    ax.set_xticks([0.0, 0.25, 0.5, 0.75, 1.0],
                  ["0", "0.25", "0.50", "0.75", "1.0"])
    ax.tick_params(labelsize=8, length=2, pad=2)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#8b949e")
    if title is None:
        title = "Fixed eligible-target weight (0-1)"
    ax.set_title(title, fontsize=10, fontweight="bold", color=INK, pad=3)


def draw_network_review_panel(fig, spec, context, row, restricted_weighted,
                              restricted_binary, hotspot, mode,
                              network_reference, panel_label):
    """Draw one synchronized categorical or weighted target-review column."""
    percentile = row[0]
    panel_grid = spec.subgridspec(
        2, 1, height_ratios=[0.82, 0.18], hspace=0.08)
    ax_network = fig.add_subplot(panel_grid[0, 0], projection="3d")
    ax_network.computed_zorder = False
    if mode == "weighted_probability":
        points = context["inflated_points"]
        triangles = context["inflated_review_triangles"]
        peak_point = context["inflated_peak_point"]
        crop = context["inflated_closeup_crop"]
        weighted_target_surface(
            ax_network, points, triangles, context["left_target_weight"],
            context["left_sulc"], context["target_color"])
        if not network_reference:
            draw_hotspot_outline(
                ax_network, points, triangles, hotspot)
    else:
        points = context["left_points"]
        triangles = context["folded_review_triangles"]
        peak_point = context["peak_point"]
        crop = context["folded_closeup_crop"]
        network_mask = (context["left_labels"] > 0
                        if network_reference else hotspot)
        network_surface(
            ax_network, points, triangles, context["left_labels"],
            network_mask, context["palette"],
            anatomy_values=context["left_sulc"])
        draw_target_outline(
            ax_network, points, triangles, context["left_target"],
            halo=network_reference)

    draw_peak_marker(
        ax_network, peak_point,
        context["visualization"]["peak_marker"])
    marker_label = {"bullseye": "bullseye", "x": "X",
                    "crosshair": "crosshair"}[
                        context["visualization"]["peak_marker"]]
    if mode == "weighted_probability" and network_reference:
        title = (
            f"{panel_label}  Weighted target map (fixed)\n"
            "target weights within eligible support")
    elif mode == "weighted_probability":
        title = (
            f"{panel_label}  Weighted target | {percentile:.1f}% E-field border\n"
            f"{marker_label} = peak | weighted on-target "
            f"{restricted_weighted:.3f}")
    elif network_reference:
        title = (
            f"{panel_label}  Folded individualized network organization\n"
            "black/white outline = eligible target")
    else:
        title = (
            f"{panel_label}  Folded hotspot networks | {percentile:.1f}% threshold\n"
            f"{marker_label} = peak | eligible on-target "
            f"{100 * restricted_binary:.1f}%")
    review_zoom = context["visualization"]["review_zoom"]
    format_3d(
        ax_network, points, title, crop=crop, view=context["review_view"],
        equal_limits=False, zoom=review_zoom)
    ax_network.title.set_fontsize(10 if panel_label == "C" else 9.5)

    ax_scale = fig.add_subplot(panel_grid[1, 0])
    if mode == "weighted_probability":
        draw_target_weight_scale(
            ax_scale, context["target_color"],
            title=("Target weight (fixed 0-1)"
                   if panel_label == "D" else None))
    else:
        composition_row = (context["network_reference_row"]
                           if network_reference else row)
        draw_horizontal_composition(
            ax_scale, composition_row, context["palette"],
            context["target_label"],
            title=("Full left-cortex network composition"
                   if network_reference else None))
    return ax_network, ax_scale


def resolve_panel_c_mode(requested, categorical_available, weighted_available):
    """Resolve the adaptive post-TANS review layout from available map types."""
    if requested == "auto":
        if categorical_available and weighted_available:
            return "dual_comparison"
        if categorical_available:
            return "categorical"
        if weighted_available:
            return "weighted_probability"
        raise ValueError(
            "Post-TANS summary requires a categorical network-label map, a "
            "weighted target map, or both")
    if requested in ("dual_comparison", "dual_context_comparison"):
        if not categorical_available or not weighted_available:
            raise ValueError(f"{requested} requires both categorical and weighted maps")
    elif requested == "categorical" and not categorical_available:
        raise ValueError("categorical Panel C requires a network-label map")
    elif requested == "weighted_probability" and not weighted_available:
        raise ValueError("weighted Panel C requires a weighted target map")
    return requested


def visualization_options(cfg, config_path, marker_override, coil_path,
                          panel_c_mode_override=None):
    options = {
        "peak_marker": "bullseye",
        "panel_c_mode": "auto",
        "head_color": "#d8c4a3",
        "head_opacity": 0.18,
        "coil_color": "#3f444a",
        "coil_edge_color": "#20252a",
        "coil_opacity": 0.13,
        "coil_show_mesh_edges": False,
        "brain_color": "#87919b",
        "brain_opacity": 0.76,
        "technician_head_opacity": 0.07,
        "technician_brain_opacity": 0.18,
        "technician_coil_opacity": 0.025,
        "technician_coil_outline_opacity": 0.72,
        "lateral_zoom": 1.30,
        "technician_zoom": 1.10,
        "closeup_zoom": 1.16,
        "full_brain_zoom": 1.30,
        "review_zoom": 0.90,
        "review_view_elevation": 7.0,
        "review_view_azimuth": 180.0,
        "coil_display_name": Path(coil_path).stem.replace("_", " "),
        "threshold_frame_duration_ms": 650,
        "network_reference_frame_duration_ms": 2000,
        "final_frame_duration_ms": 1800,
    }
    options.update(cfg.get("visualization", {}))
    if config_path:
        options.update(json.loads(require_file(
            config_path, "visualization configuration").read_text()))
    # Coil identity is simulation provenance, not a presentation override.
    options["coil_display_name"] = str(cfg.get(
        "coil_hardware_name", Path(coil_path).stem.replace("_", " ")))
    if marker_override:
        options["peak_marker"] = marker_override
    if panel_c_mode_override:
        options["panel_c_mode"] = panel_c_mode_override
    if options["peak_marker"] not in ("bullseye", "x", "crosshair"):
        raise ValueError("visualization peak_marker must be bullseye, x, or crosshair")
    if options["panel_c_mode"] not in (
            "auto", "categorical", "weighted_probability",
            "dual_comparison", "dual_context_comparison"):
        raise ValueError(
            "visualization panel_c_mode must be auto, categorical, "
            "weighted_probability, dual_comparison, or dual_context_comparison")
    for key in ("head_color", "coil_color", "coil_edge_color", "brain_color"):
        colors.to_rgba(options[key])
    for key in ("head_opacity", "coil_opacity", "brain_opacity",
                "technician_head_opacity", "technician_brain_opacity",
                "technician_coil_opacity", "technician_coil_outline_opacity"):
        value = float(options[key])
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"visualization {key} must be between 0 and 1")
        options[key] = value
    for key in ("lateral_zoom", "technician_zoom", "closeup_zoom",
                "full_brain_zoom", "review_zoom"):
        options[key] = float(options[key])
        if options[key] <= 0:
            raise ValueError(f"visualization {key} must be positive")
    for key in ("review_view_elevation", "review_view_azimuth"):
        options[key] = float(options[key])
    return options


def draw_head_scene(ax, context, hotspot, threshold, title, view, crop=None,
                    technician=False, network_reference=False):
    ax.computed_zorder = False
    points = context["left_points"]
    triangles = context["left_triangles"]
    field = context["left_field"]
    options = context["visualization"]
    head_opacity = (options["technician_head_opacity"] if technician
                    else options["head_opacity"])
    coil_opacity = (options["technician_coil_opacity"] if technician
                    else options["coil_opacity"])
    if network_reference:
        # Keep the network reference legible through the head and coil.
        head_opacity *= 0.25
        coil_opacity *= 0.30
    gray_surface(
        ax, context["skin_points"], context["skin_triangles"],
        options["head_color"], alpha=head_opacity)
    surfaces = [(points, triangles, field, context["left_labels"],
                 context["left_target"])]
    if technician:
        surfaces.append((
            context["right_points"], context["right_triangles"],
            context["right_field"], context["right_labels"],
            context["right_target"]))
    combined_cortex_surface(
        ax, [(xyz, faces, labels if network_reference else values)
             for xyz, faces, values, labels, _ in surfaces],
        context['field_norm'], context['palette'] if network_reference else None)
    if network_reference:
        for xyz, faces, _, _, target in surfaces:
            if np.any(target):
                draw_target_outline(ax, xyz, faces, target)
    coil_rgba = np.tile(
        np.r_[colors.to_rgb(options["coil_color"]), 1.0],
        (len(context["coil_triangles"]), 1))
    coil_edge = (options["coil_edge_color"]
                 if bool(options["coil_show_mesh_edges"]) else "none")
    surface_collection(
        ax, context["coil_points"], context["coil_triangles"], coil_rgba,
        alpha=coil_opacity, edgecolor=coil_edge,
        linewidth=0.08 if bool(options["coil_show_mesh_edges"]) else 0.0,
        zorder=5)
    if technician:
        silhouette = Line3DCollection(
            context["coil_silhouette"],
            colors=colors.to_rgba(
                options["coil_edge_color"],
                options["technician_coil_outline_opacity"]),
            linewidths=1.25, zorder=6)
        ax.add_collection3d(silhouette)
    draw_coil_placement(ax, context, technician=technician)
    zoom = (options["technician_zoom"] if technician
            else options["lateral_zoom"])
    format_3d(ax, context["head_points"], title, crop=crop, view=view,
              equal_limits=False, zoom=zoom)
    ax.title.set_fontsize(11)


def draw_field_panel(ax, points, triangles, field, target_mask, norm, title,
                     crop=None, view=(7, 180), equal_limits=True,
                     peak_point=None, peak_marker="bullseye", zoom=1.0):
    ax.computed_zorder = False
    data_surface(ax, points, triangles, field, cm.turbo, norm)
    draw_target_outline(ax, points, triangles, target_mask)
    if peak_point is not None:
        draw_peak_marker(ax, peak_point, peak_marker)
    format_3d(ax, points, title, crop=crop, view=view,
              equal_limits=equal_limits, zoom=zoom)
    ax.title.set_fontsize(10)


def draw_contextual_field_panel(ax, context, inflated, title):
    """Show a cortical E-field within the technician-view head/coil outline."""
    options = context["visualization"]
    if inflated:
        points = context["inflated_points"]
        triangles = context["inflated_triangles"]
        peak_point = context["inflated_peak_point"]
    else:
        points = context["left_points"]
        triangles = context["left_triangles"]
        peak_point = context["peak_point"]
    data_surface(
        ax, points, triangles, context["left_field"], cm.turbo,
        context["field_norm"], alpha=0.98)
    draw_target_outline(
        ax, points, triangles, context["left_target"])
    draw_peak_marker(ax, peak_point, options["peak_marker"])
    gray_surface(
        ax, context["skin_points"], context["skin_triangles"],
        options["head_color"], alpha=options["technician_head_opacity"])
    coil_rgba = np.tile(
        np.r_[colors.to_rgb(options["coil_color"]), 1.0],
        (len(context["coil_triangles"]), 1))
    surface_collection(
        ax, context["coil_points"], context["coil_triangles"], coil_rgba,
        alpha=options["technician_coil_opacity"], zorder=5)
    silhouette = Line3DCollection(
        context["coil_silhouette"],
        colors=colors.to_rgba(
            options["coil_edge_color"],
            options["technician_coil_outline_opacity"]),
        linewidths=1.25, zorder=6)
    ax.add_collection3d(silhouette)
    format_3d(
        ax, context["head_points"], title, crop=context["technician_crop"],
        view=context["coil_view"], equal_limits=False,
        zoom=options["technician_zoom"])
    ax.title.set_fontsize(10)


def draw_weighted_reference_panel(ax, context, title, closeup):
    """Show the fixed native weighted target before the E-field sequence."""
    ax.computed_zorder = False
    points = context["inflated_points"]
    triangles = (context["inflated_review_triangles"] if closeup
                 else context["inflated_triangles"])
    weighted_target_surface(
        ax, points, triangles, context["left_target_weight"],
        context["left_sulc"], context["target_color"])
    draw_black_target_outline(
        ax, points, triangles, context["left_target"])
    if closeup:
        draw_peak_marker(ax, context['inflated_peak_point'],
                         context['visualization']['peak_marker'])
    format_3d(
        ax, points, title,
        crop=context["inflated_closeup_crop"] if closeup else None,
        view=context["review_view"] if closeup else (7, 180),
        equal_limits=closeup,
        zoom=(context["visualization"]["closeup_zoom"] if closeup
              else context["visualization"]["full_brain_zoom"]))
    ax.title.set_fontsize(10)


def figure_image(fig):
    fig.canvas.draw()
    return Image.fromarray(np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy())


def build_review_figure(context, row, restricted_weighted, restricted_binary,
                        network_reference=False):
    percentile, threshold, _, _ = row
    hotspot = np.isfinite(context["left_field"]) & (
        context["left_field"] > threshold)
    panel_c_mode = context["visualization"]["panel_c_mode"]
    dual = panel_c_mode in ("dual_comparison", "dual_context_comparison")
    contextual = panel_c_mode == "dual_context_comparison"

    fig = plt.figure(figsize=(20, 10), dpi=120, facecolor=PAPER_BG)
    outer = fig.add_gridspec(
        1, 3, width_ratios=[0.92, 0.92, 1.84], wspace=0.035,
        left=0.018, right=0.988, bottom=0.145, top=0.91)

    if contextual:
        ax_lateral = fig.add_subplot(outer[0, 0], projection="3d")
        draw_head_scene(
            ax_lateral, context, hotspot, threshold,
            f"A  Lateral overview | {context['coil_name']}", (7, 180))
    else:
        head_grid = outer[0, 0].subgridspec(2, 1, hspace=0.01)
    if not contextual:
        ax_tech = fig.add_subplot(head_grid[0, 0], projection="3d")
        draw_head_scene(
            ax_tech, context, hotspot, threshold,
            "A1  Technician view: directly above coil center",
            context["coil_view"], crop=context["technician_crop"],
            technician=True, network_reference=network_reference)
        ax_lateral = fig.add_subplot(head_grid[1, 0], projection="3d")
        draw_head_scene(
            ax_lateral, context, hotspot, threshold,
            f"A2  Lateral overview | {context['coil_name']}", (7, 180),
            network_reference=network_reference)
    field_grid = outer[0, 1].subgridspec(2, 1, hspace=0.075)
    if contextual:
        ax_folded = fig.add_subplot(field_grid[0, 0], projection="3d")
        draw_contextual_field_panel(
            ax_folded, context, False,
            "B1  Folded E-field within head/coil outline")
        ax_inflated = fig.add_subplot(field_grid[1, 0], projection="3d")
        draw_contextual_field_panel(
            ax_inflated, context, True,
            "B2  Inflated E-field within head/coil outline")
    else:
        ax_inflated = fig.add_subplot(field_grid[0, 0], projection="3d")
        if network_reference:
            draw_weighted_reference_panel(
                ax_inflated, context,
                "B1  Weighted target close-up | black = eligible support",
                closeup=True)
        else:
            draw_field_panel(
                ax_inflated, context["inflated_points"],
                context["inflated_review_triangles"], context["left_field"],
                context["left_target"], context["field_norm"],
                ("B1  Inflated close-up | white = retained eligible target"
                 if context.get("restrict_to_components") else
                 "B1  Inflated close-up | white = eligible target"),
                crop=context["inflated_closeup_crop"], view=context["review_view"],
                peak_point=context["inflated_peak_point"],
                peak_marker=context["visualization"]["peak_marker"],
                zoom=context["visualization"]["closeup_zoom"])
        ax_full_inflated = fig.add_subplot(
            field_grid[1, 0], projection="3d")
        if network_reference:
            draw_weighted_reference_panel(
                ax_full_inflated, context,
                "B2  Full weighted target | weight 0-1",
                closeup=False)
        else:
            draw_field_panel(
                ax_full_inflated, context["inflated_points"],
                context["inflated_triangles"], context["left_field"],
                context["left_target"], context["field_norm"],
                "B2  Full inflated cortical E-field", equal_limits=False,
                zoom=context["visualization"]["full_brain_zoom"])
    scalar = cm.ScalarMappable(norm=context["field_norm"], cmap=cm.turbo)
    colorbar_axis = fig.add_axes([0.16, 0.075, 0.20, 0.018])
    colorbar = fig.colorbar(
        scalar, cax=colorbar_axis, orientation="horizontal")
    colorbar.set_label("E-field magnitude (V/m)", fontsize=9)
    colorbar.ax.tick_params(labelsize=7)
    coil_legend = [
        Line2D([], [], color=INK, marker="+", markersize=10,
               markeredgewidth=2.0, linestyle="none", label="Coil center"),
        Line2D([], [], color=INK, linewidth=1.8,
               linestyle=(0, (1, 2.2)), dash_capstyle="round",
               label="Coil-normal trajectory"),
    ]
    fig.legend(handles=coil_legend, loc="upper center",
               bbox_to_anchor=(0.135, 0.136), ncol=1,
               frameon=False, fontsize=9, labelcolor=INK)

    if dual:
        comparison_grid = outer[0, 2].subgridspec(1, 2, wspace=0.035)
        draw_network_review_panel(
            fig, comparison_grid[0, 0], context, row, restricted_weighted,
            restricted_binary, hotspot, "categorical", network_reference, "C")
        draw_network_review_panel(
            fig, comparison_grid[0, 1], context, row, restricted_weighted,
            restricted_binary, hotspot, "weighted_probability",
            network_reference, "D")
        legend_handles = (context["reference_legend_handles"]
                          if network_reference else context["legend_handles"])
        fig.legend(handles=legend_handles, loc="upper center",
                   bbox_to_anchor=(0.75, 0.108),
                   ncol=4, frameon=False,
                   fontsize=8.0 if network_reference else 8.5,
                   handlelength=1.25, columnspacing=0.85,
                   labelspacing=0.25)
    else:
        draw_network_review_panel(
            fig, outer[0, 2], context, row, restricted_weighted,
            restricted_binary, hotspot, panel_c_mode, network_reference, "C")
        if panel_c_mode == "categorical":
            legend_handles = (context["reference_legend_handles"]
                              if network_reference else context["legend_handles"])
            fig.legend(handles=legend_handles, loc="upper center",
                       bbox_to_anchor=(0.75, 0.108),
                       ncol=4, frameon=False,
                       fontsize=8.0 if network_reference else 8.5,
                       handlelength=1.25, columnspacing=0.85,
                       labelspacing=0.25)
    title = f"{context['subject']} | {context['display_network']}"
    if context.get('pose_label'):
        title += f" | {context['pose_label']}"
    fig.suptitle(title,
                 fontsize=23, fontweight="bold", color=INK, y=0.972)
    if dual and network_reference:
        footer = (
            "Reference frame | networks shown in A and C | fixed weighted "
            "target shown in B and D | no E-field hotspot boundary")
    elif dual:
        footer = (
            f"Displayed threshold {percentile:.1f}% | categorical hotspot and "
            f"complete E-field boundary | weighted on-target "
            f"{restricted_weighted:.3f} | binary on-target "
            f"{100 * restricted_binary:.1f}%")
    elif panel_c_mode == "weighted_probability" and network_reference:
        footer = (
            "Reference frame | fixed weighted target map | no E-field "
            "hotspot boundary")
    elif network_reference:
        footer = (
            "Reference frame | full individualized network organization | "
            "eligible target outlined in black/white")
    elif panel_c_mode == "weighted_probability":
        footer = (
            f"Displayed threshold {percentile:.1f}% | complete suprathreshold "
            f"E-field outlined in black/white | weighted on-target "
            f"{restricted_weighted:.3f} | selection details saved in PoseDetails.tsv")
    else:
        footer = (
            f"Displayed threshold {percentile:.1f}% | weighted on-target "
            f"{restricted_weighted:.3f} | binary on-target "
            f"{100 * restricted_binary:.1f}% | selection details saved in PoseDetails.tsv")
    input_mode = context.get("network_input_provenance", {}).get("mode")
    if input_mode == "wta_only":
        footer += "\nInput: WTA-derived 0/1 stand-in weights, not measured membership probabilities."
    elif input_mode == "weighted_only":
        footer += "\nInput: percentile-derived binary support and relative target weights; unassigned cortex is not a network atlas."
    fig.text(
        0.5, 0.012, footer,
        ha="center", va="bottom", fontsize=9, color="#5e6a75")
    return fig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--fine-result", required=True)
    parser.add_argument(
        "--best-pose",
        help=("Explicit selected-pose JSON. Defaults to BestCoarsePose.json "
              "inside --fine-result; use BestRobustPose.json for the final "
              "robust prescription."),
    )
    parser.add_argument(
        "--network-label-map",
        help=("Categorical individualized network-label map. If omitted, use "
              "network_label_map from the run configuration when available."),
    )
    parser.add_argument(
        "--weighted-target-map",
        help=("Weighted/probabilistic map to display. If omitted for a weighted "
              "run, use the scoring target map."),
    )
    parser.add_argument("--priors", required=True)
    parser.add_argument("--coil-stl", required=True)
    parser.add_argument("--left-inflated")
    parser.add_argument("--left-sulc")
    parser.add_argument("--visualization-config")
    parser.add_argument("--output", required=True)
    parser.add_argument('--pose-label', default='')
    parser.add_argument(
        "--peak-marker",
        choices=("bullseye", "x", "crosshair"),
        default=None,
        help="Override the peak marker configured for visualization.",
    )
    parser.add_argument(
        "--panel-c-mode",
        choices=("auto", "categorical", "weighted_probability",
                 "dual_comparison", "dual_context_comparison"),
        default=None,
        help="Override adaptive target-review layout selection.",
    )
    args = parser.parse_args()

    config_path = require_file(args.config, "configuration")
    cfg = json.loads(config_path.read_text())
    visual = visualization_options(
        cfg, args.visualization_config, args.peak_marker, args.coil_stl,
        args.panel_c_mode)
    requested_panel_c_mode = visual["panel_c_mode"]
    network_label_source = args.network_label_map or cfg.get("network_label_map")
    weighted_target_source = (
        args.weighted_target_map or cfg.get("weighted_target_map") or
        (cfg.get("target_map") if cfg.get("map_mode") == "weighted" else None))
    categorical_available = bool(network_label_source)
    weighted_available = bool(weighted_target_source)
    visual["panel_c_mode"] = resolve_panel_c_mode(
        requested_panel_c_mode, categorical_available, weighted_available)
    display_network = cfg.get("display_name", (
        "SM-Hand" if cfg["network"] == "Somatomotor_Hand"
        else cfg["network"]))
    result_root = Path(args.fine_result).resolve()
    best_pose_path = require_file(
        args.best_pose or result_root / "BestCoarsePose.json", "best pose")
    best = json.loads(best_pose_path.read_text())
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite QC output: {output}")
    output.mkdir(parents=True)

    center_dir = result_root / f"Center_{int(best['center_id']):05d}"
    field_image = nib.load(str(require_file(center_dir / "magnE.dtseries.nii", "E-field CIFTI")))
    fine_cfg = json.loads(require_file(result_root / "ResolvedConfig.json", "fine configuration").read_text())
    orientations = [float(value) for value in fine_cfg["orientations_degrees"]]
    orientation_index = next((index for index, value in enumerate(orientations)
                              if np.isclose(value, best["orientation_degrees"])), None)
    if orientation_index is None:
        raise ValueError("Winning orientation is absent from mapped E-field CIFTI")

    target_image = nib.load(str(require_file(cfg["target_map"], "scoring target map")))
    area_image = nib.load(str(require_file(cfg["vertex_area"], "vertex-area map")))
    network_label_path = (
        require_file(network_label_source, "network label map")
        if categorical_available else None)
    weighted_target_path = (
        require_file(weighted_target_source, "weighted target display map")
        if weighted_available else None)
    label_image = (nib.load(str(network_label_path))
                   if network_label_path is not None else None)
    weighted_display_image = (
        nib.load(str(weighted_target_path))
        if weighted_target_path is not None else None)
    scoring_mask_path = best.get("scoring_anatomical_mask", "")
    scoring_mask_image = (nib.load(str(require_file(
        scoring_mask_path, "scoring anatomical mask")))
        if scoring_mask_path else None)
    display_mask_path = cfg.get("scoring", {}).get("anatomical_mask", "")
    display_mask_image = (nib.load(str(require_file(
        display_mask_path, "display anatomical mask")))
        if display_mask_path else None)
    scoring_cfg = cfg.get("scoring", {})
    restrict_to_components = bool(
        scoring_cfg.get("restrict_target_to_design_components", False))
    design_component_ids = [int(value) for value in
                            scoring_cfg.get("design_component_ids", [])]
    component_label_path = scoring_cfg.get("component_labels", "")
    if restrict_to_components and (not component_label_path or
                                   not design_component_ids):
        raise ValueError(
            "Retained-component visualization requires component_labels and "
            "design_component_ids in scoring configuration")
    component_label_image = (nib.load(str(require_file(
        component_label_path, "scoring component labels")))
        if restrict_to_components else None)
    palette = priors_palette(require_file(args.priors, "Priors.mat"))
    target_label = next(index for index, (name, _) in palette.items()
                        if name == cfg["network"])
    restrict_weights_to_binary_support = bool(
        cfg.get("scoring", {}).get("restrict_weights_to_binary_support", False))

    structures = {"L": "CIFTI_STRUCTURE_CORTEX_LEFT",
                  "R": "CIFTI_STRUCTURE_CORTEX_RIGHT"}
    field_by_hemi = {}
    area_by_hemi = {}
    target_by_hemi = {}
    label_by_hemi = {}
    anatomical_by_hemi = {}
    display_anatomical_by_hemi = {}
    component_mask_by_hemi = {}
    eligible_by_hemi = {}
    display_eligible_by_hemi = {}
    restricted_target_by_hemi = {}
    restricted_display_weight_by_hemi = {}
    vertex_by_hemi = {}
    for hemi, structure in structures.items():
        vertices, field = cifti_structure(field_image, structure, orientation_index)
        vertex_by_hemi[hemi] = vertices
        field_by_hemi[hemi] = field
        area_by_hemi[hemi] = aligned_surface_values(area_image, structure, vertices)
        target_by_hemi[hemi] = aligned_surface_values(target_image, structure, vertices)
        label_by_hemi[hemi] = (
            aligned_surface_values(label_image, structure, vertices).astype(int)
            if label_image is not None else np.zeros(len(vertices), dtype=int))
        anatomical_by_hemi[hemi] = (
            aligned_surface_values(scoring_mask_image, structure, vertices) == 1.0
            if scoring_mask_image is not None else np.ones(len(vertices), dtype=bool))
        display_anatomical_by_hemi[hemi] = (
            aligned_surface_values(
                display_mask_image, structure, vertices) == 1.0
            if display_mask_image is not None
            else np.ones(len(vertices), dtype=bool))
        component_mask_by_hemi[hemi] = (
            np.isin(aligned_surface_values(
                component_label_image, structure, vertices), design_component_ids)
            if component_label_image is not None
            else np.ones(len(vertices), dtype=bool))
        eligible_by_hemi[hemi] = anatomical_by_hemi[hemi] & (
            (label_by_hemi[hemi] == target_label)
            if label_image is not None else (target_by_hemi[hemi] != 0)) & \
            component_mask_by_hemi[hemi]
        display_eligible_by_hemi[hemi] = display_anatomical_by_hemi[hemi] & (
            (label_by_hemi[hemi] == target_label)
            if label_image is not None else (target_by_hemi[hemi] != 0)) & \
            component_mask_by_hemi[hemi]
        scoring_weight_mask = weighted_scoring_mask(
            anatomical_by_hemi[hemi], label_by_hemi[hemi], target_label,
            target_by_hemi[hemi], restrict_weights_to_binary_support,
            label_image is not None) & component_mask_by_hemi[hemi]
        restricted_target_by_hemi[hemi] = (
            target_by_hemi[hemi] * scoring_weight_mask)
        if weighted_display_image is not None:
            display_weights = aligned_surface_values(
                weighted_display_image, structure, vertices)
            restricted_display_weight_by_hemi[hemi] = (
                display_weights * display_eligible_by_hemi[hemi])
        else:
            restricted_display_weight_by_hemi[hemi] = np.zeros(
                len(vertices), dtype=float)

    percentiles = np.round(np.arange(99.0, 100.0, 0.1), 1)
    rows = composition_rows(
        field_by_hemi, area_by_hemi, label_by_hemi, percentiles)
    selection_scores = []
    restricted_weighted_scores = []
    restricted_binary_scores = []
    for percentile in percentiles:
        selection, _ = weighted_target_score(
            field_by_hemi, area_by_hemi, restricted_target_by_hemi, percentile)
        weighted, _ = weighted_target_score(
            field_by_hemi, area_by_hemi,
            restricted_display_weight_by_hemi, percentile)
        binary, _ = weighted_target_score(
            field_by_hemi, area_by_hemi, eligible_by_hemi, percentile)
        selection_scores.append(selection)
        restricted_weighted_scores.append(weighted)
        restricted_binary_scores.append(binary)
    index_995 = int(np.argmin(np.abs(percentiles - 99.5)))
    selection_score = selection_scores[index_995]
    weighted_score = restricted_weighted_scores[index_995]
    binary_score = restricted_binary_scores[index_995]
    row_995 = min(rows, key=lambda row: abs(float(row[0]) - 99.5))
    threshold_995 = row_995[1]
    saved_binary_score = float(best["binary_score_at_99_5_percent"])
    if (np.isfinite(saved_binary_score) and
            not np.isclose(binary_score, saved_binary_score,
                           atol=1e-10, rtol=0.0)):
        raise ValueError("Visualization binary score does not match saved selected-pose score")
    if not np.isclose(np.mean(selection_scores),
                      float(best["mean_on_target_score"]), atol=1e-10, rtol=0.0):
        raise ValueError("Visualization scores do not match saved selection score")

    left_points, left_triangles = load_gifti_surface(cfg["surfaces"]["left_pial"])
    left_field = np.full(len(left_points), np.nan)
    left_labels = np.zeros(len(left_points), dtype=int)
    left_target = np.zeros(len(left_points), dtype=bool)
    left_target_weight = np.zeros(len(left_points), dtype=float)
    left_area = np.zeros(len(left_points), dtype=float)
    left_hotspot = np.zeros(len(left_points), dtype=bool)
    left_vertices = vertex_by_hemi["L"]
    left_field[left_vertices] = field_by_hemi["L"]
    left_labels[left_vertices] = label_by_hemi["L"]
    left_target[left_vertices] = display_eligible_by_hemi["L"]
    left_target_weight[left_vertices] = restricted_display_weight_by_hemi["L"]
    left_area[left_vertices] = area_by_hemi["L"]
    left_hotspot[left_vertices] = field_by_hemi["L"] > threshold_995
    left_peak_vertex = int(np.nanargmax(left_field))
    hotspot_points = left_points[left_hotspot]
    if not len(hotspot_points):
        raise ValueError("The 99.5th-percentile hotspot has no left-hemisphere vertices")
    local_triangles, _ = local_surface_patch(
        left_points, left_triangles, hotspot_points, margin_mm=20.0)
    hotspot_crop = (hotspot_points.min(axis=0) - 5.0,
                    hotspot_points.max(axis=0) + 5.0)
    right_points, right_triangles = load_gifti_surface(
        cfg["surfaces"]["right_pial"])
    right_field = np.full(len(right_points), np.nan)
    right_labels = np.zeros(len(right_points), dtype=int)
    right_target = np.zeros(len(right_points), dtype=bool)
    right_vertices = vertex_by_hemi["R"]
    if np.max(right_vertices) >= len(right_points):
        raise ValueError("Right E-field indices exceed right pial vertex count")
    right_field[right_vertices] = field_by_hemi["R"]
    right_labels[right_vertices] = label_by_hemi["R"]
    right_target[right_vertices] = display_eligible_by_hemi["R"]
    inflated_path = args.left_inflated or visual.get("left_inflated_surface")
    if not inflated_path:
        raise ValueError(
            "An fs_LR 32k left inflated surface is required via --left-inflated "
            "or visualization.left_inflated_surface")
    inflated_path = require_file(inflated_path, "left inflated surface")
    inflated_points, inflated_triangles = load_gifti_surface(inflated_path)
    if len(inflated_points) != len(left_points):
        raise ValueError("Folded and inflated surface vertex counts differ")
    sulc_path = args.left_sulc or visual.get("left_sulc_surface")
    if not sulc_path:
        raise ValueError(
            "A 32k left sulcal metric is required via "
            "visualization.left_sulc_surface")
    sulc_path = require_file(sulc_path, "left sulcal metric")
    left_sulc = load_gifti_metric(sulc_path)
    if len(left_sulc) != len(left_points):
        raise ValueError(
            "Sulcal metric and cortical surfaces have different vertex counts")

    placement_rows = list(csv.DictReader(
        require_file(center_dir / "Placements.tsv", "placement table").open(), delimiter="\t"))
    placement = placement_rows[orientation_index]
    benchmark = json.loads(require_file(center_dir / "Benchmark.json", "pose provenance").read_text())
    center = [float(best[key]) for key in ("x", "y", "z")]
    y_direction = [float(placement[f"pos_ydir_{axis}"]) for axis in "xyz"]
    handle_policy = resolve_handle_policy(cfg)
    simulated_affine = saved_pose_matrix(benchmark, float(best['orientation_degrees']))
    prescribed_affine, handle_decision = canonicalize_pose_matrix(
        simulated_affine, handle_policy)
    prescribed_degrees = prescribed_orientation(
        best["orientation_degrees"], handle_decision["flip_applied"])
    (coil_points, coil_triangles), coil_affine = load_coil_mesh(
        require_file(args.coil_stl, "coil STL"), center, y_direction,
        benchmark["scalp_normal"], float(cfg["distance_mm"]),
        pose_matrix=prescribed_affine)
    coil_center, coil_inward, coil_axis_segment = coil_axis_geometry(coil_affine)
    skin_points, skin_triangles = load_gifti_surface(cfg["skin"])
    skin_points, skin_triangles = vertex_cluster(skin_points, skin_triangles, 2.2)

    if visual["panel_c_mode"] in (
            "categorical", "dual_comparison", "dual_context_comparison"):
        save_bar_figure(rows, palette, target_label, weighted_score, binary_score,
                        output / "HotspotNetworkComposition.png")
    peak_normal = vertex_normal(left_points, left_triangles, left_peak_vertex)
    inflated_peak_normal = vertex_normal(
        inflated_points, inflated_triangles, left_peak_vertex)
    scalp_normal = -simulated_affine[:3, 2]
    center_array = np.asarray(center, dtype=float)
    technician_radius = float(visual.get("technician_crop_radius_mm", 78.0))
    technician_crop = (
        center_array - technician_radius,
        center_array + technician_radius)
    widest_hotspot = np.isfinite(left_field) & (left_field > rows[0][1])
    fixed_focus = widest_hotspot | left_target
    folded_closeup_crop = crop_from_points(left_points[fixed_focus], 7.0)
    inflated_closeup_crop = crop_from_points(
        inflated_points[fixed_focus], 7.0)
    folded_review_triangles, _ = local_surface_patch(
        left_points, left_triangles, left_points[fixed_focus], margin_mm=32.0)
    inflated_review_triangles, _ = local_surface_patch(
        inflated_points, inflated_triangles,
        inflated_points[fixed_focus], margin_mm=32.0)
    legend_area = {}
    for _, _, _, areas in rows:
        for label, area in areas.items():
            if label in palette and label > 0:
                legend_area[label] = max(legend_area.get(label, 0.0), area)
    legend_labels = [label for label, _ in sorted(
        legend_area.items(), key=lambda item: (-item[1], item[0]))[:8]]
    legend_handles = [Patch(
        facecolor=palette[label][1],
        edgecolor=INK if label == target_label else "none",
        linewidth=1.2, label=palette[label][0]) for label in legend_labels]
    reference_areas = {
        int(label): float(np.sum(left_area[left_labels == label]))
        for label in np.unique(left_labels)
        if int(label) > 0 and int(label) in palette}
    reference_total = float(sum(reference_areas.values()))
    reference_legend_handles = [Patch(
        facecolor=palette[label][1],
        edgecolor=INK if label == target_label else "none",
        linewidth=1.2, label=palette[label][0])
        for label in sorted(reference_areas)]
    context = {
        'pose_label': args.pose_label,
        "subject": cfg["subject"],
        "display_network": display_network,
        "restrict_to_components": restrict_to_components,
        "left_points": left_points,
        "left_triangles": left_triangles,
        "left_field": left_field,
        "left_labels": left_labels,
        "left_target": left_target,
        "left_target_weight": left_target_weight,
        "left_sulc": left_sulc,
        "right_points": right_points,
        "right_triangles": right_triangles,
        "right_field": right_field,
        "right_labels": right_labels,
        "right_target": right_target,
        "inflated_points": inflated_points,
        "inflated_triangles": inflated_triangles,
        "folded_review_triangles": folded_review_triangles,
        "inflated_review_triangles": inflated_review_triangles,
        "skin_points": skin_points,
        "skin_triangles": skin_triangles,
        "coil_points": coil_points,
        "coil_triangles": coil_triangles,
        "coil_center": coil_center,
        "coil_axis_segment": coil_axis_segment,
        "head_points": np.vstack(
            (skin_points, coil_points, left_points, right_points)),
        "technician_crop": technician_crop,
        "coil_view": view_from_direction(scalp_normal),
        "review_view": (view_from_direction(scalp_normal)
                        if visual.get('review_camera', 'coil_normal') == 'coil_normal'
                        else (visual['review_view_elevation'], visual['review_view_azimuth'])),
        "coil_silhouette": projected_surface_outline_segments(
            coil_points, coil_triangles, scalp_normal),
        "folded_closeup_crop": folded_closeup_crop,
        "inflated_closeup_crop": inflated_closeup_crop,
        "peak_point": left_points[left_peak_vertex] + 1.5 * peak_normal,
        "inflated_peak_point": (
            inflated_points[left_peak_vertex] + 1.5 * inflated_peak_normal),
        "field_norm": colors.Normalize(
            vmin=np.nanpercentile(left_field, 35),
            vmax=np.nanpercentile(left_field, 99.9)),
        "palette": palette,
        "network_input_provenance": cfg.get("network_input_provenance", {}),
        "target_label": target_label,
        "target_color": palette[target_label][1],
        "network_reference_row": (
            0.0, np.nan, reference_total, reference_areas),
        "legend_handles": legend_handles,
        "reference_legend_handles": reference_legend_handles,
        "visualization": visual,
        "coil_name": visual["coil_display_name"],
    }

    static_figure = build_review_figure(
        context, row_995, weighted_score, binary_score)
    static_figure.savefig(
        output / "OptimalPoseOverview.png", dpi=190, facecolor=PAPER_BG)
    plt.close(static_figure)

    reference_figure = build_review_figure(
        context, rows[0], restricted_weighted_scores[0],
        restricted_binary_scores[0], network_reference=True)
    animation_frames = [figure_image(reference_figure)]
    plt.close(reference_figure)
    for row, restricted_weighted, restricted_binary in zip(
            rows, restricted_weighted_scores, restricted_binary_scores):
        frame_figure = build_review_figure(
            context, row, restricted_weighted, restricted_binary)
        animation_frames.append(figure_image(frame_figure))
        plt.close(frame_figure)
    durations = [int(visual["network_reference_frame_duration_ms"])] + [
        int(visual["threshold_frame_duration_ms"])] * len(rows)
    durations[-1] = int(visual["final_frame_duration_ms"])
    animation_frames[0].save(
        output / "OptimalPoseThresholdSweep.gif", save_all=True,
        append_images=animation_frames[1:], duration=durations, loop=0,
        optimize=False)

    with (output / "PoseDetails.tsv").open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["subject", "network", "center_id", "scalp_vertex",
                         "x", "y", "z", "simulated_orientation_degrees",
                         "prescribed_orientation_degrees", "handle_flip_applied",
                         "coil_model", "selection_rule"])
        writer.writerow([
            cfg["subject"], cfg["network"], best["center_id"],
            best["scalp_vertex"], best["x"], best["y"], best["z"],
            best["orientation_degrees"], prescribed_degrees,
            handle_decision["flip_applied"], visual["coil_display_name"],
            best["selection_rule"]])

    if visual["panel_c_mode"] in (
            "categorical", "dual_comparison", "dual_context_comparison"):
        with (output / "HotspotNetworkComposition.tsv").open(
                "w", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow([
                "percentile", "threshold_v_per_m", "network_id",
                "network_name", "surface_area_mm2", "hotspot_percent"])
            for percentile, threshold, total, areas in rows:
                for label, area in sorted(areas.items()):
                    name = palette.get(label, ("Unassigned", None))[0]
                    writer.writerow([
                        f"{percentile:.1f}", threshold, label, name, area,
                        100 * area / total if total else 0])

    with (output / "ThresholdReviewMetrics.tsv").open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["percentile", "threshold_v_per_m",
                         "primary_selection_score",
                         "restricted_weighted_on_target",
                         "restricted_binary_on_target_percent"])
        for row, selection, restricted_weighted, restricted_binary in zip(
                rows, selection_scores, restricted_weighted_scores,
                restricted_binary_scores):
            writer.writerow([f"{row[0]:.1f}", row[1], selection,
                             restricted_weighted, 100 * restricted_binary])

    panel_c_mode = visual["panel_c_mode"]
    created_outputs = [
        "OptimalPoseOverview.png", "OptimalPoseThresholdSweep.gif",
        "ThresholdReviewMetrics.tsv", "PoseDetails.tsv",
        "VisualizationSummary.json"]
    if panel_c_mode in (
            "categorical", "dual_comparison", "dual_context_comparison"):
        created_outputs[2:2] = [
            "HotspotNetworkComposition.png",
            "HotspotNetworkComposition.tsv"]
    summary = {
        'pose_label': args.pose_label,
        "subject": cfg["subject"], "network": cfg["network"],
        "display_name": display_network,
        "map_mode": cfg["map_mode"], "best_center_id": int(best["center_id"]),
        "report_page_role": "post_tans_optimal_pose_summary",
        "requested_panel_c_mode": requested_panel_c_mode,
        "resolved_panel_c_mode": panel_c_mode,
        "panel_c_mode": panel_c_mode,
        "categorical_map_available": categorical_available,
        "weighted_map_available": weighted_available,
        "best_orientation_degrees": float(best["orientation_degrees"]),
        "simulated_orientation_degrees": float(best["orientation_degrees"]),
        "prescribed_orientation_degrees": prescribed_degrees,
        "handle_flip_applied": handle_decision["flip_applied"],
        "handle_orientation_policy": handle_policy,
        "simulated_physical_handle_direction_ras": handle_decision[
            "simulated_physical_handle_direction_ras"],
        "prescribed_physical_handle_direction_ras": handle_decision[
            "prescribed_physical_handle_direction_ras"],
        "primary_selection_metric": (
            "binary_target_surface_area_fraction"
            if cfg["map_mode"] == "binary" else
            "mean_native_target_probability"),
        "mean_99_0_to_99_9_primary_selection_score": float(
            np.mean(selection_scores)),
        "primary_selection_score_at_99_5_percent": selection_score,
        "mean_99_0_to_99_9_weighted_target_score": float(
            np.mean(restricted_weighted_scores)),
        "restricted_weighted_target_score_at_99_5_percent": weighted_score,
        "restricted_binary_on_target_at_99_5_percent": binary_score,
        "hotspot_definition": (
            "E-field > MATLAB-prctile-compatible 99.5th percentile across cortical CIFTI values"),
        "panel_c_semantics": (
            "full-height lateral overview plus folded and inflated E-field views "
            "inside head/coil context, followed by synchronized categorical and "
            "weighted target review panels"
            if panel_c_mode == "dual_context_comparison" else
            "side-by-side synchronized categorical hotspot labels and fixed "
            "native 0-1 weighted target map; the weighted panel animates only "
            "the complete suprathreshold E-field boundary"
            if panel_c_mode == "dual_comparison" else
            "fixed native 0-1 weighted target map restricted to eligible "
            "target support; only the complete suprathreshold E-field "
            "boundary changes across threshold frames"
            if panel_c_mode == "weighted_probability" else
            "categorical individualized network labels within the complete "
            "suprathreshold E-field hotspot; surface-area weighted and distinct "
            "from the restricted selection score"),
        "scoring_target_source": str(Path(cfg["target_map"]).resolve()),
        "scoring_component_label_source": (
            str(Path(component_label_path).resolve())
            if component_label_path else None),
        "scoring_design_component_ids": design_component_ids,
        "display_anatomical_mask_source": (
            str(Path(display_mask_path).resolve()) if display_mask_path else None),
        "display_target_definition": (
            "retained connected components of target network intersect "
            "configured anatomical eligibility zone"
            if restrict_to_components else
            "target network intersect configured anatomical eligibility zone"),
        "selected_pose_source": str(best_pose_path),
        "selection_rule": best.get("selection_rule", "not recorded"),
        "weighted_target_source": (
            str(weighted_target_path) if weighted_target_path is not None else None),
        "weighted_target_display_transform": (
            "none: native weights are clipped only to their declared 0-1 domain "
            "and alpha-blended over sulcal anatomy without subject-wise rescaling"
            if panel_c_mode in (
                "weighted_probability", "dual_comparison",
                "dual_context_comparison")
            else "not displayed in Panel C"),
        "canonical_color_source": str(Path(args.priors).resolve()),
        "network_label_source": (
            str(network_label_path) if network_label_path is not None else None),
        "coil_mesh_source": str(Path(args.coil_stl).resolve()),
        "coil_display_name": visual["coil_display_name"],
        "inflated_surface_source": str(inflated_path),
        "sulcal_context_source": str(sulc_path),
        "coil_affine_method": "exact saved SimNIBS transform with configured handle canonicalization",
        "coil_affine": coil_affine.tolist(),
        "coil_center_marker": {
            "style": "white-haloed plus in head views",
            "definition": "origin of the displayed saved coil transform",
            "coordinate_system": "NIfTI_RAS_mm",
            "center_xyz": coil_center.tolist(),
        },
        "coil_normal_trajectory": {
            "style": "white-haloed dotted line in lateral head view",
            "definition": "geometric normal through the coil center; local +Z points inward",
            "inward_direction_ras": coil_inward.tolist(),
            "outward_extent_mm": COIL_AXIS_OUTWARD_MM,
            "inward_extent_mm": COIL_AXIS_INWARD_MM,
            "segment_xyz": coil_axis_segment.tolist(),
        },
        "right_panel_peak_marker": (
            f"neutral {visual['peak_marker']} at maximum E-field on the displayed left cortical surface"
        ),
        "head_views": (
            "one full-height left-lateral head, coil, and cortical E-field overview"
            if panel_c_mode == "dual_context_comparison" else
            "bilateral technician view above left-lateral overview aligned to "
            "the saved scalp normal"),
        "closeup_camera": "fixed to the exact simulated coil normal for A1/B1/C/D; B2 is lateral",
        "closeup_view_elevation_azimuth": list(context['review_view']),
        "threshold_animation_geometry": (
            "fixed camera, crop, surface geometry, and legend across all frames"),
        "panel_c_scale": (
            "categorical surface-area composition beneath Panel C and fixed "
            "native 0-1 target-weight scale beneath Panel D"
            if panel_c_mode in (
                "dual_comparison", "dual_context_comparison") else
            "fixed horizontal native target-weight scale from 0 to 1"
            if panel_c_mode == "weighted_probability" else
            "one horizontal surface-area network-composition bar; full left-cortex "
            "composition in the reference frame and hotspot composition in each "
            "99.0-99.9 E-field threshold frame"),
        "animation_sequence": (
            "11 synchronized frames: all-network head views and weighted-target "
            "inflated views held for two seconds, followed by side-by-side "
            "99.0-99.9 hotspot reviews"
            if panel_c_mode in (
                "dual_comparison", "dual_context_comparison") else
            "11 frames: fixed weighted target reference followed by 99.0-99.9 "
            "E-field hotspot boundaries"
            if panel_c_mode == "weighted_probability" else
            "11 frames: full individualized network reference followed by "
            "99.0-99.9 E-field hotspot thresholds"),
        "visualization_options": visual,
        "html_report_contract": {
            "primary_static_asset": "OptimalPoseOverview.png",
            "primary_animated_asset": "OptimalPoseThresholdSweep.gif",
            "tabular_assets": [
                name for name in created_outputs if name.endswith(".tsv")],
            "manifest": "VisualizationSummary.json",
        },
        "created_outputs": created_outputs,
        "intentionally_omitted_outputs": [
            "Efield_frame.png", "HotspotNetworks_frame.png",
            "Efield_HotspotNetworks_flip.gif"],
        "visualization_only": True,
    }
    (output / "VisualizationSummary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
