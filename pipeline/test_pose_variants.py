import json
from pathlib import Path
import tempfile
import unittest

from generate_pose_variants import available, write_dose_navigation
from validate_pose_variants import check_dose_source


class PoseVariantsTests(unittest.TestCase):
    def test_disabled_robustness_exposes_only_fine(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'config.json'
            config.write_text('{"robustness": {"enabled": false}}')
            self.assertEqual(available({'coarse_config': config}), ('fine',))

    def test_dose_navigation_requires_explicit_reference_and_treatment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pairs = [{'calibration_variant': c, 'treatment_variant': t,
                      'report': str(root / t / f'sm-hand-{c}')}
                     for c in ('fine', 'robust') for t in ('fine', 'robust')]
            write_dose_navigation(root, pairs, 'TEST')
            document = (root / 'index.html').read_text()
            for pair in pairs:
                self.assertIn(f"{pair['treatment_variant']}/sm-hand-{pair['calibration_variant']}/DoseCalibrationReport.html", document)
            self.assertIn('id="lookup" hidden', document)
            self.assertNotIn('selected>', document)
            self.assertIn('reference', document)
            self.assertTrue((root / 'fine/index.html').is_file())
            self.assertTrue((root / 'robust/index.html').is_file())

    def test_dosing_rejects_wrong_source_field_or_variant(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = root / 'CoarseRunConfig.json'
            cfg.write_text('{"robustness": {"enabled": false}, "coil_center_constraint": {"enabled": false}}')
            plan = root / 'search/fine/plan'
            plan.mkdir(parents=True)
            (plan / 'FineRunConfig.json').write_text('{}')
            result = root / 'search/fine/simulation'
            center = result / 'Center_00001'
            center.mkdir(parents=True)
            best = {'center_id': 1, 'orientation_degrees': 5}
            (result / 'BestCoarsePose.json').write_text(json.dumps(best))
            (center / 'Benchmark.json').write_text(json.dumps({
                'orientations_degrees': [0, 5], 'didt_aperus': 1000000}))
            source = {'network': 'Test', 'selection_mode': 'nominal_fine',
                      'selected_pose': best, 'reference_field': str(center / 'magnE.dtseries.nii'),
                      'reference_map_index_zero_based': 1, 'reference_didt_a_per_us': 1}
            target = {'target_root': root, 'coarse_config': cfg, 'network': 'Test'}
            check_dose_source(source, target, 'fine')
            with self.assertRaises(ValueError):
                check_dose_source(dict(source, reference_map_index_zero_based=0), target, 'fine')
            with self.assertRaises(ValueError):
                check_dose_source(dict(source, selection_mode='robust'), target, 'fine')
            with self.assertRaises(ValueError):
                check_dose_source(dict(source, reference_field='/wrong/field.nii'), target, 'fine')


if __name__ == '__main__':
    unittest.main()
