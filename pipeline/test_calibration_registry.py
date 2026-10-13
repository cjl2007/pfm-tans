#!/usr/bin/env python3

import json
from pathlib import Path
import tempfile
import unittest

from calibration_registry import CalibrationSelectionError, resolve_calibration


class CalibrationRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.coil_a = self.root / "Coil-A.ccd"
        self.coil_b = self.root / "Coil-B.ccd"
        self.coil_a.write_text("coil")
        self.coil_b.write_text("coil")
        for name in ("a1", "a2", "b"):
            (self.root / f"{name}.csv").write_text(
                "amplitude_pct_mso,realized_didt_A_per_us\n10,12\n20,25\n")
        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps({
            "schema_version": "tans-stimulator-calibration-registry-v1",
            "entries": [
                {"id": "a1", "simnibs_coil_model_basename": "Coil-A.ccd",
                 "coil_hardware_name": "A one", "intended_roles": ["treatment"],
                 "csv": "a1.csv", "value_column": "realized_didt_A_per_us",
                 "mso_range": [10, 20]},
                {"id": "a2", "simnibs_coil_model_basename": "Coil-A.ccd",
                 "coil_hardware_name": "A two", "intended_roles": ["treatment"],
                 "csv": "a2.csv", "value_column": "realized_didt_A_per_us",
                 "mso_range": [10, 20]},
                {"id": "b", "simnibs_coil_model_basename": "Coil-B.ccd",
                 "coil_hardware_name": "B", "intended_roles": ["sm_hand_motor_threshold"],
                 "csv": "b.csv", "value_column": "realized_didt_A_per_us",
                 "mso_range": [10, 20]},
            ]}))

    def tearDown(self):
        self.temporary.cleanup()

    def test_unique_exact_match_is_automatic(self):
        result = resolve_calibration(
            self.registry, self.coil_b, None, "sm_hand_motor_threshold")
        self.assertEqual(result["id"], "b")
        self.assertTrue(result["is_exact_simnibs_coil_match"])

    def test_ambiguous_geometry_prompts_for_explicit_id(self):
        with self.assertRaisesRegex(CalibrationSelectionError, "ambiguous"):
            resolve_calibration(self.registry, self.coil_a, None, "treatment")

    def test_explicit_hardware_variant_resolves(self):
        result = resolve_calibration(
            self.registry, self.coil_a, "a2", "treatment")
        self.assertEqual(result["coil_hardware_name"], "A two")

    def test_mismatched_coil_requires_consent_and_justification(self):
        with self.assertRaisesRegex(CalibrationSelectionError, "Enable"):
            resolve_calibration(
                self.registry, self.coil_a, "b", "treatment")
        with self.assertRaisesRegex(CalibrationSelectionError, "justification"):
            resolve_calibration(
                self.registry, self.coil_a, "b", "treatment", True, "")
        result = resolve_calibration(
            self.registry, self.coil_a, "b", "treatment", True,
            "Temporary research comparison")
        self.assertFalse(result["is_exact_simnibs_coil_match"])
        self.assertEqual(result["selection_mode"],
                         "explicit_similar_coil_substitution")


if __name__ == "__main__":
    unittest.main()
