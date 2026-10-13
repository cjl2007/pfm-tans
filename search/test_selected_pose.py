import json
from pathlib import Path
import tempfile
import unittest

from selected_pose import robustness_enabled, selected_pose_paths


class SelectedPoseTests(unittest.TestCase):
    def test_default_is_robust_and_invalid_values_fail(self):
        self.assertTrue(robustness_enabled({}))
        self.assertFalse(robustness_enabled({'robustness': {'enabled': False}}))
        for value in ('false', '0', 0, 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                robustness_enabled({'robustness': {'enabled': value}})

    def test_semantic_and_legacy_paths_never_fall_back(self):
        for semantic in (True, False):
            with self.subTest(semantic=semantic), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                coarse = root / 'CoarseRunConfig.json'
                coarse.write_text(json.dumps({'robustness': {'enabled': False},
                                              'coil_center_constraint': {'enabled': False}}))
                fine_plan = root / ('search/fine/plan' if semantic else '03_FinePlan')
                fine_result = root / ('search/fine/simulation' if semantic else '04_FineSimulation')
                fine_plan.mkdir(parents=True)
                fine_result.mkdir(parents=True)
                (fine_plan / 'FineRunConfig.json').write_text('{}')
                (fine_result / 'BestCoarsePose.json').write_text('{}')
                selected = selected_pose_paths(root, coarse if semantic else None)
                self.assertEqual(selected['result'], fine_result)
                self.assertEqual(selected['mode'], 'nominal_fine')
                self.assertEqual(selected['best_pose'], fine_result / 'BestCoarsePose.json')
                coarse.write_text('{}')
                with self.assertRaises(FileNotFoundError):
                    selected_pose_paths(root, coarse if semantic else None)

    def test_explicit_fine_does_not_change_robustness_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coarse = root / 'CoarseRunConfig.json'
            coarse.write_text('{"robustness": {"enabled": true}, "coil_center_constraint": {"enabled": false}}')
            for relative in ('03_FinePlan/FineRunConfig.json',
                             '04_FineSimulation/BestCoarsePose.json',
                             '05_RobustnessPlan/RobustRunConfig.json',
                             '07_RobustnessAnalysis/BestRobustPose.json'):
                path = root / relative
                path.parent.mkdir(exist_ok=True)
                path.write_text('{}')
            fine = selected_pose_paths(root, variant='fine')
            robust = selected_pose_paths(root, variant='robust')
            self.assertEqual(fine['mode'], 'nominal_fine')
            self.assertEqual(fine['variant'], 'fine')
            self.assertTrue(fine['robustness_enabled'])
            self.assertEqual(robust, selected_pose_paths(root))
            self.assertNotEqual(fine['best_pose'], robust['best_pose'])
            self.assertTrue(json.loads(coarse.read_text())['robustness']['enabled'])

    def test_robust_cannot_be_requested_when_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'CoarseRunConfig.json').write_text('{"robustness": {"enabled": false}}')
            with self.assertRaisesRegex(ValueError, 'disabled'):
                selected_pose_paths(root, variant='robust')
            with self.assertRaisesRegex(ValueError, 'Unknown'):
                selected_pose_paths(root, variant='other')
