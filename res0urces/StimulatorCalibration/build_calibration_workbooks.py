#!/usr/bin/env python3
"""Regenerate formatted calibration workbooks from canonical registry CSVs."""

import csv
import json
from pathlib import Path

import numpy as np
import xlsxwriter


ROOT = Path(__file__).resolve().parent


def read_rows(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def workbook_for(entry):
    csv_path = ROOT / entry["csv"]
    output = csv_path.with_name("stimulator_didt_calibration.xlsx")
    rows = read_rows(csv_path)
    mso = np.asarray([float(row["amplitude_pct_mso"]) for row in rows])
    didt = np.asarray([float(row[entry["value_column"]]) for row in rows])
    measured = np.asarray([
        row["measurement_status"] == "measured" for row in rows])
    fit_mask = measured if measured.sum() >= 2 else np.ones(len(rows), dtype=bool)
    slope, intercept = np.polyfit(mso[fit_mask], didt[fit_mask], 1)
    fit = slope * mso + intercept
    ss_res = float(np.sum((didt[fit_mask] - fit[fit_mask]) ** 2))
    centered = didt[fit_mask] - didt[fit_mask].mean()
    ss_tot = float(np.sum(centered ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot else 1.0

    workbook = xlsxwriter.Workbook(output)
    workbook.set_properties({
        "title": f"{entry['coil_hardware_name']} dI/dt calibration",
        "subject": "TANS stimulator-output calibration",
        "comments": "Derived from canonical didt_calibration.csv",
    })
    sheet = workbook.add_worksheet("Calibration")
    metadata = workbook.add_worksheet("Metadata")
    title = workbook.add_format({
        "bold": True, "font_size": 18, "font_color": "#19222C"})
    subtitle = workbook.add_format({
        "font_size": 11, "font_color": "#596673"})
    header = workbook.add_format({
        "bold": True, "font_color": "white", "bg_color": "#19222C",
        "border": 1, "text_wrap": True, "valign": "vcenter"})
    measured_format = workbook.add_format({"bg_color": "#E8F4F3"})
    inferred_format = workbook.add_format({"bg_color": "#FFF0D0"})
    interpolated_format = workbook.add_format({"bg_color": "#F5EAF8"})
    extrapolated_format = workbook.add_format({"bg_color": "#FDE7E5"})
    anchor_format = workbook.add_format({"bg_color": "#E7EEF4"})
    number = workbook.add_format({"num_format": "0.0"})
    metric_name = workbook.add_format({"bold": True, "font_color": "#19222C"})
    metric_value = workbook.add_format({"num_format": "0.0000"})

    sheet.merge_range("A1:H1", f"{entry['coil_hardware_name']} | MagPro X100", title)
    sheet.merge_range(
        "A2:H2",
        f"{entry['waveform']} | current direction: {entry['current_direction']} | "
        f"runtime curve: {entry['mso_range'][0]:g}-{entry['mso_range'][1]:g}% MSO",
        subtitle)
    columns = [
        "amplitude_pct_mso", "realized_didt_A_per_us",
        "secondary_phase_didt_A_per_us", "coil_temp_C",
        "measurement_status", "source_image", "note",
        "linear_fit_A_per_us",
    ]
    for column, name in enumerate(columns):
        sheet.write(3, column, name, header)
    for index, row in enumerate(rows, start=4):
        status = row["measurement_status"]
        row_format = (
            measured_format if status == "measured" else
            interpolated_format if status == "interpolated" else
            extrapolated_format if status == "model_extrapolated" else
            anchor_format if status == "physical_zero_anchor" else
            inferred_format)
        values = [
            float(row["amplitude_pct_mso"]),
            float(row["realized_didt_A_per_us"]),
            (float(row["secondary_phase_didt_A_per_us"])
             if row["secondary_phase_didt_A_per_us"] else None),
            float(row["coil_temp_C"]) if row["coil_temp_C"] else None,
            status, row["source_image"], row["note"],
            float(fit[index - 4]),
        ]
        for column, value in enumerate(values):
            sheet.write(index, column, value, row_format)
    sheet.add_table(3, 0, 3 + len(rows), len(columns) - 1, {
        "name": "CalibrationData",
        "style": "Table Style Medium 2",
        "columns": [{"header": name} for name in columns],
    })
    sheet.set_column("A:D", 22)
    sheet.set_column("E:E", 29)
    sheet.set_column("F:F", 31)
    sheet.set_column("G:G", 65)
    sheet.set_column("H:H", 23, number)
    sheet.freeze_panes(4, 0)

    metric_column = 10
    metrics = [
        ("Measured-only slope", slope),
        ("Measured-only intercept", intercept),
        ("Measured-only R-squared", r_squared),
        ("Minimum runtime MSO", float(mso.min())),
        ("Maximum runtime MSO", float(mso.max())),
        ("Minimum source-observation MSO",
         float(entry["source_observation_mso_range"][0])),
        ("Maximum source-observation MSO",
         float(entry["source_observation_mso_range"][1])),
    ]
    for row_index, (name, value) in enumerate(metrics, start=3):
        sheet.write(row_index, metric_column, name, metric_name)
        sheet.write(row_index, metric_column + 1, value, metric_value)

    chart = workbook.add_chart({"type": "scatter", "subtype": "straight_with_markers"})
    chart.add_series({
        "name": "Canonical calibration",
        "categories": ["Calibration", 4, 0, 3 + len(rows), 0],
        "values": ["Calibration", 4, 1, 3 + len(rows), 1],
        "line": {"color": "#008C95", "width": 2.5},
        "marker": {"type": "circle", "size": 3,
                   "border": {"color": "#19222C"},
                   "fill": {"color": "#42C6CB"}},
    })
    chart.add_series({
        "name": "Measured-only linear fit",
        "categories": ["Calibration", 4, 0, 3 + len(rows), 0],
        "values": ["Calibration", 4, 7, 3 + len(rows), 7],
        "line": {"color": "#C49100", "width": 1.5, "dash_type": "dash"},
        "marker": {"type": "none"},
    })
    chart.set_title({"name": "%MSO to realized dI/dt"})
    chart.set_x_axis({"name": "Stimulator output (%MSO)", "major_gridlines": {"visible": False}})
    chart.set_y_axis({"name": "Realized dI/dt (A/us)", "major_gridlines": {"visible": True}})
    chart.set_legend({"position": "bottom"})
    chart.set_size({"width": 720, "height": 430})
    sheet.insert_chart("J10", chart)

    metadata.write(0, 0, "field", header)
    metadata.write(0, 1, "value", header)
    for row_index, (key, value) in enumerate(entry.items(), start=1):
        metadata.write(row_index, 0, key)
        metadata.write(row_index, 1, json.dumps(value) if isinstance(value, (list, dict)) else value)
    metadata.set_column("A:A", 34)
    metadata.set_column("B:B", 82)
    metadata.write(len(entry) + 2, 0, "canonical_csv")
    metadata.write(len(entry) + 2, 1, str(csv_path.name))
    metadata.write(len(entry) + 3, 0, "workbook_role")
    metadata.write(len(entry) + 3, 1, "Derived human-review artifact; CSV is authoritative")
    workbook.close()
    print(output)


def main():
    registry = json.loads((ROOT / "calibration_registry.json").read_text())
    for entry in registry["entries"]:
        workbook_for(entry)


if __name__ == "__main__":
    main()
