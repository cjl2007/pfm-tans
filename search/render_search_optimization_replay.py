#!/usr/bin/env python3
"""Render a rapid, acquisition-ordered replay of coarse and fine TANS search."""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_tans_search_replay")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import cm, colors
from matplotlib.lines import Line2D
from mpl_toolkits.mplot3d import proj3d
from mpl_toolkits.mplot3d.art3d import Line3DCollection
import nibabel as nib
import numpy as np
from PIL import Image, ImageDraw
from scipy.io import loadmat
import trimesh

MODULE = Path(__file__).resolve().parent
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from render_optimal_pose_qc import (  # noqa: E402
    combined_cortex_surface,
    INK,
    PAPER_BG,
    data_surface,
    format_3d,
    gray_surface,
    load_gifti_surface,
    mask_boundary_segments,
    priors_palette,
    projected_surface_outline_segments,
    surface_collection,
    vertex_cluster,
    view_from_direction,
)
from simulated_pose_geometry import saved_pose_matrix
from selected_pose import selected_pose_paths
from pose_prescription import canonicalize_pose_matrix, resolve_handle_policy
from render_target_selection_report import (  # noqa: E402
    FINE_PURPLE,
    GRID_RED,
    SCALP_SUPPORT,
    WINNER_GOLD,
    light_scalp_surface,
    overlay_mask,
)


STAGE_COLORS = {"coarse": GRID_RED, "fine": FINE_PURPLE}


def overlay_projected_target_outline(image, ax, segment_groups):
    """Raster-overlay a retained-target footprint when cortex occludes it."""
    matrix = ax.get_proj()
    height = image.height
    projected = []
    for segments in segment_groups:
        for segment in segments:
            x, y, _ = proj3d.proj_transform(
                segment[:, 0], segment[:, 1], segment[:, 2], matrix)
            display = ax.transData.transform(np.column_stack((x, y)))
            projected.append([
                (float(px), float(height - py)) for px, py in display])
    if not projected:
        return
    drawer = ImageDraw.Draw(image)
    ink = tuple(int(round(255 * value)) for value in colors.to_rgb(INK))
    for segment in projected:
        drawer.line(segment, fill=ink, width=6)
    for segment in projected:
        drawer.line(segment, fill="white", width=3)


def project_to_image(ax, point, image_height):
    """Project a 3D point into the final raster coordinate system."""
    x, y, _ = proj3d.proj_transform(*np.asarray(point, dtype=float), ax.get_proj())
    px, py = ax.transData.transform((x, y))
    return np.asarray([px, image_height - py], dtype=float)


def overlay_project_arrow(image, ax, start, end):
    """Draw one screen-space arrow so its head and shaft stay aligned."""
    start_xy = project_to_image(ax, start, image.height)
    end_xy = project_to_image(ax, end, image.height)
    direction = end_xy - start_xy
    length = float(np.linalg.norm(direction))
    if length <= 1e-6:
        return
    direction /= length
    perpendicular = np.asarray([-direction[1], direction[0]])
    drawer = ImageDraw.Draw(image)
    ink = tuple(int(round(255 * value)) for value in colors.to_rgb(INK))
    drawer.line([tuple(start_xy), tuple(end_xy)], fill="white", width=7)
    drawer.line([tuple(start_xy), tuple(end_xy)], fill=ink, width=3)
    for fill, head_length, half_width in (
            ("white", 15.0, 9.0), (ink, 11.5, 6.0)):
        base = end_xy - head_length * direction
        drawer.polygon([
            tuple(end_xy),
            tuple(base + half_width * perpendicular),
            tuple(base - half_width * perpendicular),
        ], fill=fill)


def require_file(path, label):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def read_tsv(path):
    with require_file(path, "TSV table").open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def as_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes")


def pose_key(center_id, orientation):
    return int(center_id), round(float(orientation), 6)


def ordered_stage_poses(stage, root):
    """Recover actual center-major, orientation-minor simulation order."""
    score_rows = read_tsv(root / "CoarsePoseScores.tsv")
    lookup = {
        pose_key(row["center_id"], row["orientation_degrees"]): row
        for row in score_rows
    }
    poses = []
    for center_dir in sorted(path for path in root.glob("Center_*")
                             if path.is_dir()):
        center_id = int(center_dir.name.split("_")[-1])
        benchmark = json.loads((center_dir / "Benchmark.json").read_text())
        placements = read_tsv(center_dir / "Placements.tsv")
        orientations = [float(value) for value in benchmark["orientations_degrees"]]
        if len(placements) != len(orientations):
            raise ValueError(f"Placement count mismatch in {center_dir}")
        for map_index, (orientation, placement) in enumerate(
                zip(orientations, placements)):
            score = lookup.get(pose_key(center_id, orientation))
            if score is None:
                raise ValueError(
                    f"Missing score for {stage} center {center_id}, {orientation:g}")
            poses.append({
                "stage": stage,
                "root": root,
                "center_dir": center_dir,
                "center_id": center_id,
                "map_index": map_index,
                "orientation_degrees": orientation,
                "benchmark": benchmark,
                "placement": placement,
                "score": score,
            })
    if len(poses) != len(score_rows):
        raise ValueError(
            f"Recovered {len(poses)} {stage} poses but score table has "
            f"{len(score_rows)}")
    return poses


def cifti_hemi_full(image, map_index, structure, vertex_count):
    axis = image.header.get_axis(1)
    data = np.asanyarray(image.dataobj)
    output = np.full(vertex_count, np.nan, dtype=float)
    for name, data_slice, model in axis.iter_structures():
        if name == structure:
            output[np.asarray(model.vertex, dtype=int)] = data[map_index, data_slice]
            return output
    raise ValueError(f"CIFTI lacks {structure}: {image.get_filename()}")


def load_coil_base(path):
    coil = trimesh.load_mesh(str(path), process=False)
    if isinstance(coil, trimesh.Scene):
        coil = trimesh.util.concatenate(tuple(coil.geometry.values()))
    return vertex_cluster(
        np.asarray(coil.vertices, dtype=float),
        np.asarray(coil.faces, dtype=int), 1.2)


def coil_pose(base_points, center, y_direction, outward_normal, distance_mm):
    center = np.asarray(center, dtype=float)
    normal = np.asarray(outward_normal, dtype=float)
    normal /= np.linalg.norm(normal)
    z_axis = -normal
    y_axis = np.asarray(y_direction, dtype=float) - center
    y_axis -= z_axis * np.dot(y_axis, z_axis)
    y_axis /= np.linalg.norm(y_axis)
    x_axis = np.cross(y_axis, z_axis)
    rotation = np.column_stack((x_axis, y_axis, z_axis))
    return base_points @ rotation.T + center + distance_mm * normal


def draw_head_panel(ax, context, pose, left_field, right_field, coil_points):
    ax.computed_zorder = False
    options = context["visualization"]
    target_segments = []
    gray_surface(
        ax, *context["skin_small"], options["head_color"], alpha=0.018)
    combined_cortex_surface(
        ax, [(*context['left_pial'], left_field), (*context['right_pial'], right_field)],
        context['field_norm'])
    for points, triangles, field, target in (
            (*context["left_pial"], left_field, context["left_target"]),
            (*context["right_pial"], right_field, context["right_target"])):
        target_segments.append(mask_boundary_segments(
            points, triangles, target))
    coil_rgba = np.tile(
        np.r_[colors.to_rgb(options["coil_color"]), 1.0],
        (len(context["coil_faces"]), 1))
    surface_collection(
        ax, coil_points, context["coil_faces"], coil_rgba,
        alpha=0.008, zorder=5)
    silhouette = projected_surface_outline_segments(
        coil_points, context["coil_faces"], context["view_normal"],
        resolution=256)
    ax.add_collection3d(Line3DCollection(
        silhouette, colors=colors.to_rgba(options["coil_edge_color"], 0.82),
        linewidths=1.2, zorder=6))
    score = 100.0 * float(pose["score"]["mean_on_target_score"])
    title = (
        f"A  E-field at current pose | {pose['stage'].upper()} "
        f"C{pose['center_id']} | {pose['orientation_degrees']:g} deg\n"
        f"weighted on-target {score:.2f}% | black/white = projected retained target")
    format_3d(
        ax, context["head_points"], title, crop=context["head_crop"],
        view=context["fixed_view"], equal_limits=False, zoom=1.10)
    ax.title.set_fontsize(11)
    return target_segments


def draw_current_marker(ax, point, tangent, stage_color):
    point = np.asarray(point, dtype=float)
    tangent = np.asarray(tangent, dtype=float)
    tangent /= np.linalg.norm(tangent)
    ax.scatter(
        *point, s=235, facecolors="none", edgecolors="white",
        linewidths=4.5, depthshade=False, zorder=20)
    ax.scatter(
        *point, s=205, facecolors="none", edgecolors=INK,
        linewidths=2.2, depthshade=False, zorder=21)
    ax.scatter(
        *point, s=62, facecolors=stage_color, edgecolors=INK,
        linewidths=1.0, depthshade=False, zorder=22)
    start = point - 7.0 * tangent
    end = point + 7.0 * tangent
    return start, end


def draw_search_panel(ax, context, pose, pose_index, final_prescription=False):
    light_scalp_surface(ax, *context["skin_small"], alpha=0.98)
    overlay_mask(
        ax, *context["skin_full"], context["candidate_mask"],
        SCALP_SUPPORT, alpha=0.92, zorder=5)
    ax.scatter(
        context["coarse_points"][:, 0], context["coarse_points"][:, 1],
        context["coarse_points"][:, 2], s=28, color=GRID_RED,
        edgecolors="white", linewidths=0.7, alpha=0.46,
        depthshade=False, zorder=10)
    if pose["stage"] == "fine":
        ax.scatter(
            context["fine_points"][:, 0], context["fine_points"][:, 1],
            context["fine_points"][:, 2], s=34, color=FINE_PURPLE,
            edgecolors="white", linewidths=0.7, alpha=0.58,
            depthshade=False, zorder=11)
    center = np.asarray(pose["benchmark"]["center_xyz"], dtype=float)
    normal = np.asarray(pose["benchmark"]["scalp_normal"], dtype=float)
    normal /= np.linalg.norm(normal)
    display_center = center + 1.4 * normal
    matrix = saved_pose_matrix(pose['benchmark'], pose['orientation_degrees'])
    if final_prescription:
        matrix, _ = canonicalize_pose_matrix(matrix, context['handle_policy'])
    tangent = -matrix[:3, 1]
    marker_arrow = draw_current_marker(
        ax, display_center, tangent, STAGE_COLORS[pose["stage"]])
    if final_prescription:
        detail = (
            f"FINAL | C{pose['center_id']} | "
            f"{pose['orientation_degrees']:g} deg | source "
            f"{pose_index + 1}")
    else:
        detail = (
            f"{pose['stage'].upper()} | C{pose['center_id']} | "
            f"{pose['orientation_degrees']:g} deg | pose {pose_index + 1}/"
            f"{len(context['poses'])}")
    title = f"B  Search-space replay\n{detail}"
    format_3d(
        ax, context["candidate_points"], title,
        crop=context["scalp_crop"], view=context["fixed_view"],
        equal_limits=True, zoom=1.08)
    ax.title.set_fontsize(11)
    return marker_arrow


def draw_score_trace(ax, context, history_index, displayed_pose_index):
    x = np.arange(1, history_index + 2)
    values = context["scores_percent"][:history_index + 1]
    best = np.maximum.accumulate(values)
    coarse_count = context["coarse_count"]
    ax.set_facecolor(PAPER_BG)
    ax.axvspan(0.5, coarse_count + 0.5, color=GRID_RED, alpha=0.045)
    ax.axvspan(
        coarse_count + 0.5, len(context["poses"]) + 0.5,
        color=FINE_PURPLE, alpha=0.045)
    ax.plot(x, values, color=context["target_color"], linewidth=1.0,
            alpha=0.48,
            label="current pose")
    ax.plot(x, best, color=context["target_color"], linewidth=2.4,
            label="best so far")
    displayed_x = displayed_pose_index + 1
    displayed_value = context["scores_percent"][displayed_pose_index]
    ax.scatter(displayed_x, displayed_value, s=58,
               color=context["target_color"],
               edgecolors="white", linewidths=1.1, zorder=5)
    ax.scatter(x[-1], best[-1], s=70,
               facecolors=context["target_color"], edgecolors=INK,
               linewidths=1.0, zorder=6)
    ax.axvline(coarse_count + 0.5, color="#9aa2aa", linewidth=0.9)
    y_low, y_high = context["score_limits"]
    ax.set_xlim(0.5, len(context["poses"]) + 0.5)
    ax.set_ylim(y_low, y_high)
    ax.set_ylabel("Weighted on-target (%)", fontsize=9, labelpad=1)
    ax.set_xlabel("Poses evaluated in simulation order", fontsize=9)
    ax.grid(axis="y", color="#d9dde1", linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=8)
    ax.text(
        coarse_count / 2.0, y_high, "COARSE", ha="center", va="bottom",
        fontsize=8, fontweight="bold", color=GRID_RED)
    ax.text(
        coarse_count + (len(context["poses"]) - coarse_count) / 2.0,
        y_high, "FINE", ha="center", va="bottom", fontsize=8,
        fontweight="bold", color=FINE_PURPLE)
    ax.legend(loc="lower right", frameon=False, fontsize=8, ncol=2)
    ax.set_title(
        "C  Optimization history", fontsize=11, fontweight="bold",
        color=INK, pad=8)


def draw_metric_panel(ax, context, pose_index, history_index,
                      final_prescription=False):
    pose = context["poses"][pose_index]
    score = pose["score"]
    current = 100.0 * float(score["mean_on_target_score"])
    best = float(np.max(context["scores_percent"][:history_index + 1]))
    binary = 100.0 * float(score["binary_score_at_99_5_percent"])
    ratio = float(score["target_to_global_peak_ratio"])
    peak_on_target = np.isclose(ratio, 1.0, atol=2e-6, rtol=0.0)
    passes_gate = as_bool(score["passes_target_peak_ratio"])
    ax.set_facecolor(PAPER_BG)
    ax.axis("off")
    heading = (("D  Final robust prescription" if context.get('robustness_enabled', True)
                else "D  Final nominal fine prescription") if final_prescription
               else "D  Live targeting readout")
    ax.text(0.02, 0.96, heading, ha="left", va="top",
            fontsize=11, fontweight="bold", color=INK,
            transform=ax.transAxes)
    ax.text(0.02, 0.82, f"CURRENT  {current:.2f}%", fontsize=18,
            fontweight="bold", color=context["target_color"],
            transform=ax.transAxes)
    ax.text(0.54, 0.82, f"BEST  {best:.2f}%", fontsize=18,
            fontweight="bold", color=context["target_color"],
            transform=ax.transAxes)

    ax.text(0.02, 0.66, "99.5% hotspot composition", fontsize=8.5,
            fontweight="bold", color=INK, transform=ax.transAxes)
    bar_y, bar_h = 0.55, 0.075
    ax.add_patch(plt.Rectangle(
        (0.02, bar_y), 0.96, bar_h, transform=ax.transAxes,
        facecolor="#d9dde1", edgecolor=INK, linewidth=0.8))
    target_width = 0.96 * binary / 100.0
    ax.add_patch(plt.Rectangle(
        (0.02, bar_y), target_width, bar_h, transform=ax.transAxes,
        facecolor=context["target_color"], edgecolor=INK, linewidth=1.2))
    if binary >= 9:
        rgb = np.asarray(colors.to_rgb(context["target_color"]))
        luminance = float(np.dot(rgb, [0.2126, 0.7152, 0.0722]))
        ax.text(
            0.02 + target_width / 2.0, bar_y + bar_h / 2.0,
            f"TARGET {binary:.1f}%", ha="center", va="center",
            fontsize=8, fontweight="bold",
            color=INK if luminance > 0.58 else "white",
            transform=ax.transAxes)
    ax.text(0.98, bar_y + bar_h / 2.0,
            f"off-target {100.0 - binary:.1f}%", ha="right", va="center",
            fontsize=7.5, color=INK, transform=ax.transAxes)

    status_color = "#14804a" if peak_on_target else "#b42318"
    status = "ON TARGET" if peak_on_target else "OFF TARGET"
    ax.text(0.02, 0.40, f"GLOBAL E-FIELD PEAK: {status}", fontsize=12,
            fontweight="bold", color=status_color, transform=ax.transAxes)
    ax.text(
        0.02, 0.31,
        f"target maximum / global maximum = {100.0 * ratio:.2f}%",
        fontsize=9, color=INK, transform=ax.transAxes)
    gate_color = "#14804a" if passes_gate else "#b42318"
    gate_text = "passes" if passes_gate else "fails"
    ax.text(
        0.02, 0.22, f"Peak-proximity gate: {gate_text} (minimum 95%)",
        fontsize=9, fontweight="bold", color=gate_color,
        transform=ax.transAxes)

    progress = (history_index + 1) / len(context["poses"])
    ax.add_patch(plt.Rectangle(
        (0.02, 0.08), 0.96, 0.035, transform=ax.transAxes,
        facecolor="#e3e6e8", edgecolor="none"))
    ax.add_patch(plt.Rectangle(
        (0.02, 0.08), 0.96 * progress, 0.035, transform=ax.transAxes,
        facecolor=STAGE_COLORS[pose["stage"]], edgecolor="none"))
    ax.text(
        0.50, 0.02,
        f"{history_index + 1} / {len(context['poses'])} search poses evaluated",
        ha="center", va="bottom", fontsize=8, color="#5e6a75",
        transform=ax.transAxes)


def render_frame(context, pose_index, history_index=None,
                 final_prescription=False):
    if history_index is None:
        history_index = pose_index
    pose = context["poses"][pose_index]
    cache_key = (pose["stage"], pose["center_id"])
    if context.get("field_cache_key") != cache_key:
        context["field_image"] = nib.load(
            str(pose["center_dir"] / "magnE.dtseries.nii"))
        context["field_cache_key"] = cache_key
    image = context["field_image"]
    left_field = cifti_hemi_full(
        image, pose["map_index"], "CIFTI_STRUCTURE_CORTEX_LEFT",
        len(context["left_pial"][0]))
    right_field = cifti_hemi_full(
        image, pose["map_index"], "CIFTI_STRUCTURE_CORTEX_RIGHT",
        len(context["right_pial"][0]))

    center = np.asarray(pose["benchmark"]["center_xyz"], dtype=float)
    y_direction = np.asarray([
        float(pose["placement"][f"pos_ydir_{axis}"]) for axis in "xyz"])
    matrix = saved_pose_matrix(pose['benchmark'], pose['orientation_degrees'])
    if final_prescription:
        matrix, _ = canonicalize_pose_matrix(matrix, context['handle_policy'])
    coil_points = context['coil_base'] @ matrix[:3, :3].T + matrix[:3, 3]

    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor=PAPER_BG)
    outer = fig.add_gridspec(
        1, 3, width_ratios=[1.14, 0.92, 1.03], wspace=0.085,
        left=0.018, right=0.985, bottom=0.075, top=0.90)
    ax_head = fig.add_subplot(outer[0, 0], projection="3d")
    target_segments = draw_head_panel(
        ax_head, context, pose, left_field, right_field, coil_points)
    ax_search = fig.add_subplot(outer[0, 1], projection="3d")
    ax_search.computed_zorder = False
    marker_arrow = draw_search_panel(
        ax_search, context, pose, pose_index,
        final_prescription=final_prescription)
    right = outer[0, 2].subgridspec(
        2, 1, height_ratios=[0.58, 0.42], hspace=0.12)
    draw_score_trace(
        fig.add_subplot(right[0, 0]), context, history_index, pose_index)
    draw_metric_panel(
        fig.add_subplot(right[1, 0]), context, pose_index, history_index,
        final_prescription=final_prescription)
    fig.suptitle(
        f"{context['subject']} | {context['display_name']} | "
        "TANS search optimization replay",
        fontsize=22, fontweight="bold", color=INK, y=0.972)
    fig.text(
        0.5, 0.018,
        "Every displayed frame is one simulated center-orientation pose; " +
        ("robustness validation is summarized by the final prescription"
         if context.get('robustness_enabled', True) else
         "placement-error robustness was not evaluated (disabled by configuration)"),
        ha="center", va="bottom", fontsize=8.5, color="#5e6a75")
    fig.canvas.draw()
    rgb_image = Image.fromarray(
        np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy())
    overlay_projected_target_outline(rgb_image, ax_head, target_segments)
    overlay_project_arrow(rgb_image, ax_search, *marker_arrow)
    rgb = np.asarray(rgb_image).copy()
    return fig, rgb


def open_encoder(output, width, height, fps):
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-r", f"{fps:g}", "-i", "-", "-an", "-c:v", "libx264",
        "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    ]
    return subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


def build_render_sequence(total_poses, stride, final_match):
    """Return (displayed pose, completed history, is prescription) frames."""
    if total_poses < 1:
        raise ValueError("total_poses must be positive")
    if stride < 1:
        raise ValueError("stride must be positive")
    if not 0 <= final_match < total_poses:
        raise ValueError("final_match is outside the search-pose range")
    selected = list(range(0, total_poses, stride))
    if selected[-1] != total_poses - 1:
        selected.append(total_poses - 1)
    sequence = [(index, index, False) for index in sorted(set(selected))]
    if sequence[-1][0] != final_match:
        sequence.append((final_match, total_poses - 1, True))
    else:
        sequence[-1] = (final_match, total_poses - 1, True)
    return sequence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--priors", required=True)
    parser.add_argument("--coil-stl", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument(
        "--stride", type=int, default=1,
        help="Render every Nth pose for drafts; final pose is always retained.")
    parser.add_argument("--final-hold-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.fps <= 0 or args.stride < 1 or args.final_hold_seconds < 0:
        raise ValueError("fps and stride must be positive; final hold cannot be negative")

    target_root = Path(args.target_root).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite replay output: {output}")
    output.mkdir(parents=True)
    coarse_root = target_root / "02_CoarseSimulation"
    fine_root = target_root / "04_FineSimulation"
    selected = selected_pose_paths(target_root)
    best_path = selected['best_pose']
    cfg = json.loads((target_root / "CoarseRunConfig.json").read_text())
    poses = (ordered_stage_poses("coarse", coarse_root) +
             ordered_stage_poses("fine", fine_root))
    coarse_count = sum(pose["stage"] == "coarse" for pose in poses)
    best = json.loads(require_file(best_path, "selected prescription").read_text())

    final_match = next(
        (index for index, pose in enumerate(poses)
         if pose["stage"] == "fine"
         and pose["center_id"] == int(best.get("fine_center_id", best['center_id']))
         and np.isclose(pose["orientation_degrees"],
                        float(best["orientation_degrees"]))), None)
    if final_match is None:
        raise ValueError("Selected prescription is absent from fine search poses")
    render_sequence = build_render_sequence(
        len(poses), args.stride, final_match)

    left_pial = load_gifti_surface(cfg["surfaces"]["left_pial"])
    right_pial = load_gifti_surface(cfg["surfaces"]["right_pial"])
    skin_full = load_gifti_surface(cfg["skin"])
    skin_small = vertex_cluster(*skin_full, 2.2)
    coverage_dir = Path(
        cfg["coarse_grid_policy"]["coverage_summary"]).resolve().parent
    grid_mat = loadmat(
        coverage_dir / "SearchGrid.mat", simplify_cells=True)["grid"]
    candidate_vertices = np.asarray(
        grid_mat["candidate_scalp_vertices"], dtype=int).reshape(-1) - 1
    candidate_mask = np.zeros(len(skin_full[0]), dtype=bool)
    candidate_mask[candidate_vertices] = True
    candidate_points = skin_full[0][candidate_vertices]
    scalp_crop = (
        candidate_points.min(axis=0) - 8.0,
        candidate_points.max(axis=0) + 8.0)

    coarse_grid = read_tsv(coverage_dir / "SearchGrid.tsv")
    fine_grid = read_tsv(target_root / "03_FinePlan/SearchGrid.tsv")
    coarse_points = np.asarray([
        [float(row[key]) for key in ("x", "y", "z")]
        for row in coarse_grid])
    fine_points = np.asarray([
        [float(row[key]) for key in ("x", "y", "z")]
        for row in fine_grid])

    robust_center = selected['result'] / f"Center_{int(best['center_id']):05d}"
    robust_benchmark = json.loads(
        (robust_center / "Benchmark.json").read_text())
    view_normal = -saved_pose_matrix(robust_benchmark, float(best['orientation_degrees']))[:3, 2]
    fixed_view = view_from_direction(view_normal)
    center_xyz = np.asarray([
        float(best[key]) for key in ("x", "y", "z")])
    head_radius = 52.5
    head_crop = (center_xyz - head_radius, center_xyz + head_radius)

    palette = priors_palette(require_file(args.priors, "Priors.mat"))
    target_label = next(
        label for label, (name, _) in palette.items()
        if name == cfg["network"])
    target_image = nib.load(str(require_file(cfg["target_map"], "target map")))
    scoring = cfg.get("scoring", {})
    design_component_ids = [
        int(value) for value in scoring.get("design_component_ids", [])]
    component_path = scoring.get("component_labels")
    component_image = (
        nib.load(str(require_file(component_path, "component labels")))
        if scoring.get("restrict_target_to_design_components", False)
        else None)
    target_masks = {}
    for hemisphere, structure, vertex_count in (
            ("left", "CIFTI_STRUCTURE_CORTEX_LEFT", len(left_pial[0])),
            ("right", "CIFTI_STRUCTURE_CORTEX_RIGHT", len(right_pial[0]))):
        target_values = cifti_hemi_full(
            target_image, 0, structure, vertex_count)
        if component_image is not None:
            component_values = cifti_hemi_full(
                component_image, 0, structure, vertex_count)
            target_masks[hemisphere] = np.isin(
                component_values, design_component_ids)
        else:
            target_masks[hemisphere] = (
                np.isfinite(target_values) & (target_values != 0))
    scores_percent = np.asarray([
        100.0 * float(pose["score"]["mean_on_target_score"])
        for pose in poses])
    score_margin = max(0.15, 0.10 * float(np.ptp(scores_percent)))
    max_peak = max(
        float(pose["score"]["global_peak_efield_v_per_m"])
        for pose in poses)
    coil_base, coil_faces = load_coil_base(
        require_file(args.coil_stl, "coil STL"))
    context = {
        "robustness_enabled": selected['robustness_enabled'],
        "subject": cfg["subject"],
        "handle_policy": resolve_handle_policy(cfg),
        "display_name": cfg.get("display_name", cfg["network"]),
        "poses": poses,
        "coarse_count": coarse_count,
        "scores_percent": scores_percent,
        "score_limits": (
            float(scores_percent.min() - score_margin),
            float(scores_percent.max() + score_margin)),
        "target_color": palette[target_label][1],
        "left_target": target_masks["left"],
        "right_target": target_masks["right"],
        "left_pial": left_pial,
        "right_pial": right_pial,
        "skin_full": skin_full,
        "skin_small": skin_small,
        "candidate_mask": candidate_mask,
        "candidate_points": candidate_points,
        "coarse_points": coarse_points,
        "fine_points": fine_points,
        "scalp_crop": scalp_crop,
        "view_normal": view_normal,
        "fixed_view": fixed_view,
        "head_crop": head_crop,
        "head_points": np.vstack(
            (skin_small[0], left_pial[0], right_pial[0])),
        "coil_base": coil_base,
        "coil_faces": coil_faces,
        "distance_mm": float(cfg["distance_mm"]),
        "field_norm": colors.Normalize(vmin=0.08, vmax=max_peak),
        "visualization": {
            "brain_color": "#87919b",
            "head_color": "#d8c4a3",
            "coil_color": "#3f444a",
            "coil_edge_color": "#20252a",
        },
    }

    movie_path = output / "SearchOptimizationReplay.mp4"
    encoder = None
    manifest = []
    preview_indices = {
        render_sequence[0][0],
        render_sequence[len(render_sequence) // 2][0],
        final_match,
    }
    try:
        for output_index, (pose_index, history_index, is_final) in enumerate(
                render_sequence):
            fig, rgb = render_frame(
                context, pose_index, history_index=history_index,
                final_prescription=is_final)
            if encoder is None:
                height, width = rgb.shape[:2]
                encoder = open_encoder(movie_path, width, height, args.fps)
            encoder.stdin.write(rgb.tobytes())
            if pose_index == coarse_count - 1:
                for _ in range(max(1, int(round(0.7 * args.fps)))):
                    encoder.stdin.write(rgb.tobytes())
            if is_final:
                for _ in range(int(round(args.final_hold_seconds * args.fps))):
                    encoder.stdin.write(rgb.tobytes())
            if pose_index in preview_indices:
                Image.fromarray(rgb).save(
                    output / f"ReplayFrame_pose_{pose_index + 1:03d}.png")
            plt.close(fig)
            pose = poses[pose_index]
            manifest.append({
                "movie_frame": output_index + 1,
                "search_pose_index": pose_index + 1,
                "stage": pose["stage"],
                "center_id": pose["center_id"],
                "orientation_degrees": pose["orientation_degrees"],
                "mean_on_target_score": pose["score"]["mean_on_target_score"],
                "binary_score_at_99_5_percent": pose["score"][
                    "binary_score_at_99_5_percent"],
                "target_to_global_peak_ratio": pose["score"][
                    "target_to_global_peak_ratio"],
                "is_final_prescription": is_final,
                "is_final_robust_prescription": is_final and selected['robustness_enabled'],
            })
            print(
                f"Rendered {output_index + 1}/{len(render_sequence)}: "
                f"{pose['stage']} C{pose['center_id']} "
                f"{pose['orientation_degrees']:g}", flush=True)
    finally:
        if encoder is not None and encoder.stdin is not None:
            encoder.stdin.close()
    if encoder is None:
        raise RuntimeError("No replay frames were rendered")
    stderr = encoder.stderr.read().decode("utf-8", errors="replace")
    return_code = encoder.wait()
    if return_code:
        raise RuntimeError(f"ffmpeg failed ({return_code}): {stderr}")

    with (output / "SearchOptimizationReplayFrames.tsv").open(
            "w", newline="") as handle:
        writer = csv.DictWriter(
            handle, delimiter="\t", fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    summary = {
        "selection_mode": selected['mode'],
        "robustness_status": selected['robustness_status'],
        "subject": cfg["subject"],
        "network": cfg["network"],
        "display_name": context["display_name"],
        "movie": str(movie_path),
        "coarse_poses": coarse_count,
        "fine_poses": len(poses) - coarse_count,
        "total_search_poses": len(poses),
        "rendered_pose_frames": len(render_sequence),
        "stride": args.stride,
        "fps": args.fps,
        "nominal_pose_rate_per_second": args.fps,
        "robustness_display": (
            "final prescription only; perturbations are validation samples, "
            "not candidate search poses" if selected['robustness_enabled'] else
            "not evaluated; nominal fine winner prescribed"),
        "score_trace": "retained eligible-component weighted certainty",
        "peak_status": (
            "ON TARGET only when retained target maximum equals global maximum; "
            "95% peak-proximity gate is reported separately"),
        "visualization_only": True,
    }
    (output / "SearchOptimizationReplay.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
