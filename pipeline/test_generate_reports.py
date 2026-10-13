import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(os.environ.get(
    "TEST_GENERATE_REPORTS",
    Path(__file__).with_name("generate_reports.py")))
SPEC = importlib.util.spec_from_file_location("generate_reports", MODULE_PATH)
generate_reports = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_reports)


class GenerateReportsTests(unittest.TestCase):
    def test_nominal_reports_use_fine_sources_without_robustness_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ('search/coarse/simulation', 'search/fine/plan',
                             'search/fine/simulation', 'prep'):
                (root / relative).mkdir(parents=True)
            for name in ('SelectedAnatomicalMask', 'FullBinaryTargetSupport',
                         'EligibleBinaryTarget', 'EligibleWeightedTarget',
                         'FullWeightedTargetMap', 'ComponentLabels'):
                (root / 'prep' / f'{name}.dscalar.nii').touch()
            coil = root / 'coil.ccd'
            coil.touch()
            coil.with_suffix('.stl').touch()
            cfg = {'robustness': {'enabled': False}, 'coil': str(coil),
                   'coil_center_constraint': {'enabled': False},
                   'simnibs_python': '/simnibs/python', 'target_preparation': str(root / 'prep'),
                   'coarse_grid_policy': {'coverage_summary': str(root / 'coverage/summary.json')}}
            coarse = root / 'CoarseRunConfig.json'
            coarse.write_text(json.dumps(cfg))
            fine = root / 'search/fine/plan/FineRunConfig.json'
            fine.write_text(json.dumps(cfg))
            best = root / 'search/fine/simulation/BestCoarsePose.json'
            best.write_text('{}')
            target = {'network': 'Test', 'display_name': 'Test',
                      'target_root': str(root), 'coarse_config': str(coarse)}
            with mock.patch.object(generate_reports, 'run') as run, \
                    mock.patch.object(generate_reports, 'visual_inputs', return_value=(root, root)):
                result = generate_reports.render_target_core(target, root, root, root)
            commands = [call.args[0] for call in run.call_args_list]
            selection, optimal, export = commands
            self.assertNotIn('--robust-plan', selection)
            self.assertNotIn('--robust-analysis', selection)
            self.assertEqual(optimal[optimal.index('--config') + 1], str(fine))
            self.assertEqual(optimal[optimal.index('--best-pose') + 1], str(best))
            self.assertEqual(optimal[optimal.index('--fine-result') + 1], str(best.parent))
            self.assertEqual(result['selection_mode'], 'nominal_fine')
            self.assertEqual(result['selected_pose'], str(best))
            self.assertFalse((root / 'work/report_compatibility/Test/06_RobustnessSimulation').exists())
            self.assertIn('--target-root', export)

    def test_all_core_reports_and_dosing_precede_movies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'resolved.yaml'
            config.write_text('{}')
            priors = root / 'Priors.mat'
            priors.touch()
            visual = root / 'visual.json'
            visual.write_text('{}')
            targets = [{'network': network, 'display_name': name,
                        'target_root': str(root / 'targets' / name)}
                       for network, name in [('Somatomotor_Hand', 'SM-Hand'),
                                             ('CinguloOpercular', 'AMN-CO')]]
            for target in targets:
                (Path(target['target_root']) / 'reports').mkdir(parents=True)
            manifest = root / 'MultinetworkRunManifest.json'
            manifest.write_text(json.dumps({'subject': 'TEST', 'configuration': str(config),
                                            'targets': targets}))
            events = []

            def core(target, *args):
                events.append(('core_and_export', target['display_name']))
                return {'network': target['network']}

            def dose(*args):
                events.append(('dose', 'all'))
                return root / 'reports/dose-calibration'

            def replay(target, *args):
                events.append(('replay', target['display_name']))
                self.assertFalse((root / 'ReportIndex.json').exists())

            with mock.patch.object(generate_reports.sys, 'argv', [
                    'generate_reports.py', str(manifest), '--priors', str(priors),
                    '--visualization-config', str(visual)]), \
                    mock.patch('prepare_subject.validate_search_scalp'), \
                    mock.patch.object(generate_reports, 'render_target_core', side_effect=core), \
                    mock.patch.object(generate_reports, 'render_dose_calibration', side_effect=dose), \
                    mock.patch('generate_pose_variants.generate'), \
                    mock.patch('publish_operator_export.publish'), \
                    mock.patch.object(generate_reports, 'render_search_replay', side_effect=replay), \
                    mock.patch.object(generate_reports, 'run'):
                generate_reports.main()
            self.assertEqual(events, [('core_and_export', 'SM-Hand'),
                                      ('core_and_export', 'AMN-CO'), ('dose', 'all'),
                                      ('replay', 'SM-Hand'), ('replay', 'AMN-CO')])
            self.assertTrue((root / 'ReportIndex.json').is_file())

    def test_cross_target_dose_report_receives_calibration_and_treatment(self):
        targets = [
            {"network": "Somatomotor_Hand", "display_name": "SM-Hand",
             "target_root": "/targets/sm"},
            {"network": "Salience", "display_name": "SAL",
             "target_root": "/targets/sal"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with mock.patch.object(
                    generate_reports, "compatibility_view",
                    side_effect=[Path("/views/sm"), Path("/views/sal")]), \
                    mock.patch.object(generate_reports, "run") as run:
                report = generate_reports.render_dose_calibration(
                    targets, Path("/modules"), output, Path('/Priors.mat'), {})
        self.assertEqual(report, output / 'shared/work/publishing/dose-calibration')
        command = run.call_args[0][0]
        self.assertIn("/views/sm", command)
        self.assertIn("/views/sal", command)
        self.assertIn("generate_calibration_dose_report.py", command[1])
        self.assertIn('--priors', command)
        self.assertEqual(command[command.index('--maximum-treatment-rmt-ratio') + 1], '1.2')

    def test_dose_report_is_omitted_without_sm_hand(self):
        target = {"network": "Salience", "display_name": "SAL",
                  "target_root": "/targets/sal"}
        self.assertIsNone(generate_reports.render_dose_calibration(
            [target], Path("/modules"), Path("/output"), Path('/Priors.mat'), {}))

    def test_empty_dosing_safeguard_disables_only_upper_limit(self):
        targets = [{'network': 'Somatomotor_Hand', 'display_name': 'SM-Hand',
                    'target_root': '/targets/sm'},
                   {'network': 'Salience', 'display_name': 'SAL',
                    'target_root': '/targets/sal'}]
        for value in ('', None):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                with mock.patch.object(generate_reports, 'compatibility_view',
                                       side_effect=[Path('/views/sm'), Path('/views/sal')]), \
                        mock.patch.object(generate_reports, 'run') as run:
                    generate_reports.render_dose_calibration(
                        targets, Path('/modules'), Path(directory), Path('/Priors.mat'),
                        {'dosing': {'maximum_treatment_rmt_ratio': value}})
                command = run.call_args[0][0]
                self.assertIn('--disable-treatment-rmt-limit', command)
                self.assertNotIn('--maximum-treatment-rmt-ratio', command)


if __name__ == "__main__":
    unittest.main()
