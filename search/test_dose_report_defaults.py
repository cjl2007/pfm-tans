import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import dose_report_defaults as defaults


def embedded_lookup():
    def encoded(values, dtype="<f8"):
        return base64.b64encode(np.asarray(values, dtype=dtype).tobytes()).decode()

    image = io.BytesIO()
    Image.new("RGBA", (2, 2), "gray").save(image, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
    calibration = {"samples_mso": [0, 100], "samples_didt_a_per_us": [0, 150],
                   "coil_hardware_name": "test coil"}
    source = {"calibration": calibration, "network": "Salience",
              "selection_mode": "nominal_fine", "reference_didt_a_per_us": 1.0,
              "reference_map_index_zero_based": 2,
              "reference_field": "/intentionally/missing/field",
              "preview_metrics": {
                  "field_f64le_base64": encoded([1, 2]),
                  "area_prefix_f64le_base64": encoded([0, 1, 2]),
                  "target_area_prefix_f64le_base64": encoded([0, 0, 1]),
                  "target_mass_prefix_f64le_base64": encoded([0, 0, .5]),
                  "total_target_area_mm2": 1, "total_target_mass": .5,
                  "maximum_target_reference_field": 2, "treatment_grid_mso": [0, 50, 100]}}
    preview = {"schema_version": "tans-dose-hotspot-preview-v1", "width": 2, "height": 2,
               "base_gray_png": uri, "network_png": uri, "coil_outline_png": uri,
               "field_f32le_base64": encoded([1, 2, 1, 2], "<f4"),
               "network_index_u8_base64": encoded([1, 1, 1, 1], "u1"),
               "reference_didt_a_per_us": 1,
               "provenance": {"subject": "TEST", "network": "Salience",
                              "selection_mode": "nominal_fine", "map_index_zero_based": 2,
                              "coil_hardware_name": "test coil"}}
    landmarks = {key: None for key in (
        "first_cortex_mso", "first_target_mso", "first_cortex_row", "first_target_row",
        "first_geometrically_feasible", "first_feasible", "unconstrained_selectivity_peak")}
    return {"subject": "TEST", "treatment_targets": ["SAL"],
            "policy": {"maximum_treatment_rmt_ratio": 1.2},
            "calibration": {**calibration, "threshold_v_per_m_per_calibration_didt": .8},
            "treatment_sources": {"SAL": source}, "hotspot_previews": {"SAL": preview},
            "entries": [{"treatments": {"SAL": {"treatment_mso": 60.2}},
                         "preview_absolute_threshold_v_per_m": 80,
                         "treatment_landmarks": {"SAL": landmarks}}]}


def fake_html(path, lookup):
    path.write_text(defaults.HTML_MARKER + "<script>const LOOKUP=" + json.dumps(lookup) + ";</script>")


class DefaultDoseReportTests(unittest.TestCase):
    def test_embedded_lookup_needs_no_source_files_or_rerender(self):
        original = embedded_lookup()
        before = copy.deepcopy(original)
        with patch("dose_landmarks.enrich_lookup", side_effect=AssertionError("source read")), \
                patch("dose_hotspot_preview.attach_hotspot_previews", side_effect=AssertionError("render")):
            upgraded = defaults.ensure_lookup(original)
            again = defaults.ensure_lookup(upgraded)
        self.assertEqual(original, before)
        self.assertEqual(upgraded, again)
        self.assertEqual(upgraded["entries"], original["entries"])
        defaults.validate_lookup(upgraded)

    def test_legacy_metrics_and_preview_are_upgraded(self):
        complete = embedded_lookup()
        legacy = copy.deepcopy(complete)
        del legacy["hotspot_previews"]
        del legacy["treatment_sources"]["SAL"]["preview_metrics"]
        metrics_only = copy.deepcopy(complete)
        del metrics_only["hotspot_previews"]
        with patch("dose_landmarks.enrich_lookup", return_value=metrics_only) as enrich, \
                patch("dose_hotspot_preview.attach_hotspot_previews", return_value=complete) as preview:
            result = defaults.ensure_lookup(legacy, "priors.mat")
        enrich.assert_called_once()
        preview.assert_called_once_with(metrics_only, "priors.mat")
        self.assertEqual(result["policy"], legacy["policy"])

    def test_changed_selection_is_rejected(self):
        legacy = embedded_lookup()
        del legacy["hotspot_previews"]
        altered = embedded_lookup()
        altered["entries"][0]["treatments"]["SAL"]["treatment_mso"] = 80
        with patch("dose_hotspot_preview.attach_hotspot_previews", return_value=altered):
            with self.assertRaisesRegex(ValueError, "changed recorded selections"):
                defaults.ensure_lookup(legacy)

    def test_corrupt_embedded_payload_is_not_silently_rebuilt(self):
        lookup = defaults.ensure_lookup(embedded_lookup())
        lookup["hotspot_previews"]["SAL"]["field_f32le_base64"] = ""
        with patch("dose_hotspot_preview.attach_hotspot_previews", side_effect=AssertionError("render")):
            with self.assertRaisesRegex(ValueError, "truncated preview"):
                defaults.ensure_lookup(lookup)

    def test_preview_source_mismatch_is_rejected(self):
        lookup = embedded_lookup()
        lookup["hotspot_previews"]["SAL"]["provenance"]["map_index_zero_based"] = 3
        with self.assertRaisesRegex(ValueError, "map_index_zero_based mismatch"):
            defaults.ensure_lookup(lookup)

    def test_html_requires_version_and_matching_embedded_data(self):
        lookup = defaults.ensure_lookup(embedded_lookup())
        with self.assertRaisesRegex(ValueError, "current approved"):
            defaults.validate_html("old report", lookup)
        with self.assertRaisesRegex(ValueError, "disagree"):
            defaults.validate_html(defaults.HTML_MARKER + "const LOOKUP={}", lookup)

    def test_existing_four_file_report_refresh_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report"
            report.mkdir()
            (report / "DoseLookup.json").write_text(json.dumps(embedded_lookup()))
            for name in ("DoseLookup.tsv", "DoseCalibrationReport.html", "DoseCalibrationOverview.png"):
                (report / name).write_text("old")
            (report / "fine").mkdir()
            with patch("generate_calibration_dose_report.html_report", side_effect=fake_html), \
                    patch("generate_calibration_dose_report.write_tsv", side_effect=lambda p, _: p.write_text("tsv")):
                defaults.ensure_report(report)
                first = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in report.iterdir() if p.is_file()}
                defaults.ensure_report(report)
                second = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in report.iterdir() if p.is_file()}
            self.assertEqual(first, second)
            self.assertTrue((report / "fine").is_dir())
            self.assertEqual((report / "DoseCalibrationOverview.png").read_text(), "old")

    def test_missing_legacy_source_preserves_prior_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary)
            lookup = embedded_lookup()
            del lookup["treatment_sources"]["SAL"]["preview_metrics"]
            (report / "DoseLookup.json").write_text(json.dumps(lookup))
            (report / "DoseCalibrationReport.html").write_text("prior page")
            before = {p.name: p.read_bytes() for p in report.iterdir()}
            with patch("dose_landmarks.enrich_lookup", side_effect=FileNotFoundError("missing fields")):
                with self.assertRaises(FileNotFoundError):
                    defaults.ensure_report(report)
            self.assertEqual(before, {p.name: p.read_bytes() for p in report.iterdir()})

    def test_replace_failure_rolls_back_private_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report"
            report.mkdir()
            (report / "DoseLookup.json").write_text(json.dumps(embedded_lookup()))
            (report / "DoseCalibrationReport.html").write_text("prior page")
            before = {p.name: p.read_bytes() for p in report.iterdir()}
            replace = defaults.os.replace
            count = 0

            def fail_second(source, destination):
                nonlocal count
                count += 1
                if count == 2:
                    raise OSError("test replacement failure")
                return replace(source, destination)

            with patch("generate_calibration_dose_report.html_report", side_effect=fake_html), \
                    patch("generate_calibration_dose_report.write_tsv", side_effect=lambda p, _: p.write_text("tsv")), \
                    patch.object(defaults.os, "replace", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "test replacement"):
                    defaults.ensure_report(report)
            self.assertEqual(before, {p.name: p.read_bytes() for p in report.iterdir()})


if __name__ == "__main__":
    unittest.main()
