#!/usr/bin/env python3
"""Render the target-selection and two-stage search summary report page."""
import argparse
import csv
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_tans_target_report")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import colors
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.text import Annotation
from mpl_toolkits.mplot3d import proj3d
import numpy as np
from scipy.io import loadmat
from scipy.sparse.csgraph import dijkstra
from simulated_pose_geometry import saved_pose_matrix

from generate_target_projection_coverage_plan import cortical_vector, mesh_graph
from render_optimal_pose_qc import (
    PAPER_BG,
    INK,
    crop_from_points,
    format_3d,
    gray_surface,
    load_gifti_surface,
    priors_palette,
    surface_collection,
    vertex_cluster,
    view_from_direction,
)


ZONE_GOLD = "#d8ae46"
TARGET_CYAN = "#62e3e8"
OVERLAP_TEAL = "#008c91"
COMPONENT_BLUE = "#147fbd"
EXCLUDED_MAGENTA = "#a52375"
SCALP_SUPPORT = "#86c8de"
GRID_RED = "#c7143d"
FINE_PURPLE = "#7b3294"
WINNER_GOLD = "#ffc928"


def require_file(path, label):
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Missing {label}: {resolved}")
    return resolved


def require_directory(path, label):
    resolved = Path(path).resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"Missing {label}: {resolved}")
    return resolved


def read_tsv(path):
    with require_file(path, "table").open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def load_json(path, label):
    return json.loads(require_file(path, label).read_text())


def display_network_name(name):
    aliases = {
        "Somatomotor_Hand": "SM-Hand",
        "Frontoparietal": "FPN",
        "Salience": "SAL",
        "CinguloOpercular": "AMN-CO",
    }
    return aliases.get(name, name.replace("_", " "))


def shaded_facecolors(points, triangles, base_color="#b8bec4"):
    light = np.asarray([-0.4, -0.8, 0.9], dtype=float)
    light /= np.linalg.norm(light)
    a, b, c = (points[triangles[:, index]] for index in range(3))
    normals = np.cross(b - a, c - a)
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1.0
    normals /= lengths[:, None]
    intensity = np.clip(0.60 + 0.30 * (normals @ light), 0.34, 1.0)
    base = np.asarray(colors.to_rgb(base_color))
    return np.c_[np.clip(base * intensity[:, None], 0, 1),
                 np.ones(len(triangles))]


def categorical_surface(ax, points, triangles, categories, color_by_category,
                        background="#aeb5bd"):
    rgba = shaded_facecolors(points, triangles, background)
    face_categories = categories[triangles]
    for category, color in color_by_category.items():
        active = np.sum(face_categories == category, axis=1) >= 2
        rgba[active, :3] = colors.to_rgb(color)
    return surface_collection(ax, points, triangles, rgba)


def mask_surface(ax, points, triangles, mask, color, alpha=1.0,
                 background="#aeb5bd"):
    rgba = shaded_facecolors(points, triangles, background)
    active = np.sum(mask[triangles], axis=1) >= 2
    rgba[active, :3] = colors.to_rgb(color)
    return surface_collection(ax, points, triangles, rgba, alpha=alpha)


def overlay_mask(ax, points, triangles, mask, color, alpha=1.0, zorder=5):
    active = np.sum(mask[triangles], axis=1) >= 2
    if not np.any(active):
        return None
    rgba = np.tile(np.r_[colors.to_rgb(color), 1.0], (np.count_nonzero(active), 1))
    return surface_collection(
        ax, points, triangles[active], rgba, alpha=alpha, zorder=zorder)


def draw_point(ax, point, facecolor, marker="o", size=90, edgecolor="white",
               linewidth=1.2, zorder=15):
    kwargs = {
        "s": size, "marker": marker, "color": facecolor,
        "linewidths": linewidth, "depthshade": False, "zorder": zorder,
    }
    if marker not in ("x", "+"):
        kwargs["edgecolors"] = edgecolor
    ax.scatter(*np.asarray(point), **kwargs)


def draw_winner(ax, point, label=None):
    point = np.asarray(point)
    ax.scatter(*point, s=190, marker="o", facecolors="none",
               edgecolors="white", linewidths=4.0, depthshade=False,
               zorder=20)
    ax.scatter(*point, s=170, marker="o", facecolors="none",
               edgecolors=INK, linewidths=2.2, depthshade=False, zorder=21)
    ax.scatter(*point, s=48, marker="o", color=WINNER_GOLD,
               edgecolors=INK, linewidths=1.1, depthshade=False, zorder=22)
    if label:
        draw_point_label(ax, point, label, (0.08, 0.87))


class ProjectedLabel(Annotation):
    def __init__(self, ax, point, text, position):
        self.point = np.asarray(point)
        super().__init__(text, xy=(0, 0), xytext=position,
                         textcoords='axes fraction', fontsize=8, color=INK,
                         fontweight='bold', zorder=30,
                         bbox=dict(facecolor=PAPER_BG, edgecolor='none', pad=2),
                         arrowprops=dict(arrowstyle='-', color=INK, linewidth=.8),
                         annotation_clip=False)
        ax.add_artist(self)

    def draw(self, renderer):
        x, y, _ = proj3d.proj_transform(*self.point, self.axes.get_proj())
        self.xy = (x, y)
        super().draw(renderer)


def draw_point_label(ax, point, text, position):
    return ProjectedLabel(ax, point, text, position)


def draw_scale_ruler(ax, crop, length_mm=20.0):
    """Draw a readable lower-left scale bar tied to the 3-D crop extent."""
    low, high = (np.asarray(value, dtype=float) for value in crop)
    center = (low + high) / 2
    azimuth = np.deg2rad(ax.azim)
    horizontal = np.array([-np.sin(azimuth), np.cos(azimuth), 0.0])
    def width(length):
        xyz = np.vstack((center, center + length * horizontal))
        x, y, _ = proj3d.proj_transform(*xyz.T, ax.get_proj())
        axes_xy = ax.transAxes.inverted().transform(
            ax.transData.transform(np.column_stack((x, y))))
        return float(np.linalg.norm(axes_xy[1] - axes_xy[0]))
    ruler_fraction = width(length_mm)
    while ruler_fraction > .42:
        length_mm /= 2
        ruler_fraction = width(length_mm)
    x0, y0 = 0.075, 0.075
    x1 = x0 + ruler_fraction
    for linewidth, color in ((4.2, "white"), (1.8, INK)):
        ax.add_line(Line2D(
            [x0, x1], [y0, y0], transform=ax.transAxes, color=color,
            linewidth=linewidth, solid_capstyle="butt", clip_on=False,
            zorder=25))
        for x in (x0, (x0 + x1) / 2.0, x1):
            ax.add_line(Line2D(
                [x, x], [y0 - 0.015, y0 + 0.015],
                transform=ax.transAxes, color=color, linewidth=linewidth,
                solid_capstyle="butt", clip_on=False, zorder=25))
    ax.text2D(
        (x0 + x1) / 2.0, y0 + 0.025, f"{length_mm:g} mm",
        transform=ax.transAxes, ha="center", va="bottom", fontsize=7.5,
        fontweight="bold", color=INK, zorder=26,
        bbox={"facecolor": PAPER_BG, "edgecolor": "none", "alpha": 0.88,
              "pad": 1.5})


def light_scalp_surface(ax, points, triangles, alpha=0.96):
    rgba = shaded_facecolors(points, triangles, "#cbd0d4")
    rgba[:, :3] = 0.84 + 0.16 * rgba[:, :3]
    return surface_collection(ax, points, triangles, rgba, alpha=alpha)


def grid_points(rows):
    return np.asarray([[float(row[key]) for key in ("x", "y", "z")]
                       for row in rows], dtype=float)


def render(args):
    cfg = load_json(args.config, "fine run configuration")
    target_display = cfg.get("display_name", display_network_name(cfg["network"]))
    anatomical_zone = cfg.get(
        "anatomical_zone_display",
        cfg.get("scoring", {}).get("anatomical_zone_display", "anatomical zone"))
    target_dir = require_directory(args.target_preparation, "target preparation")
    coverage_dir = require_directory(args.coverage_plan, "coverage plan")
    coarse_dir = require_directory(args.coarse_result, "coarse result")
    fine_plan_dir = require_directory(args.fine_plan, "fine plan")
    fine_dir = require_directory(args.fine_result, "fine result")
    robust_plan_dir = (require_directory(args.robust_plan, "robustness plan")
                       if args.robust_plan else None)
    robust_analysis_dir = (
        require_directory(args.robust_analysis, "robustness analysis")
        if args.robust_analysis else None)
    if (robust_plan_dir is None) != (robust_analysis_dir is None):
        raise ValueError(
            "--robust-plan and --robust-analysis must be provided together")
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite report output: {output}")
    output.mkdir(parents=True)

    pial_points, pial_triangles = load_gifti_surface(cfg["surfaces"]["left_pial"])
    skin_points, skin_triangles = load_gifti_surface(cfg["skin"])
    skin_small_points, skin_small_triangles = vertex_cluster(
        skin_points, skin_triangles, 2.2)
    structure = "CIFTI_STRUCTURE_CORTEX_LEFT"
    zone = cortical_vector(
        target_dir / "SelectedGyralMask.dscalar.nii", structure,
        len(pial_points)) == 1
    full_network = cortical_vector(
        target_dir / "FullBinaryTargetSupport.dscalar.nii", structure,
        len(pial_points)) == 1
    eligible = cortical_vector(
        target_dir / "BinaryTargetSupportInsideGyralMask.dscalar.nii",
        structure, len(pial_points)) == 1
    clusters = np.nan_to_num(cortical_vector(
        target_dir / "ClusterLabels.dscalar.nii", structure,
        len(pial_points)), nan=0.0).astype(int)

    palette = priors_palette(require_file(args.priors, "Priors.mat"))
    target_id = next(index for index, (name, _) in palette.items()
                     if name == cfg["network"])
    target_color = colors.to_hex(palette[target_id][1])
    coverage = load_json(coverage_dir / "CoverageSummary.json", "coverage summary")
    map_mode = cfg.get("map_mode", coverage.get("map_mode", "weighted"))
    coverage_fraction = float(coverage.get(
        "achieved_target_coverage_fraction",
        coverage["achieved_target_weighted_coverage_fraction"]))
    coverage_measure_label = (
        "binary target-area" if map_mode == "binary" else
        "probability-weighted target")
    component_mass_label = (
        "target-area component" if map_mode == "binary" else
        "weighted component")
    coarse_winner = load_json(coarse_dir / "BestCoarsePose.json", "coarse winner")
    fine_summary = load_json(fine_plan_dir / "FinePlanSummary.json", "fine plan summary")
    fine_winner = load_json(fine_dir / "BestCoarsePose.json", "fine winner")
    component_rows = read_tsv(
        coverage_dir.parent / "ComponentDesignStatus.tsv")
    coarse_grid = read_tsv(coverage_dir / "SearchGrid.tsv")
    fine_grid = read_tsv(fine_plan_dir / "SearchGrid.tsv")
    projected_rows = read_tsv(coverage_dir / "ProjectedTargetCoverage.tsv")
    coarse_scores = read_tsv(coarse_dir / "CoarsePoseScores.tsv")
    fine_scores = read_tsv(fine_dir / "CoarsePoseScores.tsv")
    robust_winner = None
    robust_summary = None
    robust_sensitivity = []
    robust_perturbations = []
    robust_grid = []
    runtime_overhead = None
    if robust_plan_dir is not None:
        robust_winner = load_json(
            robust_analysis_dir / "BestRobustPose.json", "robust winner")
        robust_summary = load_json(
            robust_analysis_dir / "RobustnessSummary.json",
            "robustness summary")
        robust_sensitivity = read_tsv(
            robust_analysis_dir / "RobustnessSensitivity.tsv")
        robust_perturbations = read_tsv(
            robust_plan_dir / "Perturbations.tsv")
        robust_grid = read_tsv(robust_plan_dir / "SearchGrid.tsv")
        runtime_path = robust_analysis_dir.parent / "RuntimeOverhead.json"
        if runtime_path.is_file():
            runtime_overhead = load_json(
                runtime_path, "runtime-overhead summary")

    grid_mat = loadmat(coverage_dir / "SearchGrid.mat", simplify_cells=True)["grid"]
    candidate_vertices = np.asarray(
        grid_mat["candidate_scalp_vertices"], dtype=int).reshape(-1) - 1
    candidate_mask = np.zeros(len(skin_points), dtype=bool)
    candidate_mask[candidate_vertices] = True
    mandatory_vertices = np.asarray(
        coverage["mandatory_centroid_anchor_scalp_vertices"], dtype=int) - 1
    projected_points = grid_points([
        {"x": row["x"], "y": row["y"], "z": row["z"]}
        for row in projected_rows])
    coarse_points = grid_points(coarse_grid)
    fine_points = grid_points(fine_grid)
    candidate_points = skin_points[candidate_vertices]
    # One candidate-domain-centered physical crop keeps E-G spatially aligned.
    scalp_crop = crop_from_points(candidate_points, 8.0)

    coarse_vertex = int(coarse_winner["scalp_vertex"]) - 1
    graph = mesh_graph(skin_points, skin_triangles)
    local_distance = dijkstra(
        graph, indices=coarse_vertex, directed=False,
        limit=float(fine_summary["radius_mm"]))
    local_zone = local_distance <= float(fine_summary["radius_mm"]) + 1e-8
    fine_winner_vertex = int(fine_winner["scalp_vertex"]) - 1
    distance_from_fine_winner = dijkstra(
        graph, indices=fine_winner_vertex, directed=False, limit=8.0)
    coarse_vertices = np.asarray(
        [int(row["scalp_vertex"]) - 1 for row in coarse_grid], dtype=int)
    nearby_coarse = np.flatnonzero(
        (distance_from_fine_winner[coarse_vertices] <= 8.0) &
        (np.arange(len(coarse_grid)) != int(coarse_winner["center_id"]) - 1))
    nearby_coarse = nearby_coarse[np.argsort(
        distance_from_fine_winner[coarse_vertices[nearby_coarse]])][:2]
    benchmark = load_json(
        coarse_dir / f"Center_{int(coarse_winner['center_id']):05d}" /
        "Benchmark.json", "coarse winner benchmark")
    scalp_normal = -saved_pose_matrix(
        benchmark, float(coarse_winner['orientation_degrees']))[:3, 2]
    scalp_view = view_from_direction(scalp_normal)

    focus = zone | full_network
    cortical_crop = crop_from_points(pial_points[focus], 7.0)
    meaningful_ids = [int(row["cluster_id"]) for row in component_rows
                      if row["design_status"] == "included"]
    excluded_ids = [int(row["cluster_id"]) for row in component_rows
                    if row["design_status"] != "included"]

    fig = plt.figure(figsize=(20, 10), dpi=120, facecolor=PAPER_BG)
    layout = fig.add_gridspec(
        2, 12, height_ratios=[0.92, 1.08], hspace=0.055, wspace=0.025,
        left=0.025, right=0.985, bottom=0.13, top=0.91)

    top_specs = [layout[0, 0:3], layout[0, 3:6],
                 layout[0, 6:9], layout[0, 9:12]]
    top_axes = [fig.add_subplot(spec, projection="3d") for spec in top_specs]
    for axis in top_axes:
        axis.computed_zorder = False

    mask_surface(top_axes[0], pial_points, pial_triangles, zone, ZONE_GOLD)
    format_3d(
        top_axes[0], pial_points,
        f"A  Anatomical eligibility\ngold = {anatomical_zone}",
        crop=cortical_crop, view=(8, 180), equal_limits=False, zoom=1.28)

    mask_surface(
        top_axes[1], pial_points, pial_triangles, full_network, target_color)
    format_3d(
        top_axes[1], pial_points,
        f"B  Individualized target\nnetwork color = {target_display}",
        crop=cortical_crop, view=(8, 180), equal_limits=False, zoom=1.28)

    category = np.zeros(len(pial_points), dtype=int)
    category[zone & ~full_network] = 1
    category[full_network & ~zone] = 2
    category[eligible] = 3
    categorical_surface(
        top_axes[2], pial_points, pial_triangles, category,
        {1: ZONE_GOLD, 2: TARGET_CYAN, 3: OVERLAP_TEAL})
    format_3d(
        top_axes[2], pial_points,
        "C  Eligible target\nteal = network ∩ anatomical zone",
        crop=cortical_crop, view=(8, 180), equal_limits=False, zoom=1.28)

    component_category = np.zeros(len(pial_points), dtype=int)
    component_category[np.isin(clusters, meaningful_ids)] = 1
    component_category[np.isin(clusters, excluded_ids)] = 2
    categorical_surface(
        top_axes[3], pial_points, pial_triangles, component_category,
        {1: COMPONENT_BLUE, 2: EXCLUDED_MAGENTA})
    format_3d(
        top_axes[3], pial_points,
        (f"D  Connected components\n{len(meaningful_ids)} retained | "
         f"{len(excluded_ids)} components excluded"),
        crop=cortical_crop, view=(8, 180), equal_limits=False, zoom=1.28)

    ax_projection = fig.add_subplot(layout[1, 0:3], projection="3d")
    ax_projection.computed_zorder = False
    light_scalp_surface(
        ax_projection, skin_small_points, skin_small_triangles, alpha=0.18)
    mask_surface(
        ax_projection, pial_points, pial_triangles, eligible,
        target_color, background="#aeb5bd")
    overlay_mask(
        ax_projection, skin_points, skin_triangles, candidate_mask,
        SCALP_SUPPORT, alpha=0.48, zorder=8)
    marker_offset = 1.2 * scalp_normal
    projected_display = projected_points + marker_offset
    coarse_display = coarse_points + marker_offset
    fine_display = fine_points + marker_offset
    ax_projection.scatter(
        projected_display[:, 0], projected_display[:, 1], projected_display[:, 2],
        s=4, color=OVERLAP_TEAL, alpha=0.58, edgecolors="none",
        depthshade=False, zorder=11)
    anchor_point = skin_points[mandatory_vertices[0]] + marker_offset
    draw_point(ax_projection, anchor_point, WINNER_GOLD, marker="D", size=85,
               edgecolor=INK)
    format_3d(
        ax_projection, np.vstack((skin_small_points, pial_points)),
        ("E  Target projection to scalp\n"
         "teal = projected cortex\ngold diamond = centroid anchor"),
        crop=scalp_crop, view=scalp_view, equal_limits=True, zoom=1.10)
    draw_scale_ruler(ax_projection, scalp_crop, length_mm=20.0)

    ax_coarse = fig.add_subplot(layout[1, 3:6], projection="3d")
    ax_coarse.computed_zorder = False
    light_scalp_surface(ax_coarse, skin_small_points, skin_small_triangles)
    overlay_mask(
        ax_coarse, skin_points, skin_triangles, candidate_mask,
        SCALP_SUPPORT, alpha=0.90, zorder=5)
    ax_coarse.scatter(
        coarse_display[:, 0], coarse_display[:, 1], coarse_display[:, 2],
        s=80, color=GRID_RED, edgecolors="white", linewidths=1.3,
        depthshade=False, zorder=13)
    draw_point(
        ax_coarse, skin_points[mandatory_vertices[0]] + marker_offset, WINNER_GOLD,
        marker="D", size=78, edgecolor=INK, zorder=16)
    coarse_index = int(coarse_winner["center_id"]) - 1
    draw_winner(ax_coarse, coarse_display[coarse_index],
                f"C{int(coarse_winner['center_id'])}")
    format_3d(
        ax_coarse, candidate_points,
        (f"F  Coarse search | center {int(coarse_winner['center_id'])}, "
         f"{float(coarse_winner['orientation_degrees']):g}° won\n"
         f"{len(coarse_grid)} centers × "
         f"{len(coarse_scores) // len(coarse_grid)} orientations = "
         f"{len(coarse_scores)} poses | "
         f"{100 * coverage_fraction:.1f}% {coverage_measure_label} coverage"),
        crop=scalp_crop, view=scalp_view, equal_limits=True, zoom=1.07)

    ax_fine = fig.add_subplot(layout[1, 6:9], projection="3d")
    ax_fine.computed_zorder = False
    light_scalp_surface(ax_fine, skin_small_points, skin_small_triangles)
    overlay_mask(
        ax_fine, skin_points, skin_triangles, candidate_mask,
        SCALP_SUPPORT, alpha=0.90, zorder=5)
    overlay_mask(
        ax_fine, skin_points, skin_triangles, local_zone,
        "#c9a8d5", alpha=0.88, zorder=6)
    ax_fine.scatter(
        fine_display[:, 0], fine_display[:, 1], fine_display[:, 2],
        s=74, color=FINE_PURPLE, edgecolors="white", linewidths=1.2,
        depthshade=False, zorder=13)
    if len(nearby_coarse):
        ax_fine.scatter(
            coarse_display[nearby_coarse, 0],
            coarse_display[nearby_coarse, 1],
            coarse_display[nearby_coarse, 2],
            s=115, facecolors="none", edgecolors=GRID_RED, linewidths=2.0,
            depthshade=False, zorder=16)
    draw_point(
        ax_fine, coarse_display[coarse_index], "white", marker="o", size=125,
        edgecolor=INK, linewidth=2.0, zorder=17)
    draw_point_label(ax_fine, coarse_display[coarse_index],
                     'Coarse winner', (0.05, 0.90))
    fine_index = int(fine_winner["center_id"]) - 1
    relative_gain = (
        float(fine_winner["mean_on_target_score"]) /
        float(coarse_winner["mean_on_target_score"]) - 1.0)
    draw_winner(ax_fine, fine_display[fine_index])
    draw_point_label(ax_fine, fine_display[fine_index],
                     f"Fine winner F{int(fine_winner['center_id'])}", (0.48, 0.12))
    panel_g_title = (
        f"G  Fine refinement | center {int(fine_winner['center_id'])}, "
        f"{float(fine_winner['orientation_degrees']):g}° won\n"
        f"{len(fine_grid)} centers × "
        f"{len(fine_scores) // len(fine_grid)} orientations = "
        f"{len(fine_scores)} poses | {100 * relative_gain:+.1f}% vs coarse")
    format_3d(
        ax_fine, candidate_points, panel_g_title,
        crop=crop_from_points(fine_display, 7.0), view=scalp_view,
        equal_limits=True, zoom=1.07)
    draw_scale_ruler(ax_fine, crop_from_points(fine_display, 7.0), length_mm=10)

    ax_robust = fig.add_subplot(layout[1, 9:12], projection="3d")
    ax_robust.computed_zorder = False
    light_scalp_surface(ax_robust, skin_small_points, skin_small_triangles)
    overlay_mask(
        ax_robust, skin_points, skin_triangles, candidate_mask,
        SCALP_SUPPORT, alpha=0.90, zorder=5)
    if robust_winner is None:
        panel_h_title = "H  Placement-error robustness\nnot evaluated"
        robust_crop = scalp_crop
    else:
        nominal_point = fine_display[fine_index]
        robust_fine_index = int(robust_winner["fine_center_id"]) - 1
        robust_point = fine_display[robust_fine_index]
        default_radius = float(robust_summary["default_position_error_mm"])
        winner_finalist = int(robust_summary["winner_finalist_id"])
        robust_xyz = grid_points(robust_grid)
        sampled_center_ids = sorted({
            int(row["robust_center_id"])
            for row in robust_perturbations
            if int(row["finalist_id"]) == winner_finalist
            and float(row["actual_geodesic_radius_mm"]) <= default_radius + 1e-8
        })
        sampled_points = robust_xyz[np.asarray(sampled_center_ids) - 1]
        sampled_display = sampled_points + marker_offset
        robust_crop = crop_from_points(
            np.vstack((sampled_display, nominal_point, robust_point)), 6.0)
        ax_robust.scatter(
            sampled_display[:, 0], sampled_display[:, 1], sampled_display[:, 2],
            s=34, facecolors="#f4f5f6", edgecolors=INK, linewidths=0.8,
            depthshade=False, zorder=15)
        draw_point(ax_robust, nominal_point, INK, marker="x", size=125,
                   linewidth=2.5, zorder=19)
        draw_winner(ax_robust, robust_point)
        draw_point_label(ax_robust, nominal_point, 'Nominal best', (0.05, 0.88))
        draw_point_label(ax_robust, robust_point, 'Robust prescription', (0.45, 0.12))
        nominal_rows = [
            row for row in robust_sensitivity
            if int(row["fine_center_id"]) == int(fine_winner["center_id"])
            and np.isclose(float(row["position_error_mm"]), default_radius)
            and np.isclose(float(row["orientation_error_degrees"]),
                           float(robust_summary[
                               "default_orientation_error_degrees"]))
        ]
        robust_tail = float(robust_summary["winner_lower_tail_mean"])
        robust_nominal_loss = (
            float(robust_winner["mean_on_target_score"]) /
            float(fine_winner["mean_on_target_score"]) - 1.0)
        tail_gain = (robust_tail / float(
            nominal_rows[0]["lower_tail_mean_10_percent"]) - 1.0
            if nominal_rows else np.nan)
        tail_gain_text = (
            f"{100 * tail_gain:+.1f}% lower-tail vs nominal winner"
            if np.isfinite(tail_gain) else "nominal winner not a finalist")
        panel_h_title = (
            f"H  Placement-error robustness | fine center "
            f"{int(robust_winner['fine_center_id'])}, "
            f"{float(robust_winner['orientation_degrees']):g}°\n"
            f"≤{default_radius:g} mm / ≤"
            f"{float(robust_summary['default_orientation_error_degrees']):g}° | "
            f"nominal {100 * robust_nominal_loss:+.1f}%\n{tail_gain_text}")
    format_3d(
        ax_robust, candidate_points, panel_h_title,
        crop=robust_crop, view=scalp_view, equal_limits=True, zoom=1.07)
    draw_scale_ruler(ax_robust, robust_crop, length_mm=10)

    for ax in top_axes + [ax_projection, ax_coarse, ax_fine, ax_robust]:
        ax.title.set_fontsize(9.8)

    fig.suptitle(
        f"{cfg['subject']} | {target_display} | "
        "Target selection and search refinement",
        fontsize=23, fontweight="bold", color=INK, y=0.972)
    legend_handles = [
        Patch(facecolor=ZONE_GOLD, label="anatomical zone"),
        Patch(facecolor=target_color, label="target network"),
        Patch(facecolor=OVERLAP_TEAL, label="eligible overlap"),
        Patch(facecolor=COMPONENT_BLUE, label="retained component"),
        Patch(facecolor=EXCLUDED_MAGENTA, label="excluded component"),
        Patch(facecolor=SCALP_SUPPORT, label="candidate scalp domain"),
        Line2D([], [], marker="o", linestyle="none", color=OVERLAP_TEAL,
               markersize=5, label="projected target cortex"),
        Line2D([], [], marker="D", linestyle="none", markerfacecolor=WINNER_GOLD,
               markeredgecolor=INK, markersize=6, label="centroid anchor"),
        Line2D([], [], marker="o", linestyle="none", markerfacecolor=GRID_RED,
               markeredgecolor="white", markersize=7, label="coarse center"),
        Line2D([], [], marker="o", linestyle="none", markerfacecolor=FINE_PURPLE,
               markeredgecolor="white", markersize=7, label="fine center"),
        Line2D([], [], marker="o", linestyle="none", markerfacecolor="none",
               markeredgecolor=GRID_RED, markeredgewidth=1.6, markersize=7,
               label="nearby coarse center"),
        Line2D([], [], marker="o", linestyle="none", markerfacecolor="white",
               markeredgecolor=INK, markeredgewidth=1.6, markersize=7,
               label="parent coarse winner"),
        Line2D([], [], marker="o", linestyle="none", markerfacecolor=WINNER_GOLD,
               markeredgecolor=INK, markersize=7,
               label=("robust prescription" if robust_winner is not None
                      else "stage winner")),
    ]
    if robust_winner is not None:
        legend_handles.extend([
            Line2D([], [], marker="x", linestyle="none", color=INK,
                   markeredgewidth=1.8, markersize=7,
                   label="nominal fine maximum"),
            Line2D([], [], marker="o", linestyle="none",
                   markerfacecolor="#d2d6da", markeredgecolor=INK,
                   markersize=5, label="sampled placement error"),
        ])
    fig.legend(
        handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, 0.052),
        ncol=8, frameon=False, fontsize=6.7, handlelength=1.3,
        columnspacing=1.35, labelspacing=0.55)
    coarse_policy = cfg.get("coarse_grid_policy", {})
    relative_floor = float(coarse_policy.get(
        "minimum_design_cluster_relative_weighted_area_fraction", 0.0))
    footer = (
        f"Working default | component ≥"
        f"{int(coarse_policy.get('minimum_design_cluster_vertices', 10))} vertices, "
        f"≥{float(coarse_policy.get('minimum_design_cluster_area_mm2', 25)):g} mm², "
        f"and ≥{100 * relative_floor:g}% of largest {component_mass_label} | "
        f"coarse {coverage_measure_label} coverage {100 * coverage_fraction:.1f}% "
        f"within {coverage['coverage_radius_mm']:.0f} mm | "
        f"fine radius {float(fine_summary['radius_mm']):g} mm, spacing "
        f"{float(fine_summary['spacing_mm']):g} mm")
    if runtime_overhead is not None:
        footer += (
            f" | robustness +"
            f"{100 * runtime_overhead['robustness_runtime_overhead_fraction']:.0f}% "
            f"runtime ({int(runtime_overhead['robustness_poses'])} poses)")
    fig.text(0.5, 0.010, footer, ha="center", va="bottom",
             fontsize=9, color="#5e6a75")

    image_path = output / "TargetSelectionSearchSummary.png"
    fig.savefig(image_path, dpi=190, facecolor=PAPER_BG)
    plt.close(fig)

    metrics = {
        "subject": cfg["subject"],
        "network": cfg["network"],
        "map_mode": map_mode,
        "primary_selection_metric": (
            "binary_target_surface_area_fraction"
            if map_mode == "binary" else
            "mean_native_target_probability"),
        "coarse_coverage_measure": coverage.get(
            "coverage_measure", "probability_weighted_surface_area"),
        "coarse_coverage_fraction": coverage_fraction,
        "report_page_role": "target_selection_and_search_refinement",
        "display_name": target_display,
        "anatomical_zone": anatomical_zone,
        "eligible_target_definition": "target network intersect anatomical zone",
        "meaningful_component_ids": meaningful_ids,
        "excluded_component_ids": excluded_ids,
        "coarse": {
            **coverage,
            "winner_center_id": int(coarse_winner["center_id"]),
            "winner_orientation_degrees": float(
                coarse_winner["orientation_degrees"]),
            "winner_mean_on_target_score": float(
                coarse_winner["mean_on_target_score"]),
            "poses": len(coarse_scores),
        },
        "fine": {
            **fine_summary,
            "winner_center_id": int(fine_winner["center_id"]),
            "winner_orientation_degrees": float(
                fine_winner["orientation_degrees"]),
            "winner_mean_on_target_score": float(
                fine_winner["mean_on_target_score"]),
            "relative_improvement_over_coarse": relative_gain,
        },
        "robustness": ({
            **robust_summary,
            "selected_pose": robust_winner,
            "selection_source": str(
                robust_analysis_dir / "BestRobustPose.json"),
            "runtime_overhead": runtime_overhead,
        } if robust_winner is not None else None),
        "visualization_only": True,
        "html_report_contract": {
            "primary_static_asset": image_path.name,
            "manifest": "TargetSelectionSearchSummary.json",
        },
        "source_paths": {
            "configuration": str(Path(args.config).resolve()),
            "target_preparation": str(target_dir),
            "coverage_plan": str(coverage_dir),
            "coarse_result": str(coarse_dir),
            "fine_plan": str(fine_plan_dir),
            "fine_result": str(fine_dir),
            "robustness_plan": (
                str(robust_plan_dir) if robust_plan_dir is not None else None),
            "robustness_analysis": (
                str(robust_analysis_dir)
                if robust_analysis_dir is not None else None),
            "canonical_colors": str(Path(args.priors).resolve()),
        },
    }
    (output / "TargetSelectionSearchSummary.json").write_text(
        json.dumps(metrics, indent=2) + "\n")
    print(json.dumps({
        "image": str(image_path),
        "manifest": str(output / "TargetSelectionSearchSummary.json"),
    }, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--target-preparation", required=True)
    parser.add_argument("--coverage-plan", required=True)
    parser.add_argument("--coarse-result", required=True)
    parser.add_argument("--fine-plan", required=True)
    parser.add_argument("--fine-result", required=True)
    parser.add_argument("--robust-plan")
    parser.add_argument("--robust-analysis")
    parser.add_argument("--priors", required=True)
    parser.add_argument("--output", required=True)
    render(parser.parse_args())


if __name__ == "__main__":
    main()
