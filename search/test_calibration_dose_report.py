import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np


MODULE_PATH = Path(__file__).with_name("generate_calibration_dose_report.py")
SPEC = importlib.util.spec_from_file_location("dose_report", MODULE_PATH)
DOSE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOSE)


class TestCalibrationDoseReport(unittest.TestCase):
    def test_weighted_quantile_prioritizes_high_weight_value(self):
        result = DOSE.weighted_quantile([1, 2, 3], [1, 1, 8], 0.75)
        self.assertEqual(result, 3.0)

    def test_interpolation_rejects_extrapolation(self):
        with self.assertRaises(ValueError):
            DOSE.interpolate_checked(
                [9], np.array([10, 20]), np.array([14, 28]))

    def test_inverse_calibration_is_supported(self):
        result = DOSE.interpolate_checked(
            [21], np.array([14, 28]), np.array([10, 20]))
        self.assertEqual(float(result[0]), 15.0)

    def test_bounded_grid_includes_irregular_endpoint_without_overshoot(self):
        values = DOSE.bounded_grid(0.0, 151.235, 0.25)
        self.assertEqual(values[-1], 151.235)
        self.assertLessEqual(float(values.max()), 151.235)
        DOSE.interpolate_checked(
            values, np.array([0.0, 151.235]), np.array([0.0, 100.0]))

    def test_bounded_grid_does_not_duplicate_regular_endpoint(self):
        np.testing.assert_array_equal(
            DOSE.bounded_grid(0.0, 1.0, 0.25),
            np.array([0.0, 0.25, 0.5, 0.75, 1.0]))

    def test_dose_selection_uses_lower_intensity_for_metric_tie(self):
        rows = [
            {"treatment_mso": 20.0, "binary_on_target": 0.5,
             "feasible": True},
            {"treatment_mso": 10.0, "binary_on_target": 0.5,
             "feasible": True},
            {"treatment_mso": 5.0, "binary_on_target": 0.9,
             "feasible": False},
        ]
        selected = DOSE.choose_dose(rows, "binary_on_target")
        self.assertEqual(selected["treatment_mso"], 10.0)

    def test_linear_field_scaling_controls_hotspot(self):
        rows = DOSE.evaluate_doses(
            field=np.array([1.0, 0.5]), reference_didt=1.0,
            threshold=1.0, target_mask=np.array([True, False]),
            target_weights=np.array([1.0, 0.0]),
            vertex_area=np.array([1.0, 1.0]),
            treatment_mso=np.array([10.0, 20.0]),
            treatment_didt=np.array([1.0, 2.0]),
            minimum_hotspot_area=1.0)
        self.assertEqual(rows[0]["hotspot_area_mm2"], 1.0)
        self.assertEqual(rows[1]["hotspot_area_mm2"], 2.0)

    def test_rmt_relative_ceiling_is_a_hard_feasibility_constraint(self):
        rows = DOSE.evaluate_doses(
            field=np.array([1.0]), reference_didt=1.0,
            threshold=0.1, target_mask=np.array([True]),
            target_weights=np.array([1.0]), vertex_area=np.array([100.0]),
            treatment_mso=np.array([54.0, 54.1, 60.0]),
            treatment_didt=np.array([1.0, 1.1, 1.2]),
            minimum_hotspot_area=50.0, maximum_treatment_mso=54.0,
            rmt_mso=45.0)
        self.assertTrue(rows[0]["feasible"])
        self.assertTrue(rows[0]["within_rmt_safety_limit"])
        self.assertFalse(rows[1]["feasible"])
        self.assertFalse(rows[2]["within_rmt_safety_limit"])
        selected = DOSE.choose_dose(rows, "binary_on_target")
        self.assertEqual(selected["treatment_mso"], 54.0)
        self.assertAlmostEqual(selected["treatment_to_rmt_ratio"], 1.2)

    def test_disabled_rmt_ceiling_does_not_limit_upward_or_downward_doses(self):
        rows = DOSE.evaluate_doses(
            field=np.array([1.0]), reference_didt=1.0,
            threshold=0.1, target_mask=np.array([True]),
            target_weights=np.array([1.0]), vertex_area=np.array([100.0]),
            treatment_mso=np.array([20.0, 80.0]),
            treatment_didt=np.array([1.0, 2.0]),
            minimum_hotspot_area=50.0, maximum_treatment_mso=None,
            rmt_mso=45.0)
        self.assertTrue(all(row["feasible"] for row in rows))
        self.assertTrue(all(row["within_rmt_safety_limit"] for row in rows))

    def test_network_rgb_is_serialized_as_canonical_hex(self):
        self.assertEqual(DOSE.rgb_hex([0, 0, 0]), "#000000")
        self.assertEqual(DOSE.rgb_hex([0, 1, 1]), "#00ffff")
        self.assertEqual(DOSE.rgb_hex([111, 44, 145]), "#6f2c91")

    def test_normalized_calibration_column_is_auto_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / "calibration.csv"
            table.write_text(
                "amplitude_pct_mso,realized_didt_A_per_us\n"
                "5,4\n10,13\n")
            mso, didt, column = DOSE.read_calibration(table)
        np.testing.assert_array_equal(mso, [5, 10])
        np.testing.assert_array_equal(didt, [4, 13])
        self.assertEqual(column, "realized_didt_A_per_us")

    def test_target_calibration_preserves_hardware_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / "calibration.csv"
            table.write_text(
                "amplitude_pct_mso,realized_didt_A_per_us\n"
                "5,4\n10,13\n")
            target = {"config": {
                "network": "Salience",
                "coil": "/coils/MagVenture_Cool-B65.ccd",
                "coil_hardware_name": "MagVenture Cool-B65 A/P",
                "coil_calibration": {
                    "id": "b65-ap", "csv": str(table),
                    "value_column": "realized_didt_A_per_us",
                    "selection_mode": "explicit_exact_registry_entry",
                    "is_exact_simnibs_coil_match": True,
                },
            }}
            mso, didt, identity = DOSE.target_calibration(target)
        self.assertEqual(identity["id"], "b65-ap")
        self.assertEqual(
            identity["coil_hardware_name"], "MagVenture Cool-B65 A/P")
        self.assertEqual(identity["mso_range"], [5.0, 10.0])


if __name__ == "__main__":
    unittest.main()
