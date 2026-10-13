#!/usr/bin/env python3
"""Build an offline SM-Hand rMT to treatment-network dose lookup and QC report."""
import argparse
import csv
import html
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_tans_dose_report")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
from scipy.io import loadmat


PAPER = "#f4f6f8"
INK = "#19222c"
GOLD = "#c49100"
TEAL = "#008c95"


def normalize_network_name(value):
    return "".join(character.lower() for character in str(value)
                   if character.isalnum())


def rgb_hex(rgb):
    values = np.asarray(rgb, dtype=float).reshape(-1)[:3]
    if values.max(initial=0.0) > 1.0:
        values = values / 255.0
    values = np.clip(np.rint(255 * values), 0, 255).astype(int)
    return "#" + "".join(f"{value:02x}" for value in values)


def priors_colors(path):
    priors = loadmat(require_file(path, "Priors.mat"), squeeze_me=True,
                     struct_as_record=False)["Priors"]
    labels = [str(value) for value in np.atleast_1d(priors.NetworkLabels)]
    colors = np.asarray(priors.NetworkColors, dtype=float)
    return {normalize_network_name(label): rgb_hex(colors[index])
            for index, label in enumerate(labels)}


def target_color(colors, network):
    aliases = {normalize_network_name(network)}
    if network == "Somatomotor_Hand":
        aliases.update(("smhand", "somatomotorhandnetwork"))
    if network == "CinguloOpercular":
        aliases.update(("actionmode", "cinguloopercularnetwork"))
    matches = [color for label, color in colors.items() if label in aliases]
    if len(matches) != 1:
        raise ValueError(
            f"Cannot uniquely resolve {network!r} in Priors.mat: {len(matches)} matches")
    return matches[0]


def marker_edge(color):
    values = tuple(int(color[index:index + 2], 16) / 255.0
                   for index in (1, 3, 5))
    luminance = 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]
    return "white" if luminance < 0.35 else INK


def require_file(value, label):
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def read_json(path):
    return json.loads(require_file(path, "JSON file").read_text())


def read_calibration(path, column=None):
    rows = []
    with require_file(path, "stimulator calibration CSV").open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if column is None:
            column = next((name for name in (
                "realized_didt_A_per_us", "didt_A_A_per_us")
                if name in fields), None)
        if not column or column not in fields:
            raise ValueError(
                f"Calibration lacks requested dI/dt column {column!r}: {path}")
        for row in reader:
            rows.append((float(row["amplitude_pct_mso"]), float(row[column])))
    values = np.asarray(rows, dtype=float)
    if len(values) < 2 or np.any(np.diff(values[:, 0]) <= 0):
        raise ValueError("Calibration %MSO values must be strictly increasing")
    if np.any(np.diff(values[:, 1]) <= 0):
        raise ValueError("Calibration dI/dt values must be strictly increasing")
    return values[:, 0], values[:, 1], column


def interpolate_checked(x, xp, fp):
    x = np.asarray(x, dtype=float)
    if np.any(x < xp[0]) or np.any(x > xp[-1]):
        raise ValueError(f"Value outside calibrated range [{xp[0]:g}, {xp[-1]:g}]")
    return np.interp(x, xp, fp)


def bounded_grid(start, stop, step):
    """Build an inclusive numeric grid without stepping beyond its bounds."""
    start, stop, step = float(start), float(stop), float(step)
    if not np.isfinite([start, stop, step]).all():
        raise ValueError("Grid bounds and step must be finite")
    if step <= 0 or stop < start:
        raise ValueError("Grid requires a positive step and stop >= start")
    count = int(np.floor((stop - start) / step + 1e-12))
    values = start + step * np.arange(count + 1, dtype=float)
    tolerance = max(1e-12, abs(stop) * 1e-12)
    values = values[values <= stop + tolerance]
    if not len(values) or values[-1] < stop - tolerance:
        values = np.append(values, stop)
    else:
        values[-1] = stop
    return values


def weighted_quantile(values, weights, quantile):
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not np.any(valid):
        raise ValueError("Weighted quantile has no positive finite weights")
    if not 0 <= quantile <= 1:
        raise ValueError("Quantile must be between zero and one")
    values, weights = values[valid], weights[valid]
    order = np.argsort(values, kind="stable")
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights)
    index = int(np.searchsorted(cumulative, quantile * cumulative[-1], side="left"))
    return float(values[min(index, len(values) - 1)])


def cifti_vector(path):
    data = np.asanyarray(nib.load(str(require_file(path, "CIFTI file"))).dataobj)
    if data.ndim != 2 or data.shape[0] != 1:
        raise ValueError(f"Expected one-map CIFTI: {path}; found {data.shape}")
    return np.asarray(data[0], dtype=float)


def selected_field(target_root, variant='selected'):
    from selected_pose import selected_pose_paths
    target_root = Path(target_root).resolve()
    cfg = read_json(target_root / "CoarseRunConfig.json")
    selected = selected_pose_paths(target_root, variant=variant)
    best = read_json(selected['best_pose'])
    center_dir = selected['result'] / f"Center_{int(best['center_id']):05d}"
    benchmark = read_json(center_dir / "Benchmark.json")
    orientations = np.asarray(benchmark["orientations_degrees"], dtype=float)
    matches = np.flatnonzero(np.isclose(orientations, float(best["orientation_degrees"])))
    if len(matches) != 1:
        raise ValueError(f"Cannot locate selected orientation in {center_dir}")
    fields = np.asanyarray(nib.load(str(center_dir / "magnE.dtseries.nii")).dataobj)
    if fields.shape[0] != len(orientations):
        raise ValueError(f"E-field map/orientation mismatch in {center_dir}")
    reference = float(benchmark["didt_aperus"]) / 1e6
    if reference <= 0:
        raise ValueError("Reference dI/dt must be positive")
    return {
        "root": target_root,
        "config": cfg,
        "best": best,
        "selection_mode": selected['mode'],
        "robustness_status": selected['robustness_status'],
        "field": np.asarray(fields[matches[0]], dtype=float),
        "reference_didt_a_per_us": reference,
        "source_field": str(center_dir / "magnE.dtseries.nii"),
        "source_map_index_zero_based": int(matches[0]),
    }


def target_calibration(target, csv_override=None, column_override=None):
    configured = target["config"].get("coil_calibration") or {}
    path = csv_override or configured.get("csv")
    if not path:
        raise ValueError(
            f"No dI/dt calibration configured for {target['config'].get('display_name', target['config']['network'])}")
    column = column_override or configured.get("value_column")
    mso, didt, column = read_calibration(path, column)
    return mso, didt, {
        "id": configured.get("id", "command-line-calibration"),
        "csv": str(Path(path).resolve()),
        "column": column,
        "coil_hardware_name": target["config"].get(
            "coil_hardware_name", Path(target["config"]["coil"]).stem.replace("_", " ")),
        "simnibs_coil_model": target["config"]["coil"],
        "mso_range": [float(mso[0]), float(mso[-1])],
        "didt_range_A_per_us": [float(didt[0]), float(didt[-1])],
        "selection_mode": configured.get("selection_mode", "command_line_override"),
        "is_exact_simnibs_coil_match": configured.get(
            "is_exact_simnibs_coil_match"),
        "substitution_justification": configured.get(
            "substitution_justification"),
    }


def retained_target(target):
    cfg = target["config"]
    scoring = cfg.get("scoring", {})
    binary = cifti_vector(cfg["binary_target_map"])
    weighted = cifti_vector(cfg["weighted_target_map"])
    mask = np.isfinite(binary) & (binary > 0)
    if scoring.get("restrict_target_to_design_components", False):
        labels = cifti_vector(scoring["component_labels"])
        mask &= np.isin(labels, [int(value) for value in scoring["design_component_ids"]])
    return mask, np.clip(np.nan_to_num(weighted, nan=0.0), 0.0, None)


def evaluate_doses(field, reference_didt, threshold, target_mask, target_weights,
                   vertex_area, treatment_mso, treatment_didt,
                   minimum_hotspot_area, maximum_treatment_mso=None,
                   rmt_mso=None):
    target_area = float(vertex_area[target_mask].sum())
    target_mass = float((vertex_area[target_mask] * target_weights[target_mask]).sum())
    rows = []
    for mso, didt in zip(treatment_mso, treatment_didt):
        scaled = field * (didt / reference_didt)
        hotspot = np.isfinite(scaled) & (scaled >= threshold)
        hotspot_area = float(vertex_area[hotspot].sum())
        overlap = hotspot & target_mask
        overlap_area = float(vertex_area[overlap].sum())
        overlap_mass = float((vertex_area[overlap] * target_weights[overlap]).sum())
        within_rmt_limit = (
            maximum_treatment_mso is None or
            float(mso) <= float(maximum_treatment_mso) + 1e-9)
        geometric_feasibility = bool(
            hotspot_area >= minimum_hotspot_area and overlap_area > 0)
        rows.append({
            "treatment_mso": float(mso),
            "treatment_didt_a_per_us": float(didt),
            "hotspot_area_mm2": hotspot_area,
            "target_area_in_hotspot_mm2": overlap_area,
            "binary_on_target": overlap_area / hotspot_area if hotspot_area else 0.0,
            "binary_target_coverage": overlap_area / target_area if target_area else 0.0,
            "weighted_on_target": overlap_mass / hotspot_area if hotspot_area else 0.0,
            "weighted_target_coverage": overlap_mass / target_mass if target_mass else 0.0,
            "treatment_to_rmt_ratio": (
                float(mso) / float(rmt_mso) if rmt_mso else None),
            "maximum_treatment_mso": (
                float(maximum_treatment_mso)
                if maximum_treatment_mso is not None else None),
            "within_rmt_safety_limit": bool(within_rmt_limit),
            "geometrically_feasible": geometric_feasibility,
            "feasible": bool(geometric_feasibility and within_rmt_limit),
        })
    return rows


def choose_dose(rows, metric):
    feasible = [row for row in rows if row["feasible"]]
    if not feasible:
        return None
    maximum = max(row[metric] for row in feasible)
    tied = [row for row in feasible if np.isclose(row[metric], maximum, atol=1e-12)]
    return min(tied, key=lambda row: row["treatment_mso"])


def rounded_row(row):
    if row is None:
        return None
    output = {}
    for key, value in row.items():
        output[key] = round(value, 8) if isinstance(value, float) else value
    return output


def build_lookup(args):
    calibration_target = selected_field(
        args.calibration_target_root, getattr(args, 'calibration_pose_variant', 'selected'))
    treatment_targets = [selected_field(
        path, getattr(args, 'treatment_pose_variant', 'selected'))
        for path in args.treatment_target_root]
    calibration_mso, calibration_didt, calibration_identity = target_calibration(
        calibration_target, args.calibration_csv, args.calibration_column)
    vertex_area = cifti_vector(calibration_target["config"]["vertex_area"])
    calibration_mask, calibration_weights = retained_target(calibration_target)
    if not np.any(calibration_mask):
        raise ValueError("Calibration target has no retained vertices")
    base_threshold = weighted_quantile(
        calibration_target["field"][calibration_mask],
        calibration_weights[calibration_mask] ** args.weight_gamma,
        args.threshold_quantile)
    rmt_min = max(1.0, calibration_mso[0]) if args.rmt_min is None else args.rmt_min
    rmt_max = calibration_mso[-1] if args.rmt_max is None else args.rmt_max
    if rmt_min <= 0:
        raise ValueError('Measured rMT must be positive')
    rmt_mso = bounded_grid(rmt_min, rmt_max, args.rmt_step)
    rmt_didt = interpolate_checked(rmt_mso, calibration_mso, calibration_didt)
    curve_stride = max(1, int(round(1.0 / args.treatment_didt_step)))
    colors = priors_colors(args.priors)
    calibration_target_color = target_color(
        colors, calibration_target["config"]["network"])
    target_context = {}
    for target in treatment_targets:
        mask, weights = retained_target(target)
        label = target["config"].get("display_name", target["config"]["network"])
        treatment_csv = args.treatment_calibration_csv
        treatment_column = args.treatment_calibration_column
        if (not treatment_csv and args.calibration_csv and
                Path(target["config"]["coil"]).name ==
                Path(calibration_target["config"]["coil"]).name):
            treatment_csv = args.calibration_csv
            treatment_column = args.calibration_column
        treatment_calibration_mso, treatment_calibration_didt, identity = target_calibration(
            target, treatment_csv, treatment_column)
        treatment_min = (treatment_calibration_mso[0]
                         if args.treatment_min is None else args.treatment_min)
        treatment_max = (treatment_calibration_mso[-1]
                         if args.treatment_max is None else args.treatment_max)
        treatment_didt_limits = interpolate_checked(
            [treatment_min, treatment_max], treatment_calibration_mso,
            treatment_calibration_didt)
        treatment_didt = bounded_grid(
            treatment_didt_limits[0], treatment_didt_limits[1],
            args.treatment_didt_step)
        treatment_mso = interpolate_checked(
            treatment_didt, treatment_calibration_didt,
            treatment_calibration_mso)
        # Score exactly the one-decimal MSO values displayed to the operator.
        treatment_mso = np.unique(np.round(treatment_mso, 1))
        treatment_mso = treatment_mso[(treatment_mso >= treatment_min) &
                                      (treatment_mso <= treatment_max)]
        treatment_didt = interpolate_checked(
            treatment_mso, treatment_calibration_mso, treatment_calibration_didt)
        color = target_color(colors, target["config"]["network"])
        target_context[label] = (
            target, mask, weights, treatment_mso, treatment_didt, identity,
            color)

    entries = []
    full_curves = {}
    for mso, didt in zip(rmt_mso, rmt_didt):
        threshold = base_threshold * didt / calibration_target["reference_didt_a_per_us"]
        treatments = {}
        maximum_treatment_mso = (
            float(mso) * args.maximum_treatment_rmt_ratio
            if args.maximum_treatment_rmt_ratio is not None else None)
        for label, (target, mask, weights, treatment_mso,
                    treatment_didt, _, color) in target_context.items():
            curves = evaluate_doses(
                target["field"], target["reference_didt_a_per_us"], threshold,
                mask, weights, vertex_area, treatment_mso, treatment_didt,
                args.minimum_hotspot_area_mm2,
                maximum_treatment_mso=maximum_treatment_mso,
                rmt_mso=mso)
            selected = choose_dose(curves, args.selection_metric)
            peak_ratio = float(target["best"]["target_to_global_peak_ratio"])
            result = rounded_row(selected)
            if result is not None:
                result.update({
                    "target": label,
                    "target_color": color,
                    "peak_ratio": round(peak_ratio, 8),
                    "passes_peak_gate": peak_ratio >= args.minimum_peak_ratio,
                    "pose_center_id": int(target["best"]["center_id"]),
                    "pose_selection_mode": target['selection_mode'],
                    "pose_orientation_degrees": float(target["best"]["orientation_degrees"]),
                })
            treatments[label] = result
            displayed_curve = curves[::curve_stride]
            if displayed_curve[-1] is not curves[-1]:
                displayed_curve.append(curves[-1])
            full_curves.setdefault(label, {})[f"{mso:g}"] = [
                rounded_row(row) for row in displayed_curve]
        entries.append({
            "rmt_mso": float(mso),
            "rmt_didt_a_per_us": round(float(didt), 8),
            "absolute_threshold_v_per_m": round(float(threshold), 8),
            "maximum_treatment_mso": (
                round(maximum_treatment_mso, 8)
                if maximum_treatment_mso is not None else None),
            "treatments": treatments,
        })

    sources = {}
    for label, (target, _, _, treatment_mso, _, identity, color) in target_context.items():
        sources[label] = {
            "target_root": str(target["root"]),
            "selected_pose": target["best"],
            "selection_mode": target['selection_mode'],
            "robustness_status": target['robustness_status'],
            "reference_field": target["source_field"],
            "reference_map_index_zero_based": target["source_map_index_zero_based"],
            "reference_didt_a_per_us": target["reference_didt_a_per_us"],
            "calibration": identity,
            "network": target["config"]["network"],
            "target_color": color,
            "evaluated_treatment_grid_mso": treatment_mso.tolist(),
        }
    lookup = {
        "schema_version": "tans-calibration-dose-lookup-v2",
        "network_input_provenance": {
            target["config"]["network"]: target["config"]["network_input_provenance"]
            for target in [calibration_target, *treatment_targets]
            if target["config"].get("network_input_provenance", {}).get("mode") in ("wta_only", "weighted_only")},
        "status": "PROVISIONAL_FIXED_POSE_TRANSLATION",
        "subject": calibration_target["config"]["subject"],
        "calibration_target": "SM-Hand / retained left precentral component",
        "treatment_targets": list(target_context),
        "operator_input": "measured SM-Hand resting motor threshold",
        "calibration": {
            **calibration_identity,
            "measured_mso_range": [float(calibration_mso[0]), float(calibration_mso[-1])],
            "planned_calibration_pose_assumed": True,
            "selected_pose": calibration_target["best"],
            "selection_mode": calibration_target['selection_mode'],
            "robustness_status": calibration_target['robustness_status'],
            "reference_field": calibration_target["source_field"],
            "reference_map_index_zero_based": calibration_target["source_map_index_zero_based"],
            "reference_didt_a_per_us": calibration_target["reference_didt_a_per_us"],
            "network": calibration_target["config"]["network"],
            "target_color": calibration_target_color,
        },
        "policy": {
            "absolute_threshold_method": "weighted_quantile_of_calibration_target_efield",
            "threshold_quantile": args.threshold_quantile,
            "target_probability_weight_gamma": args.weight_gamma,
            "minimum_hotspot_area_mm2": args.minimum_hotspot_area_mm2,
            "minimum_target_to_global_peak_ratio": args.minimum_peak_ratio,
            "treatment_didt_step_a_per_us": args.treatment_didt_step,
            "displayed_treatment_mso_decimals": 1,
            "rmt_step_mso": args.rmt_step,
            "maximum_treatment_rmt_ratio": args.maximum_treatment_rmt_ratio,
            "maximum_treatment_rule": (None
                if args.maximum_treatment_rmt_ratio is None else
                "treatment %MSO must be <= maximum_treatment_rmt_ratio "
                "times measured SM-Hand rMT %MSO"),
            "selection_metric": args.selection_metric,
            "selection_rule": (
                "maximize metric among geometrically feasible doses at or below "
                "the hard rMT-relative ceiling; tie-break to lower %MSO"),
            "hotspot_rule": "complete cortex with E-field >= absolute threshold",
            "pose_policy": "fixed selected pose from preceding TANS search; per-target selection mode recorded in sources",
        },
        "warnings": [
            "TANS dose estimate; not a clinical dosing recommendation.",
            "The planned SM-Hand pose is assumed until the actual rMT trajectory is supplied.",
            "Coil pose has not yet been re-optimized separately for each absolute threshold.",
            "Threshold quantile, probability exponent, and feasibility criteria require validation.",
            *(([("Treatment intensity is hard-limited to "
                  f"{100 * args.maximum_treatment_rmt_ratio:g}% of measured "
                  "SM-Hand rMT %MSO; decreases are unrestricted.")]
                if args.maximum_treatment_rmt_ratio is not None else
                ["The optional rMT-relative treatment ceiling is disabled."])),
            *(
                ["A similar-coil calibration substitution was explicitly authorized; review its justification."]
                if (not calibration_identity.get("is_exact_simnibs_coil_match", True)
                    or any(not source["calibration"].get(
                        "is_exact_simnibs_coil_match", True)
                           for source in sources.values())) else []),
        ],
        "entries": entries,
        "dose_response_curves": full_curves,
        "treatment_sources": sources,
    }
    return lookup, calibration_mso, calibration_didt


def write_tsv(path, lookup):
    fields = [
        "rmt_mso", "rmt_didt_a_per_us", "absolute_threshold_v_per_m", "target",
        "treatment_mso", "treatment_didt_a_per_us", "binary_on_target",
        "binary_target_coverage", "weighted_on_target", "weighted_target_coverage",
        "hotspot_area_mm2", "target_area_in_hotspot_mm2", "peak_ratio",
        "passes_peak_gate", "pose_center_id", "pose_orientation_degrees",
        "maximum_treatment_mso", "treatment_to_rmt_ratio",
        "within_rmt_safety_limit", "geometrically_feasible", "target_color"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        for entry in lookup["entries"]:
            for target, treatment in entry["treatments"].items():
                base = {key: entry[key] for key in (
                    "rmt_mso", "rmt_didt_a_per_us", "absolute_threshold_v_per_m",
                    "maximum_treatment_mso")}
                base["target"] = target
                if treatment:
                    base.update(treatment)
                writer.writerow({key: base.get(key, "") for key in fields})


def static_overview(path, lookup, calibration_mso, calibration_didt, example_rmt):
    entries = lookup["entries"]
    index = int(np.argmin([abs(row["rmt_mso"] - example_rmt) for row in entries]))
    entry = entries[index]
    example_rmt = entry["rmt_mso"]
    calibration_color = lookup["calibration"]["target_color"]
    plt.rcParams.update({"font.family": "Arial", "font.size": 10})
    fig = plt.figure(figsize=(16, 9), facecolor=PAPER)
    grid = fig.add_gridspec(2, 3, height_ratios=[1.0, 1.0], hspace=0.36, wspace=0.30)
    fig.suptitle(
        f"{lookup['subject']} | SM-Hand rMT calibration to treatment-network dose",
        fontsize=22, fontweight="bold", color=INK, y=0.965)
    reference_mode = ('fine' if lookup['calibration'].get('selection_mode') == 'nominal_fine' else 'robust')
    treatment_modes = ', '.join(
        f"{name}: {'fine' if source.get('selection_mode') == 'nominal_fine' else 'robust'}"
        for name, source in lookup['treatment_sources'].items())
    fig.text(0.5, 0.917, f'SM-Hand rMT reference: {reference_mode} | Treatment: {treatment_modes}',
             ha='center', fontsize=11, color=INK)

    ax = fig.add_subplot(grid[0, 0], facecolor=PAPER)
    ax.plot(calibration_mso, calibration_didt, color=INK, linewidth=2.4)
    ax.scatter(calibration_mso, calibration_didt, color=calibration_color,
               edgecolor=INK, linewidth=0.35, s=24, zorder=3)
    ax.scatter([example_rmt], [entry["rmt_didt_a_per_us"]],
               color=calibration_color, s=110,
               edgecolor=marker_edge(calibration_color), linewidth=1.2, zorder=4)
    ax.set(title=f"A  {lookup['calibration']['coil_hardware_name']} calibration",
           xlabel="Stimulator output (%MSO)",
           ylabel="dI/dt (A/us)")
    ax.grid(alpha=0.22)

    ax = fig.add_subplot(grid[0, 1], facecolor=PAPER)
    x = [row["rmt_mso"] for row in entries]
    y = [row["absolute_threshold_v_per_m"] for row in entries]
    ax.plot(x, y, color=calibration_color, linewidth=2.6)
    ax.scatter([example_rmt], [entry["absolute_threshold_v_per_m"]],
               color=calibration_color, s=110,
               edgecolor=marker_edge(calibration_color), linewidth=1.2, zorder=4)
    ax.set(title="B  Inferred absolute cortical threshold",
           xlabel="Measured SM-Hand rMT (%MSO)", ylabel="Threshold (V/m)")
    ax.grid(alpha=0.22)

    ax = fig.add_subplot(grid[0, 2], facecolor=PAPER)
    ax.axis("off")
    ax.text(0.03, 0.95, "Operator input", fontsize=12, fontweight="bold", va="top")
    ax.text(0.03, 0.77, f"SM-Hand rMT   {example_rmt:g}% MSO",
            fontsize=20, fontweight="bold", color=calibration_color)
    ax.text(0.03, 0.63, f"{entry['rmt_didt_a_per_us']:.1f} A/us",
            fontsize=16, color=INK)
    ax.text(0.03, 0.46, "Planned SM-Hand pose assumed", fontsize=11,
            color="#a52b20", fontweight="bold")
    ax.text(0.03, 0.32, "Research-use fixed-pose translation", fontsize=10, color=INK)

    for column, target in enumerate(lookup["treatment_targets"][:2]):
        ax = fig.add_subplot(grid[1, column], facecolor=PAPER)
        color = lookup["treatment_sources"][target]["target_color"]
        curve = lookup["dose_response_curves"][target][f"{example_rmt:g}"]
        mso = [row["treatment_mso"] for row in curve]
        score = [100 * row[lookup["policy"]["selection_metric"]] for row in curve]
        area = [row["hotspot_area_mm2"] for row in curve]
        ax.plot(mso, score, color=color, linewidth=2.3, label="on-target")
        if entry["maximum_treatment_mso"] is not None:
            ax.axvline(entry["maximum_treatment_mso"], color="#a52b20",
                       linewidth=1.2, linestyle="--", label="rMT ceiling")
        selected = entry["treatments"][target]
        if selected:
            ax.scatter([selected["treatment_mso"]],
                       [100 * selected[lookup["policy"]["selection_metric"]]],
                       color=color, edgecolor=marker_edge(color),
                       linewidth=1.2, s=105, zorder=4,
                       label="selected")
        ax.set(title=f"{chr(67 + column)}  {target} fixed-pose dose response",
               xlabel="Treatment intensity (%MSO)", ylabel="Binary on-target (%)")
        ax.grid(alpha=0.22)
        other = ax.twinx()
        other.plot(mso, area, color=TEAL, linewidth=1.2, alpha=0.60)
        other.set_ylabel("Hotspot area (mm2)", color=TEAL)
        ax.legend(frameon=False, loc="upper right")

    ax = fig.add_subplot(grid[1, 2], facecolor=PAPER)
    ax.axis("off")
    ax.text(0.02, 0.95, "Treatment outputs", fontsize=13, fontweight="bold", va="top")
    y = 0.77
    for target in lookup["treatment_targets"]:
        selected = entry["treatments"][target]
        color = lookup["treatment_sources"][target]["target_color"]
        if selected:
            text = (f"{target}:  {selected['treatment_mso']:.1f}% MSO\n"
                    f"{selected['treatment_didt_a_per_us']:.1f} A/us | "
                    f"on-target {100 * selected['binary_on_target']:.1f}%")
        else:
            text = f"{target}: no feasible dose"
        ax.text(0.02, y, text, fontsize=15, fontweight="bold", color=color,
                va="top")
        y -= 0.25
    ax.text(0.02, 0.13, "Minimum hotspot area: "
            f"{lookup['policy']['minimum_hotspot_area_mm2']:g} mm2",
            fontsize=10, color=INK)
    ratio = lookup["policy"]["maximum_treatment_rmt_ratio"]
    limit_text = (f"Hard upward ceiling: {100 * ratio:g}% of rMT MSO"
                  if ratio is not None else "rMT-relative ceiling disabled")
    ax.text(0.02, 0.05, limit_text, fontsize=10, color="#a52b20",
            fontweight="bold")
    fig.text(0.5, 0.025,
             "PROVISIONAL | fixed selected treatment poses | actual rMT trajectory not yet supplied",
             ha="center", color="#a52b20", fontsize=10, fontweight="bold")
    fig.savefig(path, dpi=150, facecolor=PAPER, bbox_inches="tight")
    plt.close(fig)


def html_report(path, lookup):
    from dose_preview_ui import PREVIEW_CSS, PREVIEW_MARKUP, PREVIEW_SCRIPT
    from dose_session_ui import SESSION_CSS, SESSION_MARKUP, SESSION_SCRIPT
    from dose_report_defaults import HTML_MARKER

    compact = json.dumps(lookup, separators=(",", ":"))
    targets = "".join(
        f'<option value="{html.escape(target)}">{html.escape(target)}</option>'
        for target in lookup["treatment_targets"])
    calibration_name = html.escape(lookup["calibration"]["coil_hardware_name"])
    treatment_names = ", ".join(sorted({
        source["calibration"]["coil_hardware_name"]
        for source in lookup["treatment_sources"].values()}))
    rmt_min, rmt_max = lookup["calibration"]["mso_range"]
    didt_min, didt_max = lookup["calibration"]["didt_range_A_per_us"]
    initial_rmt = min(max(50, rmt_min), rmt_max)
    reference = lookup['calibration']
    reference_variant = ('fine' if reference.get('selection_mode') == 'nominal_fine' else 'robust')
    reference_pose = reference['selected_pose']
    treatment_variants = ', '.join(
        f"{label}: {'fine' if source.get('selection_mode') == 'nominal_fine' else 'robust'}"
        for label, source in lookup['treatment_sources'].items())
    pose_identity = html.escape(
        f"SM-Hand rMT reference: {reference_variant.upper()}, fine center "
        f"{reference_pose.get('fine_center_id', reference_pose['center_id'])}, "
        f"simulated {reference_pose['orientation_degrees']:g} degrees. "
        f"Treatment poses: {treatment_variants}.")
    operator_view = bool(lookup.get('operator_prescription'))
    if operator_view:
        pose_identity = (
            'Matched to the prescribed SM-Hand and treatment trajectories in this export package. '
            'Measure rMT at that SM-Hand pose using the specified motor coil.')
    operator_style = '#pose {display:none}' if operator_view else ''
    input_notes = ''.join('<div class="warning">Input-map provenance: ' +
                          html.escape(summary) + '</div>' for summary in dict.fromkeys(
                              item['summary'] for item in lookup.get('network_input_provenance', {}).values()))
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
{HTML_MARKER}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(lookup['subject'])} TANS dose lookup</title>
<style>
:root{{--paper:#f4f6f8;--ink:#19222c;--line:#cbd2d8;--purple:#6f2c91;--gold:#c49100;--teal:#008c95;}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:Arial,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 30px 44px}} h1{{font-size:30px;margin:0 0 5px}} .sub{{font-size:15px;color:#596673;margin-bottom:24px}}
.warning{{border-left:5px solid #a52b20;background:#fff;padding:12px 15px;margin:0 0 22px;font-weight:700}}
.layout{{display:grid;grid-template-columns:minmax(320px,.85fr) minmax(420px,1.15fr);gap:24px}}
section{{border-top:2px solid var(--ink);padding-top:14px}} h2{{font-size:16px;margin:0 0 16px}}
label{{display:block;font-size:12px;font-weight:700;margin:14px 0 6px}} input,select{{width:100%;height:42px;border:1px solid #8d98a2;background:#fff;padding:7px 10px;font:16px Arial}}
input[type=range]{{padding:0;border:0;background:transparent}} .linked{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.result{{background:#fff;border:1px solid var(--line);padding:20px;margin-bottom:14px}} .eyebrow{{font-size:11px;font-weight:700;color:#596673;text-transform:uppercase}}
.dose{{font-size:36px;font-weight:800;margin:8px 0 2px}} .didt{{font-size:20px;font-weight:700;color:var(--gold)}}
.metrics{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px 18px;margin-top:18px;font-size:13px}} .metrics b{{display:block;font-size:18px}}
.status{{margin-top:14px;font-size:12px;font-weight:700;color:#167744}} .note{{font-size:12px;color:#596673;line-height:1.45;margin-top:18px}}
.limit{{margin-top:14px;border-left:5px solid #a52b20;background:#fff4f2;padding:10px 12px;color:#8f2118;font-weight:700;font-size:13px}} .limit.ok{{border-color:#167744;background:#f1faf4;color:#116337}}
canvas{{width:100%;height:190px;background:#fff;border:1px solid var(--line);margin-top:14px}}
@media(max-width:800px){{.layout{{grid-template-columns:1fr}} main{{padding:20px}}}}
{PREVIEW_CSS}
{SESSION_CSS}
{operator_style}
</style></head><body><main>
<h1>{html.escape(lookup['subject'])} | Calibration-to-treatment dose</h1>
<div class="sub">Calibration: {calibration_name} &nbsp;|&nbsp; Treatment: {html.escape(treatment_names)}</div>
<div class="warning" id="poseIdentity">{pose_identity}<br>Use only the reference pose actually used for the rMT measurement. Registration accuracy must be assessed independently.</div>
<div class="warning">TANS dose estimate. Planned SM-Hand rMT pose is assumed; verify against the actual recorded rMT trajectory before interpretation.</div>
{input_notes}<div class="layout"><section><h2>Resting motor threshold — preview until locked</h2>
<label for="mso">SM-Hand rMT (%MSO)</label><input id="slider" type="range" min="{rmt_min:g}" max="{rmt_max:g}" step="1" value="{initial_rmt:g}"><input id="mso" type="number" min="{rmt_min:g}" max="{rmt_max:g}" step="1" value="{initial_rmt:g}">
<label for="didt">SM-Hand rMT dI/dt (A/us)</label><input id="didt" type="number" min="{didt_min:g}" max="{didt_max:g}" step="0.1" value="{float(np.interp(initial_rmt, [row['rmt_mso'] for row in lookup['entries']], [row['rmt_didt_a_per_us'] for row in lookup['entries']])):.1f}">
<label for="target">Treatment target</label><select id="target">{targets}</select>
<label for="planned">Check planned treatment intensity (%MSO)</label><input id="planned" type="number" min="0" max="100" step="0.1">
<div class="limit" id="limit"></div>
<div class="note">Motor and treatment coil calibrations are distinct. The configured upward limit, when enabled, is applied to treatment MSO relative to the measured motor-coil rMT MSO.</div>
</section><section><h2>Modeled treatment selection</h2><div class="result"><div class="result-grid"><div>
<div class="eyebrow" id="targetName"></div><div class="dose" id="dose"></div><div class="didt" id="doseDidt"></div>
<div class="metrics"><div>Motor-derived threshold<b id="threshold"></b></div></div>
<div class="status" id="status"></div><div class="note" id="measurementNote"></div></div>
{PREVIEW_MARKUP}</div></div><canvas id="curve" width="960" height="280" aria-label="Modeled binary on-target by treatment intensity"></canvas>
<dl id="landmarks" class="landmarks"></dl><div class="note">Onset denotes the first modeled threshold crossing, not an established minimum effective dose. The area criterion and selection are modeling rules, not a clinically validated treatment range.</div>
<div class="note" id="pose"></div></section></div>
{SESSION_MARKUP}</main>
<script>const LOOKUP={compact};
const $=id=>document.getElementById(id), entries=LOOKUP.entries;
function nearestBy(key,value){{return entries.reduce((a,b)=>Math.abs(b[key]-value)<Math.abs(a[key]-value)?b:a)}}
function targetColor(target){{return LOOKUP.treatment_sources[target].target_color}}
function checkLimit(entry){{const raw=$('planned').value,planned=+raw,maximum=entry.maximum_treatment_mso,node=$('limit'),ratio=LOOKUP.policy.maximum_treatment_rmt_ratio,range=LOOKUP.treatment_sources[$('target').value].calibration.mso_range;node.classList.remove('ok');if(!raw||!Number.isFinite(planned)||planned<range[0]||planned>range[1]){{node.textContent='Enter an intensity inside the treatment-coil calibrated range: '+range[0]+'-'+range[1]+'% MSO.';return}}if(maximum===null){{node.classList.add('ok');node.textContent='rMT-relative ceiling disabled in configuration. Within calibrated range; this does not establish clinical safety.';return}}const ok=planned<=maximum+1e-9;node.classList.toggle('ok',ok);const shownMaximum=(Math.floor((maximum+1e-9)*10)/10).toFixed(1);node.textContent=ok?'Within configured upward limit: maximum '+shownMaximum+'% MSO ('+(100*ratio).toFixed(0)+'% of rMT).':'DO NOT DELIVER: '+planned.toFixed(1)+'% MSO exceeds the configured '+shownMaximum+'% MSO limit ('+(100*ratio).toFixed(0)+'% of rMT).'}}
function draw(entry,target,selected){{const c=$('curve'),x=c.getContext('2d'),rows=LOOKUP.dose_response_curves[target][String(entry.rmt_mso)],range=LOOKUP.treatment_sources[target].calibration.mso_range,xmin=range[0],xmax=range[1],span=xmax-xmin,color=targetColor(target);x.clearRect(0,0,c.width,c.height);x.fillStyle='#fff';x.fillRect(0,0,c.width,c.height);const L=48,R=16,T=16,B=34,W=c.width-L-R,H=c.height-T-B,hasLimit=entry.maximum_treatment_mso!==null,limitX=hasLimit?Math.min(L+W,Math.max(L,L+W*(entry.maximum_treatment_mso-xmin)/span)):null;if(hasLimit){{x.fillStyle='#fff0ee';x.fillRect(limitX,T,Math.max(0,L+W-limitX),H)}}x.strokeStyle='#cbd2d8';x.beginPath();x.moveTo(L,T);x.lineTo(L,T+H);x.lineTo(L+W,T+H);x.stroke();x.strokeStyle=color;x.lineWidth=3;x.beginPath();rows.forEach((r,i)=>{{const px=L+W*(r.treatment_mso-xmin)/span,py=T+H*(1-r.binary_on_target);i?x.lineTo(px,py):x.moveTo(px,py)}});x.stroke();if(hasLimit){{x.strokeStyle='#a52b20';x.lineWidth=1;x.setLineDash([5,4]);x.beginPath();x.moveTo(limitX,T);x.lineTo(limitX,T+H);x.stroke();x.setLineDash([])}}if(selected){{const px=L+W*(selected.treatment_mso-xmin)/span,py=T+H*(1-selected.binary_on_target);x.fillStyle=color;x.beginPath();x.arc(px,py,6,0,Math.PI*2);x.fill();x.strokeStyle='#19222c';x.stroke()}}x.fillStyle='#596673';x.font='12px Arial';x.fillText(xmin+'% MSO',L,T+H+21);x.fillText(xmax+'% MSO',L+W-62,T+H+21);x.save();x.translate(15,T+H/2+30);x.rotate(-Math.PI/2);x.fillText('binary on-target',0,0);x.restore()}}
function render(entry){{const target=$('target').value,r=entry.treatments[target],color=targetColor(target),maximum=entry.maximum_treatment_mso;$('mso').value=entry.rmt_mso;$('slider').value=entry.rmt_mso;$('didt').value=entry.rmt_didt_a_per_us.toFixed(1);$('targetName').textContent=target+' treatment';$('targetName').style.color=color;$('dose').style.color=color;$('threshold').textContent=entry.absolute_threshold_v_per_m.toFixed(1)+' V/m';if(!r){{$('dose').textContent='No feasible dose';$('doseDidt').textContent='';$('precision').textContent=$('coverage').textContent=$('area').textContent='-';$('status').textContent=maximum===null?'No dose satisfies the configured hotspot criteria.':'No dose satisfies hotspot criteria at or below '+maximum.toFixed(1)+'% MSO. Do not exceed the limit.';$('status').style.color='#a52b20';$('pose').textContent='';$('planned').value=maximum===null?'':maximum.toFixed(1);checkLimit(entry);draw(entry,target,null);return}}$('dose').textContent=r.treatment_mso.toFixed(1)+'% MSO';$('doseDidt').textContent=r.treatment_didt_a_per_us.toFixed(1)+' A/us';$('precision').textContent=(100*r.binary_on_target).toFixed(1)+'%';$('coverage').textContent=(100*r.binary_target_coverage).toFixed(1)+'%';$('area').textContent=r.hotspot_area_mm2.toFixed(0)+' mm2';$('status').textContent=r.passes_peak_gate?'Peak-proximity gate passes':'Peak-proximity gate fails';$('status').style.color=r.passes_peak_gate?'#167744':'#a52b20';$('planned').value=r.treatment_mso.toFixed(1);checkLimit(entry);$('pose').textContent='Fixed '+(r.pose_selection_mode==='nominal_fine'?'nominal fine':'robust')+' pose: center '+r.pose_center_id+', orientation '+r.pose_orientation_degrees+' degrees. Selection maximizes '+LOOKUP.policy.selection_metric.replaceAll('_',' ')+' with a '+LOOKUP.policy.minimum_hotspot_area_mm2+' mm2 minimum hotspot.';draw(entry,target,r)}}
let validEntry=null;
const renderEntry=render;
render=entry=>{{validEntry=entry;renderEntry(entry)}};
function readMeasurement(key,raw){{const value=Number(raw),entry=nearestBy(key,value),tolerance=key==='rmt_mso'?1e-8:0.051;if(raw.trim()===''||!Number.isFinite(value)||Math.abs(entry[key]-value)>tolerance){{validEntry=null;$('dose').textContent='Invalid rMT input';$('doseDidt').textContent='';$('planned').value='';$('threshold').textContent=$('precision').textContent=$('coverage').textContent=$('area').textContent='-';$('curve').getContext('2d').clearRect(0,0,720,210);$('pose').textContent='';$('status').textContent='Enter a measured rMT on the precomputed grid. Values are never silently rounded.';$('limit').classList.remove('ok');$('limit').textContent='No prescription available for this input.';return}}render(entry)}}
$('mso').onchange=e=>readMeasurement('rmt_mso',e.target.value);$('slider').oninput=e=>readMeasurement('rmt_mso',e.target.value);$('didt').onchange=e=>readMeasurement('rmt_didt_a_per_us',e.target.value);$('target').onchange=()=>{{if(validEntry)render(validEntry)}};$('planned').oninput=()=>{{if(validEntry)checkLimit(validEntry)}};render(nearestBy('rmt_mso',{initial_rmt:g}));
</script><script>{PREVIEW_SCRIPT}</script><script>{SESSION_SCRIPT}</script></body></html>"""
    path.write_text(document)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-target-root", required=True)
    parser.add_argument("--treatment-target-root", action="append", required=True)
    parser.add_argument('--calibration-pose-variant', choices=('selected', 'fine', 'robust'), default='selected')
    parser.add_argument('--treatment-pose-variant', choices=('selected', 'fine', 'robust'), default='selected')
    parser.add_argument("--calibration-csv")
    parser.add_argument("--calibration-column")
    parser.add_argument("--treatment-calibration-csv")
    parser.add_argument("--treatment-calibration-column")
    parser.add_argument("--priors", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rmt-min", type=float)
    parser.add_argument("--rmt-max", type=float)
    parser.add_argument("--rmt-step", type=float, default=1.0)
    parser.add_argument("--treatment-min", type=float)
    parser.add_argument("--treatment-max", type=float)
    parser.add_argument("--treatment-didt-step", type=float, default=0.25)
    parser.add_argument("--threshold-quantile", type=float, default=0.99)
    parser.add_argument("--weight-gamma", type=float, default=2.0)
    parser.add_argument("--minimum-hotspot-area-mm2", type=float, default=50.0)
    parser.add_argument("--minimum-peak-ratio", type=float, default=0.95)
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument("--maximum-treatment-rmt-ratio", type=float, default=1.20)
    limit.add_argument("--disable-treatment-rmt-limit", action="store_true")
    parser.add_argument("--selection-metric", choices=("binary_on_target", "weighted_on_target"), default="binary_on_target")
    parser.add_argument("--example-rmt", type=float, default=50.0)
    args = parser.parse_args()
    if args.disable_treatment_rmt_limit:
        args.maximum_treatment_rmt_ratio = None
    for key in (
            "rmt_step", "treatment_didt_step", "weight_gamma",
            "minimum_hotspot_area_mm2"):
        if not np.isfinite(getattr(args, key)) or getattr(args, key) <= 0:
            raise ValueError(f"--{key.replace('_', '-')} must be positive")
    if not 0 <= args.threshold_quantile <= 1:
        raise ValueError("--threshold-quantile must be in [0, 1]")
    if (args.maximum_treatment_rmt_ratio is not None and
            (not np.isfinite(args.maximum_treatment_rmt_ratio) or
             args.maximum_treatment_rmt_ratio < 1)):
        raise ValueError("--maximum-treatment-rmt-ratio must be finite and >= 1 (upward-only)")
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite dose report: {output}")
    output.mkdir(parents=True)
    lookup, calibration_mso, calibration_didt = build_lookup(args)
    from dose_report_defaults import ensure_lookup
    lookup = ensure_lookup(lookup, args.priors)
    (output / "DoseLookup.json").write_text(json.dumps(lookup, indent=2) + "\n")
    write_tsv(output / "DoseLookup.tsv", lookup)
    static_overview(output / "DoseCalibrationOverview.png", lookup,
                    calibration_mso, calibration_didt, args.example_rmt)
    html_report(output / "DoseCalibrationReport.html", lookup)
    summary = {
        "output": str(output),
        "html_report": str(output / "DoseCalibrationReport.html"),
        "static_qc": str(output / "DoseCalibrationOverview.png"),
        "lookup_json": str(output / "DoseLookup.json"),
        "lookup_tsv": str(output / "DoseLookup.tsv"),
        "rmt_entries": len(lookup["entries"]),
        "treatment_targets": lookup["treatment_targets"],
        "status": lookup["status"],
    }
    (output / "DoseReportSummary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
