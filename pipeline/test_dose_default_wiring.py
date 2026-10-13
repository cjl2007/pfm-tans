"""Pipeline integration tests with source-free upgrade/publishing stand-ins."""

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import generate_pose_variants as variants_module
import generate_reports as reports_module
import publish_operator_export as publisher


VERSION = 'tans-interactive-dose-v1'
POSE = {'center_id': 1, 'orientation_degrees': 0, 'x': 1, 'y': 2, 'z': 3, 'scalp_vertex': 10}
MATRIX = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def helper_stub():
    return SimpleNamespace(ensure_report=Mock(), ensure_lookup=Mock(),
                           validate_lookup=Mock(), validate_html=Mock())


class DoseDefaultWiringTests(unittest.TestCase):
    def targets(self, root):
        return [{'network': 'Somatomotor_Hand', 'target_root': str(root / 'targets/SM-Hand')},
                {'network': 'Salience', 'target_root': str(root / 'targets/SAL')}]

    def test_existing_complete_selected_report_is_upgraded_without_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = helper_stub()
            with patch.dict('sys.modules', {'dose_report_defaults': helper}), \
                 patch.object(reports_module, 'compatibility_view', return_value=root / 'view'), \
                 patch.object(reports_module, 'complete', return_value=True), \
                 patch.object(reports_module, 'run') as run, \
                 patch('publication_paths.dose_source', return_value=root / 'report'):
                result = reports_module.render_dose_calibration(
                    self.targets(root), root / 'module', root, root / 'priors', {})
            self.assertEqual(result, root / 'report')
            helper.ensure_report.assert_called_once_with(root / 'report', priors=root / 'priors')
            run.assert_not_called()

    def test_fresh_selected_report_uses_generator_and_incomplete_report_still_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = helper_stub()
            with patch.dict('sys.modules', {'dose_report_defaults': helper}), \
                 patch.object(reports_module, 'compatibility_view', return_value=root / 'view'), \
                 patch.object(reports_module, 'complete', return_value=False), \
                 patch.object(reports_module, 'run') as run, \
                 patch('publication_paths.dose_source', return_value=root / 'report'):
                reports_module.render_dose_calibration(
                    self.targets(root), root / 'module', root, root / 'priors', {})
                self.assertEqual(run.call_count, 1)
                self.assertIn(str(root / 'module/generate_calibration_dose_report.py'), run.call_args.args[0])
                (root / 'report').mkdir()
                with self.assertRaises(FileExistsError):
                    reports_module.render_dose_calibration(
                        self.targets(root), root / 'module', root, root / 'priors', {})
                self.assertEqual(run.call_count, 1)
            helper.ensure_report.assert_not_called()

    def test_failed_selected_upgrade_does_not_fall_back_to_regeneration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = helper_stub()
            helper.ensure_report.side_effect = FileNotFoundError('Recorded E-field unavailable')
            with patch.dict('sys.modules', {'dose_report_defaults': helper}), \
                 patch.object(reports_module, 'compatibility_view', return_value=root / 'view'), \
                 patch.object(reports_module, 'complete', return_value=True), \
                 patch.object(reports_module, 'run') as run, \
                 patch('publication_paths.dose_source', return_value=root / 'report'):
                with self.assertRaisesRegex(FileNotFoundError, 'Recorded E-field'):
                    reports_module.render_dose_calibration(
                        self.targets(root), root / 'module', root, root / 'priors', {})
            run.assert_not_called()

    def test_pairing_and_legacy_report_both_use_shared_upgrade(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / 'report'
            report.mkdir()
            (report / 'DoseLookup.json').write_text('{}')
            helper = helper_stub()
            with patch.dict('sys.modules', {'dose_report_defaults': helper}), \
                 patch.object(reports_module, 'compatibility_view', return_value=root / 'view'), \
                 patch.object(reports_module, 'complete', return_value=True), \
                 patch.object(reports_module, 'run') as run, \
                 patch.object(variants_module, 'available', return_value=['fine']), \
                 patch.object(variants_module, 'write_dose_navigation'), \
                 patch('publication_paths.dose_source', return_value=report):
                pairs = variants_module.render_dose_variants(
                    self.targets(root), root / 'module', root, root / 'priors', {'subject': 'Test'})
            self.assertEqual(len(pairs), 1)
            self.assertEqual(helper.ensure_report.call_args_list,
                             [call(report / 'fine/sm-hand-fine', priors=root / 'priors'),
                              call(report, priors=root / 'priors')])
            run.assert_not_called()

    def test_pose_only_pipeline_does_not_require_dose_upgrade(self):
        helper = helper_stub()
        with patch.dict('sys.modules', {'dose_report_defaults': helper}):
            self.assertIsNone(reports_module.render_dose_calibration(
                [{'network': 'Salience'}], Path('/unused'), Path('/unused'), Path('/unused'), {}))
            self.assertEqual(variants_module.render_dose_variants(
                [{'network': 'Salience'}], Path('/unused'), Path('/unused'), Path('/unused'), {}), [])
        helper.ensure_report.assert_not_called()


class DoseDefaultPublicationTests(unittest.TestCase):
    def fixture(self, root):
        manifest = {'subject': 'Test', 'targets': []}
        variants = {'subject': 'Test', 'validation': {'status': 'passed'},
                    'targets': [], 'dose_pairings': []}
        for network, name in [('Somatomotor_Hand', 'SM-Hand'), ('Salience', 'SAL')]:
            source = root / 'source' / name
            source.mkdir(parents=True)
            target = root / 'targets' / name
            coarse = target / 'CoarseRunConfig.json'
            plan = target / 'search/fine/plan/FineRunConfig.json'
            best = target / 'search/fine/simulation/BestCoarsePose.json'
            publisher.write(coarse, {'subject': 'Test', 'network': network,
                                      'coil_center_constraint': {'enabled': False},
                                      'robustness': {'enabled': False}})
            publisher.write(plan, {})
            publisher.write(best, POSE)
            contents = {
                'Test_T1w.nii.gz': b'mri',
                f'Test_{name}_Fine_OptimalTrajectory_Brainsight.txt': b'pose',
                f'Test_{name}_Fine_OptimalPoseOverview.png': b'png',
                f'Test_{name}_Fine_OptimalPose.json': b'{}',
                f'Test_{name}_Fine_OptimalPose_matsimnibs.txt': b'matrix',
            }
            for filename, data in contents.items():
                (source / filename).write_bytes(data)
            publisher.write(source / 'NeuronavigationManifest.json', {
                'subject': 'Test', 'pose_variant': 'fine', 'selection_mode': 'nominal_fine',
                'robustness_status': 'not_evaluated_disabled_by_config',
                'sources': {'selected_pose': str(best), 'selected_config': str(plan)},
                'coil_hardware_name': 'Test coil', 'coil_calibration_id': 'test-coil',
                'selected_pose': {'scalp_center_xyz': [1, 2, 3], 'scalp_vertex_one_based': 10,
                                  'simulated_orientation_degrees': 0,
                                  'simulated_matsimnibs': MATRIX, 'prescribed_matsimnibs': MATRIX},
                'files': {path.name: {'sha256': publisher.sha(path), 'bytes': path.stat().st_size}
                          for path in source.iterdir()},
            })
            manifest['targets'].append({'network': network, 'target_root': str(target),
                                         'coarse_config': str(coarse)})
            variants['targets'].append({'network': network, 'display_name': name,
                                         'variants': {'fine': {'neuronavigation': str(source),
                                                               'best_pose': str(best)}}})
        lookup = {
            'subject': 'Test', 'calibration': {'selection_mode': 'nominal_fine', 'selected_pose': POSE,
                                             'coil_hardware_name': 'Test coil', 'id': 'test-coil'},
            'policy': {'maximum_treatment_rmt_ratio': 1.2}, 'treatment_targets': ['SAL'],
            'treatment_sources': {'SAL': {'network': 'Salience', 'selection_mode': 'nominal_fine',
                                         'selected_pose': POSE,
                                         'calibration': {'coil_hardware_name': 'Test coil', 'id': 'test-coil'}}},
            'dose_response_curves': {'SAL': {'45': []}},
            'entries': [{'rmt_mso': 45, 'maximum_treatment_mso': 54,
                         'treatments': {'SAL': {'treatment_mso': 41.5}}}],
        }
        publisher.write(root / 'dose/DoseLookup.json', lookup)
        variants['dose_pairings'].append({'treatment_networks': ['Salience'],
                                         'treatment_variant': 'fine', 'calibration_variant': 'fine',
                                         'report': str(root / 'dose')})
        return manifest, variants, lookup

    def fake_modules(self):
        helper = helper_stub()
        def upgrade(lookup, priors=None):
            result = copy.deepcopy(lookup)
            result['dose_report_interface'] = VERSION
            result['hotspot_previews'] = {'SAL': {'preview': 'embedded', 'provenance': {
                'simulated_pose_matrix': MATRIX, 'prescribed_pose_matrix': MATRIX}}}
            result['treatment_sources']['SAL']['preview_metrics'] = {'cdf': 'embedded'}
            for entry in result['entries']:
                entry['treatment_landmarks'] = {'SAL': {'first_cortex_mso': 30}}
            return result
        def validate(lookup):
            if lookup.get('dose_report_interface') != VERSION:
                raise ValueError('Missing current dose interface')
        def validate_html(document, lookup):
            if f'<meta name="tans-dose-interface" content="{VERSION}">' not in document:
                raise ValueError('Missing current HTML marker')
            marker = '<script>const LOOKUP='
            embedded, _ = json.JSONDecoder().raw_decode(document.split(marker, 1)[1])
            if embedded != lookup:
                raise ValueError('HTML payload mismatch')
        helper.ensure_lookup.side_effect = upgrade
        helper.validate_lookup.side_effect = validate
        helper.validate_html.side_effect = validate_html
        def html_report(path, lookup):
            Path(path).write_text(f'<!doctype html><meta name="tans-dose-interface" content="{VERSION}">'
                                  '<script>const LOOKUP=' + json.dumps(lookup) + ';</script>')
        report = SimpleNamespace(html_report=Mock(side_effect=html_report),
                                 write_tsv=Mock(side_effect=lambda path, lookup: Path(path).write_text('test\n')))
        return helper, report

    def test_publication_upgrades_before_filtering_without_changing_source_or_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants, original = self.fixture(root)
            source_bytes = (root / 'dose/DoseLookup.json').read_bytes()
            helper, report = self.fake_modules()
            destination = root / 'export'
            destination.mkdir()
            with patch.dict('sys.modules', {'dose_report_defaults': helper,
                                           'generate_calibration_dose_report': report}):
                output = publisher.build_package(destination, manifest, variants)
                self.assertEqual(publisher.validate_package(destination)['status'], 'passed')
            helper.ensure_lookup.assert_called_once_with(original)
            helper.validate_lookup.assert_called_once()
            helper.validate_html.assert_called_once()
            published = publisher.read(destination / output['dose_pairings'][0]['lookup'])
            self.assertEqual(published['dose_report_interface'], VERSION)
            self.assertEqual(published['entries'][0]['treatments'], original['entries'][0]['treatments'])
            self.assertEqual(published['policy'], original['policy'])
            self.assertEqual(published['hotspot_previews']['SAL']['preview'], 'embedded')
            self.assertEqual((root / 'dose/DoseLookup.json').read_bytes(), source_bytes)

    def test_validation_rejects_legacy_payload_even_when_package_hashes_agree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants, _ = self.fixture(root)
            helper, report = self.fake_modules()
            destination = root / 'export'
            destination.mkdir()
            with patch.dict('sys.modules', {'dose_report_defaults': helper,
                                           'generate_calibration_dose_report': report}):
                output = publisher.build_package(destination, manifest, variants)
                key = output['dose_pairings'][0]['lookup']
                lookup = publisher.read(destination / key)
                lookup.pop('dose_report_interface')
                publisher.write(destination / key, lookup)
                output['files'][key] = {'sha256': publisher.sha(destination / key),
                                        'bytes': (destination / key).stat().st_size}
                publisher.write(destination / 'ExportManifest.json', output)
                with self.assertRaisesRegex(ValueError, 'Missing current dose interface'):
                    publisher.validate_package(destination)

    def test_validation_rejects_old_html_interface_even_when_payload_agrees(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants, _ = self.fixture(root)
            helper, report = self.fake_modules()
            destination = root / 'export'
            destination.mkdir()
            with patch.dict('sys.modules', {'dose_report_defaults': helper,
                                           'generate_calibration_dose_report': report}):
                output = publisher.build_package(destination, manifest, variants)
                key = output['dose_pairings'][0]['html']
                path = destination / key
                path.write_text(path.read_text().replace(f'<meta name="tans-dose-interface" content="{VERSION}">', ''))
                output['files'][key] = {'sha256': publisher.sha(path), 'bytes': path.stat().st_size}
                publisher.write(destination / 'ExportManifest.json', output)
                with self.assertRaisesRegex(ValueError, 'Missing current HTML marker'):
                    publisher.validate_package(destination)

    def test_failed_upgrade_does_not_replace_existing_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants, _ = self.fixture(root)
            publisher.write(root / 'MultinetworkRunManifest.json', manifest)
            publisher.write(root / 'reports/PoseVariants.json', variants)
            existing = root / 'shared/export'
            existing.mkdir(parents=True)
            (existing / 'keep.txt').write_bytes(b'previous validated export')
            helper, report = self.fake_modules()
            helper.ensure_lookup.side_effect = FileNotFoundError('Recorded field missing')
            with patch.dict('sys.modules', {'dose_report_defaults': helper,
                                           'generate_calibration_dose_report': report}):
                with self.assertRaisesRegex(FileNotFoundError, 'Recorded field missing'):
                    publisher.publish(root, migrate=False)
            self.assertEqual((existing / 'keep.txt').read_bytes(), b'previous validated export')
            self.assertEqual(list(existing.parent.glob('.operator-export-building-*')), [])


if __name__ == '__main__':
    unittest.main()
