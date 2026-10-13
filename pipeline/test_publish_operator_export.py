import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from publication_paths import operator_export, existing_operator_export
import publish_operator_export as publisher

from publish_operator_export import (build_package, filter_lookup, local_name, migrate_sources,
                                     sha, validate_package, write)


class OperatorExportTests(unittest.TestCase):
    def test_export_name_uses_manifest_subject_for_nested_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'tans/runs/custom_network_run'
            write(root / 'MultinetworkRunManifest.json', {'subject': 'BioSelect-23005'})
            self.assertEqual(operator_export(root), root / 'shared/export_BioSelect-23005')
            self.assertEqual(existing_operator_export(root), operator_export(root))
            legacy = root / 'shared/export'
            legacy.mkdir(parents=True)
            self.assertEqual(existing_operator_export(root), legacy)
            operator_export(root).mkdir()
            self.assertEqual(existing_operator_export(root), operator_export(root))

    def test_invalid_subject_cannot_escape_export_directory(self):
        for subject in ('', '.', '..', '../other', 'nested/name', 'nested\\name',
                        'name\n', 'name\r', 'name\x00', None, 123):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write(root / 'MultinetworkRunManifest.json', {'subject': subject})
                with self.subTest(subject=subject), self.assertRaises(ValueError):
                    operator_export(root)

    def test_publish_names_package_and_archives_legacy_then_previous_named_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = self.fixture(root)
            variants.update(subject=manifest['subject'], validation={'status': 'passed'})
            write(root / 'MultinetworkRunManifest.json', manifest)
            write(root / 'reports/PoseVariants.json', variants)
            legacy = root / 'shared/export'
            legacy.mkdir(parents=True)
            build_package(legacy, manifest, variants)
            original_hash = sha(legacy / 'Test_T1w.nii.gz')
            with patch.object(publisher, 'research_links') as links:
                result = publisher.publish(root, migrate=False)
                destination = root / 'shared/export_Test'
                self.assertEqual(result['export'], str(destination))
                self.assertEqual(result['validation']['relocated_copy'], 'passed')
                self.assertFalse(legacy.exists())
                links.assert_called_once_with(root, destination)
                publisher.publish(root, migrate=False)
            self.assertEqual(validate_package(destination)['status'], 'passed')
            archived = list((root / 'shared/work/publishing/previous-exports').iterdir())
            self.assertEqual(len(archived), 2)
            for package in archived:
                self.assertEqual(sha(package / 'Test_T1w.nii.gz'), original_hash)

    def test_target_filter_keeps_scores_policy_and_reference_unchanged(self):
        lookup = {'subject': 'Test', 'calibration': {'selection_mode': 'nominal_fine'},
                  'policy': {'maximum_treatment_rmt_ratio': None},
                  'treatment_targets': ['SAL', 'AMN'],
                  'treatment_sources': {'SAL': {'network': 'Salience'}, 'AMN': {'network': 'CinguloOpercular'}},
                  'dose_response_curves': {'SAL': {'45': [1, 2]}, 'AMN': {'45': [3, 4]}},
                  'entries': [{'rmt_mso': 45, 'maximum_treatment_mso': None,
                               'treatments': {'SAL': {'treatment_mso': 41.5, 'score': .7}, 'AMN': None}}]}
        before = copy.deepcopy(lookup)
        result, label = filter_lookup(lookup, 'Salience')
        self.assertEqual(lookup, before)
        self.assertEqual(label, 'SAL')
        self.assertEqual(result['policy'], lookup['policy'])
        self.assertEqual(result['calibration'], lookup['calibration'])
        self.assertEqual(result['entries'][0]['treatments'], {'SAL': lookup['entries'][0]['treatments']['SAL']})
        self.assertEqual(result['treatment_targets'], ['SAL'])
        self.assertEqual(set(result['dose_response_curves']), {'SAL'})
        result, _ = filter_lookup(lookup, 'CinguloOpercular')
        self.assertIsNone(result['entries'][0]['treatments']['AMN'])
        with self.assertRaises(ValueError):
            filter_lookup(lookup, 'Missing')

    def test_safe_path_components(self):
        for value in ('../escape', '/tmp/wrong', '..', '', 'nested/name', 'nested\\name'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_name(value)

    def fixture(self, root):
        source = root / 'source'
        source.mkdir()
        target = root / 'targets/SAL'
        coarse = target / 'CoarseRunConfig.json'
        plan = target / 'search/fine/plan/FineRunConfig.json'
        best = target / 'search/fine/simulation/BestCoarsePose.json'
        write(coarse, {'subject': 'Test', 'network': 'Salience', 'robustness': {'enabled': False},
                       'coil_center_constraint': {'enabled': False}})
        write(plan, {})
        write(best, {'center_id': 1, 'orientation_degrees': 0,
                     'x': 1, 'y': 2, 'z': 3, 'scalp_vertex': 10})
        for name, content in [('Test_T1w.nii.gz', b'mri'),
                              ('Test_Salience_Fine_OptimalTrajectory_Brainsight.txt', b'pose'),
                              ('Test_Salience_Fine_OptimalPoseOverview.png', b'png'),
                              ('Test_Salience_Fine_OptimalPose.json', b'{}'),
                              ('Test_Salience_Fine_OptimalPose_matsimnibs.txt', b'matrix')]:
            (source / name).write_bytes(content)
        nav = {'subject': 'Test', 'pose_variant': 'fine', 'selection_mode': 'nominal_fine',
               'robustness_status': 'not_evaluated_disabled_by_config',
               'sources': {'selected_pose': str(best), 'selected_config': str(plan)},
               'coil_hardware_name': 'Test coil', 'coil_calibration_id': 'test-coil',
               'selected_pose': {'scalp_center_xyz': [1, 2, 3], 'scalp_vertex_one_based': 10,
                                 'simulated_orientation_degrees': 0},
               'files': {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size} for p in source.iterdir()}}
        write(source / 'NeuronavigationManifest.json', nav)
        manifest = {'subject': 'Test', 'targets': [{'network': 'Salience', 'target_root': str(target),
                                                   'coarse_config': str(coarse)}]}
        variants = {'targets': [{'network': 'Salience', 'display_name': 'SAL',
                                'variants': {'fine': {'neuronavigation': str(source), 'best_pose': str(best)}}}], 'dose_pairings': []}
        return manifest, variants

    def test_pose_only_build_is_closed_and_survives_move(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, variants = self.fixture(root)
            destination = root / 'export'
            destination.mkdir()
            result = build_package(destination, manifest, variants)
            self.assertEqual(result['dose_pairings'], [])
            self.assertTrue((destination/'Test_T1w.nii.gz').is_file())
            self.assertFalse((destination/'SAL/robust').exists())
            self.assertEqual(list(destination.rglob('*T1w*')), [destination/'Test_T1w.nii.gz'])
            self.assertIn('Pose-only export', (destination/'SAL/index.html').read_text())
            self.assertFalse((destination/'SAL/fine').exists())
            self.assertEqual(validate_package(destination)['status'], 'passed')
            moved = root / 'somewhere-unrelated/moved'
            shutil.copytree(destination, moved)
            shutil.rmtree(destination)
            shutil.rmtree(root / 'source')
            shutil.rmtree(root / 'targets')
            self.assertEqual(validate_package(moved)['status'], 'passed')

    def test_integrity_and_external_resource_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, variants = self.fixture(root)
            destination = root / 'export'
            destination.mkdir()
            build_package(destination, manifest, variants)
            index = destination / 'index.html'
            index.write_text(index.read_text() + '<img src="../../outside.png">')
            metadata = json.loads((destination/'ExportManifest.json').read_text())
            metadata['files']['index.html'] = {'sha256': sha(index), 'bytes': index.stat().st_size}
            write(destination/'ExportManifest.json', metadata)
            with self.assertRaisesRegex(ValueError, 'Broken or external'):
                validate_package(destination)

    def test_source_checksum_change_fails_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, variants = self.fixture(root)
            (root/'source/Test_T1w.nii.gz').write_text('changed')
            destination = root / 'export'
            destination.mkdir()
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                build_package(destination, manifest, variants)

    def test_published_trajectory_must_match_original_not_just_package_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, variants = self.fixture(root)
            destination = root / 'export'
            destination.mkdir()
            metadata = build_package(destination, manifest, variants)
            key = metadata['targets'][0]['variants']['fine']['trajectory']
            (destination / key).write_bytes(b'changed pose')
            metadata['files'][key]['sha256'] = sha(destination / key)
            write(destination / 'ExportManifest.json', metadata)
            with self.assertRaisesRegex(ValueError, 'differs from original'):
                validate_package(destination)

    def test_migration_preserves_sources_and_rewrites_structured_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root/'targets/SAL'
            old_nav = target/'export/neuronavigation'
            old_dose = root/'reports/dose-calibration'
            write(old_nav/'NeuronavigationManifest.json', {'source': 'kept'})
            write(old_dose/'DoseLookup.json', {'data': 'kept'})
            write(root/'MultinetworkRunManifest.json', {'targets': [{'target_root': str(target)}]})
            write(root/'ReportIndex.json', {'targets': [{'neuronavigation': str(old_nav)}], 'dose_calibration': str(old_dose)})
            migrate_sources(root)
            index = json.loads((root/'ReportIndex.json').read_text())
            self.assertEqual(index['targets'][0]['neuronavigation'], str(target/'work/publishing/neuronavigation'))
            self.assertEqual(json.loads((Path(index['dose_calibration'])/'DoseLookup.json').read_text()), {'data': 'kept'})
            self.assertFalse(old_nav.exists())
            migrate_sources(root)


if __name__ == '__main__':
    unittest.main()
