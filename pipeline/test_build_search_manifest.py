#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("build_search_manifest.py")
SPEC = importlib.util.spec_from_file_location("build_search_manifest", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildSearchManifestTests(unittest.TestCase):
    def test_robustness_flag_is_preserved_in_simulation_config(self):
        config = {'subject': 'TEST', 'inputs': {
            'categorical_networks': '/labels', 'vertex_area': '/area',
            'network_metadata': '/metadata'},
            'subject_adapter': {'workbench': '/wb', 'subject_dir': '/subject',
                'head_mesh': '/head', 'm2m_dir': '/m2m', 'skin': '/skin',
                'surfaces': {key: '/' + key for key in (
                    'left_midthickness', 'right_midthickness', 'left_white',
                    'right_white', 'left_pial', 'right_pial', 'left_roi', 'right_roi')}},
            'simulation': {'coil': '/coil', 'simnibs_python': '/python'}}
        with mock.patch.object(MODULE, 'require_file', side_effect=lambda path, label: Path(path)), \
                mock.patch.object(MODULE, 'require_directory', side_effect=lambda path, label: Path(path)):
            default = MODULE.shared_configuration(config, {})
            config['robustness'] = {'enabled': False}
            disabled = MODULE.shared_configuration(config, {})
        self.assertTrue(default['robustness'].pop('enabled'))
        self.assertFalse(disabled['robustness'].pop('enabled'))
        self.assertEqual(default, disabled)
        self.assertEqual(default['network_metadata'], '/metadata')

    def test_output_root_allows_preexisting_config_and_shared_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "shared").mkdir()
            self.assertEqual(MODULE.initialize_output_root(root), root.resolve())

    def test_output_root_rejects_existing_search_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "targets").mkdir()
            with self.assertRaises(FileExistsError):
                MODULE.initialize_output_root(root)

    def test_retention_profiles_are_explicit(self):
        self.assertFalse(MODULE.resolve_retention("standard")[
            "keep_raw_simulation_workdir"])
        self.assertTrue(MODULE.resolve_retention("standard")[
            "cleanup_after_validation"])
        self.assertFalse(MODULE.resolve_retention("debug")[
            "cleanup_after_validation"])
        self.assertTrue(MODULE.resolve_retention("research")[
            "keep_raw_simulation_workdir"])

    def test_orientation_default_covers_half_turn(self):
        self.assertEqual(MODULE.orientations({}), list(range(0, 180, 15)))

    def test_orientation_values_must_be_unique_and_below_180(self):
        with self.assertRaises(ValueError):
            MODULE.orientations({"orientations_degrees": [0, 15, 15]})
        with self.assertRaises(ValueError):
            MODULE.orientations({"orientations_degrees": [0, 180]})

    def test_policy_override_is_written_as_explicit_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy = Path(temporary) / "policy.json"
            policy.write_text(
                '{"policy_name":"p","policy_status":"working_default",'
                '"target_weighted_coverage_fraction":0.85}')
            resolved, source = MODULE.resolve_policy({
                "search": {"policy": str(policy), "coverage_fraction": 0.9}})
            self.assertEqual(source, policy.resolve())
            self.assertEqual(resolved["target_weighted_coverage_fraction"], 0.9)
            self.assertEqual(resolved["policy_status"], "explicit_run_override")


if __name__ == "__main__":
    unittest.main()
