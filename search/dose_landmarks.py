"""Add model-only diagnostic landmarks without changing a dose selection."""

import base64
import copy
import importlib.util
import math
from pathlib import Path

import numpy as np


DEFAULT_REPORT_MODULE = Path(__file__).with_name("generate_calibration_dose_report.py")


def load_report_module(path=DEFAULT_REPORT_MODULE):
    spec = importlib.util.spec_from_file_location("landmark_dose_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def analytic_onset(field, threshold, reference_didt, calibration_mso,
                   calibration_didt, mask=None):
    """Return the continuous E >= threshold crossing, not an effective dose."""
    field = np.asarray(field, dtype=float)
    valid = np.isfinite(field) & (field > 0)
    if mask is not None:
        valid &= np.asarray(mask, dtype=bool)
    if not np.any(valid):
        return None
    required_didt = float(threshold) * float(reference_didt) / float(field[valid].max())
    if not calibration_didt[0] <= required_didt <= calibration_didt[-1]:
        return None
    return float(np.interp(required_didt, calibration_didt, calibration_mso))


def row_landmarks(rows, round_row):
    def first(predicate):
        return round_row(next((row for row in rows if predicate(row)), None))

    positive = [row for row in rows if row["hotspot_area_mm2"] > 0]
    peak = None
    if positive:
        maximum = max(row["binary_on_target"] for row in positive)
        ties = [row for row in positive
                if np.isclose(row["binary_on_target"], maximum, atol=1e-12)]
        peak = round_row(min(ties, key=lambda row: row["treatment_mso"]))
    return {
        "first_cortex_row": first(lambda row: row["hotspot_area_mm2"] > 0),
        "first_target_row": first(lambda row: row["target_area_in_hotspot_mm2"] > 0),
        "first_geometrically_feasible": first(lambda row: row["geometrically_feasible"]),
        "first_feasible": first(lambda row: row["feasible"]),
        "unconstrained_selectivity_peak": peak,
    }


def merge_curve_rows(existing, additions):
    """Retain displayed rows and insert exact full-grid diagnostic landmarks."""
    by_mso = {row["treatment_mso"]: copy.deepcopy(row) for row in existing}
    for row in additions:
        if row is not None and row["treatment_mso"] not in by_mso:
            by_mso[row["treatment_mso"]] = copy.deepcopy(row)
    return [by_mso[mso] for mso in sorted(by_mso)]


def runtime_metrics(field, mask, weights, vertex_area):
    """Encode sorted fields and prefix sums for threshold-exact browser scoring."""
    finite = np.flatnonzero(np.isfinite(field))
    order = finite[np.argsort(field[finite], kind="stable")]

    def encoded(values):
        return base64.b64encode(np.asarray(values, dtype="<f8").tobytes()).decode("ascii")

    def prefix(values):
        return encoded(np.concatenate(([0.0], np.cumsum(values, dtype=float))))

    return {
        "field_f64le_base64": encoded(field[order]),
        "area_prefix_f64le_base64": prefix(vertex_area[order]),
        "target_area_prefix_f64le_base64": prefix(vertex_area[order] * mask[order]),
        "target_mass_prefix_f64le_base64": prefix(
            vertex_area[order] * mask[order] * weights[order]),
        "total_target_area_mm2": float(vertex_area[mask].sum()),
        "total_target_mass": float((vertex_area[mask] * weights[mask]).sum()),
        "maximum_target_reference_field": float(field[finite][mask[finite]].max(initial=0.0)),
    }


def validate_row(recorded, computed, label):
    if (recorded is None) != (computed is None):
        raise ValueError(f"{label}: recomputed feasibility differs from the recorded lookup")
    if recorded is None:
        return
    for key, actual in computed.items():
        if key not in recorded:
            raise ValueError(f"{label}: recorded row lacks {key}")
        expected = recorded[key]
        if isinstance(actual, float):
            matches = (isinstance(expected, (int, float)) and
                       math.isclose(actual, expected, rel_tol=0.0, abs_tol=1.1e-8))
        else:
            matches = actual == expected
        if not matches:
            raise ValueError(f"{label}: changed {key}: recorded={expected!r}, computed={actual!r}")


def source_context(source, report):
    reference = Path(source["reference_field"])
    if "target_root" in source:
        config_path = Path(source["target_root"]) / "CoarseRunConfig.json"
    else:
        config_path = next((parent / "CoarseRunConfig.json"
                            for parent in reference.parents
                            if (parent / "CoarseRunConfig.json").is_file()), None)
        if config_path is None:
            raise FileNotFoundError(f"Cannot find recorded source config above {reference}")
    config = report.read_json(config_path)
    data = np.asanyarray(report.nib.load(str(reference)).dataobj)
    index = source["reference_map_index_zero_based"]
    if data.ndim != 2 or not isinstance(index, int) or not 0 <= index < data.shape[0]:
        raise ValueError(f"Invalid recorded field map index: {reference}: {index}")
    field = np.asarray(data[index], dtype=float)
    mask, weights = report.retained_target({"config": config})
    vertex_area = report.cifti_vector(config["vertex_area"])
    if not (field.shape == mask.shape == weights.shape == vertex_area.shape):
        raise ValueError(f"Recorded source arrays have inconsistent shapes: {reference}")
    if not np.all(np.isfinite(vertex_area)) or np.any(vertex_area < 0):
        raise ValueError(f"Invalid vertex areas: {reference}")
    reference_didt = float(source["reference_didt_a_per_us"])
    if not math.isfinite(reference_didt) or reference_didt <= 0:
        raise ValueError(f"Invalid reference dI/dt: {reference}")
    return field, mask, weights, vertex_area, reference_didt


def enrich_lookup(lookup, report_module=None):
    """Return a new lookup; fail on changed source data or selection results."""
    report = report_module or load_report_module()
    output = copy.deepcopy(lookup)
    policy = lookup["policy"]
    motor = lookup["calibration"]
    motor_field, motor_mask, motor_weights, vertex_area, motor_ref = source_context(motor, report)
    base_threshold = report.weighted_quantile(
        motor_field[motor_mask], motor_weights[motor_mask] **
        policy["target_probability_weight_gamma"], policy["threshold_quantile"])
    motor_mso, motor_didt, _ = report.read_calibration(motor["csv"], motor["column"])
    output["calibration"]["samples_mso"] = motor_mso.tolist()
    output["calibration"]["samples_didt_a_per_us"] = motor_didt.tolist()
    output["calibration"]["threshold_v_per_m_per_calibration_didt"] = base_threshold / motor_ref
    for target, source in lookup["treatment_sources"].items():
        field, mask, weights, target_area, reference_didt = source_context(source, report)
        if not np.array_equal(vertex_area, target_area):
            raise ValueError(f"{target}: motor and treatment vertex areas differ")
        calibration = source["calibration"]
        calibration_mso, calibration_didt, _ = report.read_calibration(
            calibration["csv"], calibration["column"])
        output_calibration = output["treatment_sources"][target]["calibration"]
        output_calibration["samples_mso"] = calibration_mso.tolist()
        output_calibration["samples_didt_a_per_us"] = calibration_didt.tolist()
        preview_metrics = runtime_metrics(field, mask, weights, vertex_area)
        output["treatment_sources"][target]["preview_metrics"] = preview_metrics
        for entry, enriched_entry in zip(lookup["entries"], output["entries"]):
            rmt_mso = entry["rmt_mso"]
            key = f"{rmt_mso:g}"
            label = f"{target}, rMT {key}"
            displayed = lookup["dose_response_curves"][target][key]
            if not displayed:
                raise ValueError(f"{label}: missing recorded dose curve")
            lower, upper = displayed[0]["treatment_mso"], displayed[-1]["treatment_mso"]
            didt_limits = report.interpolate_checked(
                [lower, upper], calibration_mso, calibration_didt)
            if "evaluated_treatment_grid_mso" in source:
                grid_mso = np.asarray(source["evaluated_treatment_grid_mso"], dtype=float)
                if (np.any(~np.isfinite(grid_mso)) or np.any(np.diff(grid_mso) <= 0)
                        or grid_mso[0] != lower or grid_mso[-1] != upper):
                    raise ValueError(f"{label}: invalid recorded treatment grid")
            else:
                grid_didt = report.bounded_grid(*didt_limits, policy["treatment_didt_step_a_per_us"])
                grid_mso = np.unique(np.round(report.interpolate_checked(
                    grid_didt, calibration_didt, calibration_mso), 1))
                grid_mso = grid_mso[(grid_mso >= lower) & (grid_mso <= upper)]
            grid_didt = report.interpolate_checked(grid_mso, calibration_mso, calibration_didt)
            if "treatment_grid_mso" in preview_metrics:
                if preview_metrics["treatment_grid_mso"] != grid_mso.tolist():
                    raise ValueError(f"{label}: treatment grid changes between rMT rows")
            else:
                preview_metrics["treatment_grid_mso"] = grid_mso.tolist()
            measured_didt = float(report.interpolate_checked(rmt_mso, motor_mso, motor_didt))
            threshold = base_threshold * measured_didt / motor_ref
            enriched_entry["preview_absolute_threshold_v_per_m"] = threshold
            validate_row(entry, {"rmt_didt_a_per_us": measured_didt,
                                 "absolute_threshold_v_per_m": threshold}, label)
            rows = report.evaluate_doses(
                field, reference_didt, threshold, mask, weights, vertex_area,
                grid_mso, grid_didt, policy["minimum_hotspot_area_mm2"],
                maximum_treatment_mso=entry["maximum_treatment_mso"], rmt_mso=rmt_mso)
            selected = report.choose_dose(rows, policy["selection_metric"])
            validate_row(entry["treatments"][target], selected, label)
            rows_by_mso = {row["treatment_mso"]: row for row in rows}
            for row in displayed:
                if row["treatment_mso"] not in rows_by_mso:
                    raise ValueError(f"{label}: recorded curve cannot be reproduced on its inferred grid")
                validate_row(row, rows_by_mso[row["treatment_mso"]], label)
            landmarks = row_landmarks(rows, report.rounded_row)
            landmarks["first_cortex_mso"] = analytic_onset(
                field, threshold, reference_didt, calibration_mso, calibration_didt)
            landmarks["first_target_mso"] = analytic_onset(
                field, threshold, reference_didt, calibration_mso, calibration_didt, mask)
            enriched_entry.setdefault("treatment_landmarks", {})[target] = landmarks
            additions = [value for value in landmarks.values() if isinstance(value, dict)]
            additions.append(report.rounded_row(selected))
            output["dose_response_curves"][target][key] = merge_curve_rows(displayed, additions)
    return output
