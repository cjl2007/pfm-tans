import base64
import copy
import json
import os
from pathlib import Path
import unittest

import numpy as np

import dose_landmarks as landmarks


REPORT = landmarks.load_report_module()
# Optional external regression fixture; ordinary unit runs use synthetic data only.
REFERENCE_LOOKUP = Path(os.environ.get("TANS_TEST_DOSE_LOOKUP", ""))


class TestLandmarks(unittest.TestCase):
    def rows(self, field=(2.0, 1.0), area=(10.0, 60.0), mask=(False, True),
             mso=(0.0, 0.5, 1.0, 2.0), limit=None):
        return REPORT.evaluate_doses(
            np.array(field), 1.0, 1.0, np.array(mask), np.array(mask, dtype=float),
            np.array(area), np.array(mso), np.array(mso), 50.0,
            maximum_treatment_mso=limit, rmt_mso=1.0)

    def test_global_and_target_onset_are_distinct(self):
        args = (np.array([2.0, 1.0]), 1.0, 1.0, np.array([0.0, 2.0]), np.array([0.0, 2.0]))
        self.assertEqual(landmarks.analytic_onset(*args), 0.5)
        self.assertEqual(landmarks.analytic_onset(*args, mask=[False, True]), 1.0)
        found = landmarks.row_landmarks(self.rows(), REPORT.rounded_row)
        self.assertEqual(found["first_cortex_row"]["treatment_mso"], 0.5)
        self.assertEqual(found["first_target_row"]["treatment_mso"], 1.0)

    def test_missing_or_out_of_range_crossing_is_none(self):
        for field, mask in [([0.0, np.nan], None), ([1.0, 2.0], [False, False]),
                            ([0.1, 0.2], None)]:
            self.assertIsNone(landmarks.analytic_onset(field, 1.0, 1.0, [0.0, 1.0], [0.0, 1.0], mask))
        self.assertIsNone(landmarks.analytic_onset([2.0], 1.0, 1.0, [1.0, 2.0], [1.0, 2.0]))

    def test_empty_target_and_zero_field_have_no_feasible_or_peak(self):
        found = landmarks.row_landmarks(self.rows(field=(0.0, np.nan), mask=(False, False)),
                                        REPORT.rounded_row)
        self.assertTrue(all(value is None for value in found.values()))

    def test_area_and_threshold_equalities_count(self):
        found = landmarks.row_landmarks(self.rows(field=(1.0,), area=(50.0,), mask=(True,),
                                                  mso=(0.9, 1.0)), REPORT.rounded_row)
        self.assertEqual(found["first_feasible"]["treatment_mso"], 1.0)

    def test_ceiling_does_not_hide_diagnostic_onset(self):
        found = landmarks.row_landmarks(self.rows(limit=0.8), REPORT.rounded_row)
        self.assertEqual(found["first_geometrically_feasible"]["treatment_mso"], 1.0)
        self.assertIsNone(found["first_feasible"])
        self.assertFalse(found["first_target_row"]["within_rmt_safety_limit"])

    def test_plateau_uses_earliest_row_and_ignores_area_minimum(self):
        found = landmarks.row_landmarks(self.rows(field=(2.0, 1.0), mask=(True, False)),
                                        REPORT.rounded_row)
        self.assertEqual(found["unconstrained_selectivity_peak"]["treatment_mso"], 0.5)
        self.assertFalse(found["unconstrained_selectivity_peak"]["feasible"])

    def test_piecewise_inverse_not_endpoint_linear_interpolation(self):
        self.assertEqual(landmarks.analytic_onset([1.0], 3.0, 1.0,
                                                 [0.0, 10.0, 20.0], [0.0, 1.0, 5.0]), 15.0)

    def test_curve_insertions_preserve_existing_and_deduplicate(self):
        existing = [{"treatment_mso": 0.0}, {"treatment_mso": 2.0}]
        original = copy.deepcopy(existing)
        merged = landmarks.merge_curve_rows(existing, [{"treatment_mso": 1.0},
                                                       {"treatment_mso": 2.0}, None])
        self.assertEqual(existing, original)
        self.assertEqual([row["treatment_mso"] for row in merged], [0.0, 1.0, 2.0])

    def test_changed_selection_is_rejected_and_round8_is_tolerated(self):
        landmarks.validate_row({"score": 0.12345679}, {"score": 0.1234567891}, "test")
        for recorded, actual in [(None, {"score": 1.0}), ({"score": 1.0}, None),
                                  ({"score": 1.0}, {"score": 0.5})]:
            with self.assertRaises(ValueError):
                landmarks.validate_row(recorded, actual, "test")

    def test_runtime_prefixes_preserve_threshold_equality_and_exclude_nonfinite(self):
        field = np.array([2.0, 1.0, np.nan, np.inf, 0.0, -1.0])
        mask = np.array([False, True, True, True, False, False])
        weights = np.array([0.0, 0.5, 1.0, 1.0, 0.0, 0.0])
        area = np.array([10.0, 60.0, 4.0, 6.0, 3.0, 2.0])
        runtime = landmarks.runtime_metrics(field, mask, weights, area)
        def decoded(name):
            return np.frombuffer(base64.b64decode(runtime[name]), dtype="<f8")
        sorted_field = decoded("field_f64le_base64")
        self.assertTrue(np.all(np.isfinite(sorted_field)))
        self.assertTrue(np.all(np.diff(sorted_field) >= 0))
        self.assertEqual(runtime["total_target_area_mm2"], 70.0)
        self.assertEqual(runtime["total_target_mass"], 40.0)
        prefixes = [decoded(name) for name in ("area_prefix_f64le_base64",
                    "target_area_prefix_f64le_base64", "target_mass_prefix_f64le_base64")]
        for prefix in prefixes:
            self.assertEqual(len(prefix), len(sorted_field) + 1)
            self.assertEqual(prefix[0], 0.0)
        for didt in [0.0, 0.5, 1.0, 1.7]:
            index = next((i for i, value in enumerate(sorted_field) if value * didt >= 1.0),
                         len(sorted_field))
            with np.errstate(invalid="ignore"):
                scaled = field * didt
            hotspot = np.isfinite(scaled) & (scaled >= 1.0)
            expected = [area[hotspot].sum(), area[hotspot & mask].sum(),
                        (area[hotspot & mask] * weights[hotspot & mask]).sum()]
            self.assertEqual([prefix[-1] - prefix[index] for prefix in prefixes], expected)

    @unittest.skipUnless(REFERENCE_LOOKUP.is_file(), "Set TANS_TEST_DOSE_LOOKUP to opt into the external reference test")
    def test_cir002_rmt60_regression_and_non_mutation(self):
        lookup = json.loads(REFERENCE_LOOKUP.read_text())
        lookup["entries"] = [entry for entry in lookup["entries"] if entry["rmt_mso"] == 60]
        before = json.dumps(lookup, sort_keys=True)
        enriched = landmarks.enrich_lookup(lookup, REPORT)
        self.assertEqual(json.dumps(lookup, sort_keys=True), before)
        self.assertEqual(enriched["policy"], lookup["policy"])
        self.assertEqual(enriched["entries"][0]["treatments"], lookup["entries"][0]["treatments"])
        found = enriched["entries"][0]["treatment_landmarks"]["Salience"]
        self.assertAlmostEqual(found["first_cortex_mso"], 54.5803693, places=5)
        self.assertEqual(found["first_cortex_mso"], found["first_target_mso"])
        self.assertEqual(found["first_cortex_row"]["treatment_mso"], 54.6)
        self.assertEqual(found["first_feasible"]["treatment_mso"], 58.4)
        self.assertEqual(enriched["entries"][0]["treatments"]["Salience"]["treatment_mso"], 60.2)
        curve = enriched["dose_response_curves"]["Salience"]["60"]
        self.assertTrue({54.6, 58.4, 60.2}.issubset({row["treatment_mso"] for row in curve}))
        calibration = enriched["treatment_sources"]["Salience"]["calibration"]
        self.assertEqual(len(calibration["samples_mso"]), len(calibration["samples_didt_a_per_us"]))
        source = lookup["treatment_sources"]["Salience"]
        field, mask, weights, area, reference = landmarks.source_context(source, REPORT)
        self.assertEqual(enriched["calibration"]["samples_mso"][0], 0.0)
        self.assertAlmostEqual(enriched["calibration"]["threshold_v_per_m_per_calibration_didt"] * 93.0,
                               enriched["entries"][0]["preview_absolute_threshold_v_per_m"])
        runtime = enriched["treatment_sources"]["Salience"]["preview_metrics"]
        self.assertEqual(len(runtime["treatment_grid_mso"]), 606)
        sorted_field = np.frombuffer(base64.b64decode(runtime["field_f64le_base64"]), dtype="<f8")
        area_prefix = np.frombuffer(base64.b64decode(runtime["area_prefix_f64le_base64"]), dtype="<f8")
        scale = 90.28 / reference
        threshold = enriched["entries"][0]["preview_absolute_threshold_v_per_m"]
        index = np.flatnonzero(sorted_field * scale >= threshold)[0]
        self.assertAlmostEqual(area_prefix[-1] - area_prefix[index], 67.47753954, places=6)
        row = REPORT.evaluate_doses(field, reference, lookup["entries"][0]["absolute_threshold_v_per_m"],
                                    mask, weights, area, [57.4],
                                    np.interp([57.4], calibration["samples_mso"],
                                              calibration["samples_didt_a_per_us"]), 50.0, 72.0, 60.0)[0]
        self.assertFalse(row["feasible"])
        self.assertAlmostEqual(row["hotspot_area_mm2"], 32.71214676)
        lookup["entries"][0]["treatments"]["Salience"]["treatment_mso"] = 59.0
        with self.assertRaisesRegex(ValueError, "changed treatment_mso"):
            landmarks.enrich_lookup(lookup, REPORT)


if __name__ == "__main__":
    unittest.main()
