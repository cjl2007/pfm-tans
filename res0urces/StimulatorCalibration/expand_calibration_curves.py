#!/usr/bin/env python3
"""Expand observed calibration points to documented 0-100% MSO curves."""

import csv
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent
FIELDS = (
    "amplitude_pct_mso", "realized_didt_A_per_us",
    "secondary_phase_didt_A_per_us", "coil_temp_C",
    "measurement_status", "source_image", "note",
)


def number(value):
    return "" if value == "" else f"{float(value):.6f}".rstrip("0").rstrip(".")


def expand(entry):
    directory = (ROOT / entry["csv"]).parent
    source = directory / "observed_calibration.csv"
    output = directory / "didt_calibration.csv"
    with source.open(newline="") as handle:
        observed = list(csv.DictReader(handle))
    by_mso = {int(float(row["amplitude_pct_mso"])): row for row in observed}
    source_mso = np.asarray(sorted(by_mso), dtype=float)
    source_didt = np.asarray([
        float(by_mso[int(value)]["realized_didt_A_per_us"])
        for value in source_mso])
    measured = np.asarray([
        by_mso[int(value)]["measurement_status"] == "measured"
        for value in source_mso])
    fit_slope, fit_intercept = np.polyfit(
        source_mso[measured], source_didt[measured], 1)
    upper_mso = float(source_mso[-1])
    upper_didt = float(source_didt[-1])

    secondary_available = any(
        row.get("secondary_phase_didt_A_per_us", "") for row in observed)
    secondary_x = np.asarray([
        float(row["amplitude_pct_mso"]) for row in observed
        if row.get("secondary_phase_didt_A_per_us", "")], dtype=float)
    secondary_y = np.asarray([
        float(row["secondary_phase_didt_A_per_us"]) for row in observed
        if row.get("secondary_phase_didt_A_per_us", "")], dtype=float)

    rows = []
    anchor = {
        "amplitude_pct_mso": "0", "realized_didt_A_per_us": "0",
        "secondary_phase_didt_A_per_us": "0" if secondary_available else "",
        "coil_temp_C": "", "measurement_status": "physical_zero_anchor",
        "source_image": "", "note": "Physical zero-output anchor",
    }
    rows.append(anchor)
    interpolation_x = np.concatenate(([0.0], source_mso))
    interpolation_y = np.concatenate(([0.0], source_didt))
    for mso in range(1, 101):
        if mso in by_mso:
            row = dict(by_mso[mso])
        elif mso < upper_mso:
            row = {
                "amplitude_pct_mso": str(mso),
                "realized_didt_A_per_us": number(np.interp(
                    mso, interpolation_x, interpolation_y)),
                "secondary_phase_didt_A_per_us": (
                    number(np.interp(mso, np.concatenate(([0.0], secondary_x)),
                                     np.concatenate(([0.0], secondary_y))))
                    if secondary_available else ""),
                "coil_temp_C": "", "measurement_status": "interpolated",
                "source_image": "",
                "note": "Piecewise-linear interpolation between observed points",
            }
        else:
            estimate = upper_didt + fit_slope * (mso - upper_mso)
            row = {
                "amplitude_pct_mso": str(mso),
                "realized_didt_A_per_us": number(estimate),
                "secondary_phase_didt_A_per_us": "",
                "coil_temp_C": "",
                "measurement_status": "model_extrapolated",
                "source_image": "",
                "note": (
                    "Measured-only linear slope extrapolation anchored to the "
                    f"{upper_mso:g}% observation"),
            }
        rows.append({field: row.get(field, "") for field in FIELDS})

    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "source_observation_mso_range": [
            float(source_mso.min()), float(source_mso.max())],
        "full_curve_mso_range": [0.0, 100.0],
        "estimated_above_mso": (
            float(upper_mso) if upper_mso < 100 else None),
        "measured_only_linear_slope_A_per_us_per_pct_mso": float(fit_slope),
        "measured_only_linear_intercept_A_per_us": float(fit_intercept),
    }


def main():
    registry_path = ROOT / "calibration_registry.json"
    registry = json.loads(registry_path.read_text())
    for entry in registry["entries"]:
        metadata = expand(entry)
        entry.pop("measured_mso_range", None)
        entry.update(metadata)
        entry["mso_range"] = [0.0, 100.0]
        entry["status"] = "measured_and_estimated_full_curve"
    registry_path.write_text(json.dumps(registry, indent=2) + "\n")


if __name__ == "__main__":
    main()
