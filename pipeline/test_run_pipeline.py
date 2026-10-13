#!/usr/bin/env python3

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("run_pipeline.py")
SPEC = importlib.util.spec_from_file_location("run_pipeline", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RunPipelineTests(unittest.TestCase):
    def test_ridgepfm_directory_precedence_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = Path(directory)/"SUB001"
            parent = subject/"func"/"task"/"PFM"
            current, legacy = parent/"RidgePFM", parent/"RidgeFusion"
            self.assertEqual(MODULE.resolve_pfm_directory(subject, "task"), current)
            legacy.mkdir(parents=True)
            self.assertEqual(MODULE.resolve_pfm_directory(subject, "task"), legacy)
            current.mkdir()
            self.assertEqual(MODULE.resolve_pfm_directory(subject, "task"), current)
            override = Path(directory)/"custom maps"
            self.assertEqual(MODULE.resolve_pfm_directory(subject, "task", str(override)), override)

    def test_unsupported_python_fails_before_inputs_or_outputs(self):
        for version in ((3, 7, 3), (3, 9, 0)):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "must-not-be-created"
                command = (
                    "import runpy, sys; "
                    f"sys.version_info = {version!r}; "
                    f"sys.version = {'.'.join(map(str, version))!r}; "
                    f"sys.argv = [{str(MODULE_PATH)!r}, "
                    f"{str(root / 'missing-config.yaml')!r}, "
                    f"'--output', {str(output)!r}]; "
                    f"runpy.run_path({str(MODULE_PATH)!r}, run_name='__main__')")
                result = subprocess.run(
                    [sys.executable, "-B", "-c", command],
                    capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("requires Python 3.10+", result.stderr)
                self.assertIn("TANS_PYTHON", result.stderr)
                self.assertIn(sys.executable, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(output.exists())

    def test_supported_python_can_show_help(self):
        result = subprocess.run(
            [sys.executable, "-B", str(MODULE_PATH), "--help"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--subject-dir", result.stdout)

    def test_template_rendering_is_recursive(self):
        value = {
            "subject": "{subject}",
            "inputs": ["{subject_dir}/anat/T1w/T1w.nii.gz", 7],
        }
        rendered = MODULE.render_strings(value, {
            "subject": "CIR002",
            "subject_dir": "/study/CIR002",
        })
        self.assertEqual(rendered["subject"], "CIR002")
        self.assertEqual(
            rendered["inputs"], ["/study/CIR002/anat/T1w/T1w.nii.gz", 7])

    def test_stage_order(self):
        self.assertLess(MODULE.stage_index("headmodel"),
                        MODULE.stage_index("targets"))
        self.assertLess(MODULE.stage_index("search"),
                        MODULE.stage_index("reports"))

    def test_target_plan_runs_calibration_before_treatment(self):
        config = {
            "search": {"map_mode": "weighted"},
            "targets": [{"network": "Somatomotor_Hand", "display_name": "SM-Hand"}],
        }
        with mock.patch.object(
                MODULE, "require_file",
                side_effect=lambda value, _label: Path(value)):
            MODULE.configure_target_plan(
                config, "Salience", "/mask.dscalar.nii",
                "Glasser predefined LPFC + subject-specific MFG", True,
                "/coils/C-B60.ccd", "/coils/Cool-B65.ccd")
        self.assertEqual([item["network"] for item in config["targets"]],
                         ["Somatomotor_Hand", "Salience"])
        self.assertTrue(config["workflow"]["dose_transfer_requested"])
        self.assertEqual(config["targets"][0]["coil"], "/coils/C-B60.ccd")
        self.assertEqual(config["targets"][1]["coil"], "/coils/Cool-B65.ccd")
        self.assertEqual(config["targets"][1]["anatomical_zone_display"],
                         "Glasser predefined LPFC + subject-specific MFG")

    def test_skip_calibration_requires_treatment_target(self):
        config = {"targets": [{"network": "Somatomotor_Hand"}]}
        with self.assertRaisesRegex(ValueError, "requires a treatment target"):
            MODULE.configure_target_plan(
                config, None, None, "configured cortical search space", False,
                "/C-B60.ccd", "/Cool-B65.ccd")

    def test_dosing_calibrations_are_attached_to_matching_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c60 = root / "C-B60.ccd"
            b65 = root / "Cool-B65.ccd"
            mask = root / "mask.dscalar.nii"
            for path in (c60, b65, mask):
                path.write_text("test")
            table = (
                "amplitude_pct_mso,realized_didt_A_per_us\n"
                "10,12\n20,25\n")
            (root / "c60.csv").write_text(table)
            (root / "b65.csv").write_text(table)
            registry = root / "registry.json"
            registry.write_text(json.dumps({
                "schema_version": "tans-stimulator-calibration-registry-v1",
                "entries": [
                    {"id": "c60", "coil_hardware_name": "C-B60",
                     "simnibs_coil_model_basename": c60.name,
                     "intended_roles": ["sm_hand_motor_threshold"],
                     "csv": "c60.csv", "value_column": "realized_didt_A_per_us",
                     "mso_range": [10, 20]},
                    {"id": "b65-ap", "coil_hardware_name": "Cool-B65 A/P",
                     "simnibs_coil_model_basename": b65.name,
                     "intended_roles": ["treatment"], "csv": "b65.csv",
                     "value_column": "realized_didt_A_per_us",
                     "mso_range": [10, 20]},
                ]}))
            config = {
                "search": {"map_mode": "weighted"},
                "targets": [{"network": "Somatomotor_Hand",
                             "display_name": "SM-Hand"}],
            }
            MODULE.configure_target_plan(
                config, "Salience", str(mask),
                "Glasser predefined LPFC + subject-specific MFG", True,
                c60, b65)
            MODULE.configure_dosing_calibrations(
                config, registry, None, "b65-ap", False, "")
        self.assertEqual(config["targets"][0]["coil_hardware_name"], "C-B60")
        self.assertEqual(config["targets"][1]["coil_hardware_name"],
                         "Cool-B65 A/P")
        self.assertEqual(config["workflow"]["dosing_calibrations"]
                         ["selections"]["treatment"]["id"], "b65-ap")

    def test_treatment_identity_is_resolved_when_sm_hand_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c60 = root / "C-B60.ccd"
            b65 = root / "Cool-B65.ccd"
            mask = root / "mask.dscalar.nii"
            for path in (c60, b65, mask):
                path.write_text("test")
            (root / "b65.csv").write_text(
                "amplitude_pct_mso,realized_didt_A_per_us\n"
                "10,12\n20,25\n")
            registry = root / "registry.json"
            registry.write_text(json.dumps({
                "schema_version": "tans-stimulator-calibration-registry-v1",
                "entries": [{
                    "id": "b65-ap", "coil_hardware_name": "Cool-B65 A/P",
                    "simnibs_coil_model_basename": b65.name,
                    "intended_roles": ["treatment"], "csv": "b65.csv",
                    "value_column": "realized_didt_A_per_us",
                    "mso_range": [10, 20],
                }],
            }))
            config = {
                "search": {"map_mode": "weighted"},
                "targets": [{"network": "Somatomotor_Hand"}],
            }
            MODULE.configure_target_plan(
                config, "Salience", str(mask),
                "Glasser predefined LPFC + subject-specific MFG", False,
                c60, b65)
            MODULE.configure_dosing_calibrations(
                config, registry, None, None, False, "")
        self.assertEqual(len(config["targets"]), 1)
        self.assertEqual(
            config["targets"][0]["coil_hardware_name"], "Cool-B65 A/P")


if __name__ == "__main__":
    unittest.main()
