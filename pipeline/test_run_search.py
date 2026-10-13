#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import unittest
import json
import tempfile
from unittest import mock


MODULE_PATH = Path(__file__).with_name("run_search.py")
SPEC = importlib.util.spec_from_file_location("run_search", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RunSearchTests(unittest.TestCase):
    def fixture(self, root, enabled):
        target = {'network': 'Test', 'display_name': 'Test', 'target_root': str(root),
                  'coarse_config': str(root / 'CoarseRunConfig.json')}
        Path(target['coarse_config']).write_text(json.dumps({
            'robustness': {'enabled': enabled}, 'coil_center_constraint': {'enabled': False}}))
        paths = MODULE.stage_paths(root)
        for stage, count, seconds in [('coarse', 24, 60), ('fine', 10, 30)]:
            result = paths[stage + '_result']
            result.mkdir(parents=True)
            (result / 'CoarseRunSummary.json').write_text(json.dumps({
                'poses': count, 'wall_seconds': seconds}))
        paths['fine_plan'].mkdir(parents=True)
        (paths['fine_plan'] / 'FineRunConfig.json').write_text(
            '{"coil_center_constraint": {"enabled": false}}')
        (paths['fine_result'] / 'BestCoarsePose.json').write_text(json.dumps({
            'center_id': 2, 'orientation_degrees': 15, 'passes_target_peak_ratio': True}))
        return target, paths

    def test_disabled_runs_no_perturbations_and_records_zero_overhead(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, paths = self.fixture(root, False)
            before = (paths['fine_result'] / 'BestCoarsePose.json').read_bytes()
            with mock.patch.object(MODULE, 'run') as run:
                MODULE.run_target(target, Path('/module'), 6, 'robustness', False)
                run.assert_not_called()
            self.assertFalse((root / 'search/robustness').exists())
            runtime = json.loads((root / 'RuntimeSummary.json').read_text())
            self.assertEqual(runtime['robustness_poses'], 0)
            self.assertEqual(runtime['robustness_total_wall_seconds'], 0)
            self.assertEqual(runtime['baseline_coarse_plus_fine_wall_seconds'], 90)
            self.assertFalse(runtime['robustness_enabled'])
            self.assertEqual(json.loads((root / 'RunState.json').read_text())[
                'stages']['robustness']['status'], 'skipped_by_config')
            selected = json.loads((root / 'SelectionSummary.json').read_text())
            self.assertEqual(selected['selection_mode'], 'nominal_fine')
            self.assertEqual(before, Path(selected['selected_pose_source']).read_bytes())

    def test_disabled_rejects_stale_robustness_or_failed_peak_gate(self):
        for stale in (True, False):
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as directory:
                target, paths = self.fixture(Path(directory), False)
                if stale:
                    paths['robust_plan'].mkdir(parents=True)
                else:
                    (paths['fine_result'] / 'BestCoarsePose.json').write_text(json.dumps({
                        'passes_target_peak_ratio': False}))
                with mock.patch.object(MODULE, 'run') as run, self.assertRaises(ValueError):
                    MODULE.run_target(target, Path('/module'), 6, 'robustness', False)
                run.assert_not_called()

    def test_enabled_preserves_completed_robustness_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, paths = self.fixture(root, True)
            for key in ('robust_plan', 'robust_result', 'robust_analysis'):
                paths[key].mkdir(parents=True)
            for key, name, data in (
                ('robust_plan', 'RobustRunConfig.json', {}),
                ('robust_plan', 'RobustPlanSummary.json', {'planning_wall_seconds': 2}),
                ('robust_result', 'CoarseRunSummary.json', {'wall_seconds': 180, 'poses': 90}),
                ('robust_analysis', 'RobustnessSummary.json', {'analysis_wall_seconds': 3}),
            ):
                (paths[key] / name).write_text(json.dumps(data))
            with mock.patch.object(MODULE, 'run') as run:
                MODULE.run_target(target, Path('/module'), 6, 'robustness', False)
                run.assert_not_called()
            runtime = json.loads((root / 'RuntimeSummary.json').read_text())
            self.assertTrue(runtime['robustness_enabled'])
            self.assertEqual(runtime['robustness_poses'], 90)
            self.assertEqual(runtime['robustness_total_wall_seconds'], 185)

    def test_semantic_stage_paths(self):
        root = Path("/tmp/example/targets/SM-Hand")
        paths = MODULE.stage_paths(root)
        self.assertEqual(paths["coarse_result"],
                         root / "search" / "coarse" / "simulation")
        self.assertEqual(paths["robust_analysis"],
                         root / "search" / "robustness" / "analysis")


if __name__ == "__main__":
    unittest.main()
