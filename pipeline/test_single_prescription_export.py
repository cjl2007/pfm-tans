"""Only the configured prescribed pose reaches technician-facing exports."""

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import publish_operator_export as publisher


POLICY = 'configured-selected-pose-v1'
MATRIX = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def best_identity(variant):
    return {'center_id': 2 if variant == 'robust' else 1,
            'orientation_degrees': 10 if variant == 'robust' else 0,
            'x': 1, 'y': 2, 'z': 3, 'scalp_vertex': 10}


def fixture(root, settings, include_stale_robust=False):
    manifest = {'subject': 'Test', 'targets': []}
    variants = {'subject': 'Test', 'validation': {'status': 'passed'},
                'targets': [], 'dose_pairings': []}
    for network, name, enabled in settings:
        target = root / 'targets' / name
        coarse = target / 'CoarseRunConfig.json'
        publisher.write(coarse, {'subject': 'Test', 'network': network,
                                  'robustness': {'enabled': enabled},
                                  'coil_center_constraint': {'enabled': False}})
        record = {'network': network, 'display_name': name, 'variants': {}}
        for variant in ('fine', 'robust') if enabled or include_stale_robust else ('fine',):
            if variant == 'robust':
                plan = target / 'search/robustness/plan/RobustRunConfig.json'
                best = target / 'search/robustness/analysis/BestRobustPose.json'
            else:
                plan = target / 'search/fine/plan/FineRunConfig.json'
                best = target / 'search/fine/simulation/BestCoarsePose.json'
            publisher.write(plan, {})
            publisher.write(best, best_identity(variant))
            source = target / 'work/publishing/neuronavigation' / variant
            source.mkdir(parents=True)
            prefix = f'Test_{name}_{variant.capitalize()}'
            contents = {'Test_T1w.nii.gz': b'identical MRI across targets',
                        f'{prefix}_OptimalTrajectory_Brainsight.txt': f'{name}:{variant}:trajectory'.encode(),
                        f'{prefix}_OptimalPoseOverview.png': f'{name}:{variant}:preview'.encode(),
                        f'{prefix}_OptimalPose.json': b'{}',
                        f'{prefix}_OptimalPose_matsimnibs.txt': b'prescribed matrix'}
            for filename, data in contents.items():
                (source / filename).write_bytes(data)
            publisher.write(source / 'NeuronavigationManifest.json', {
                'subject': 'Test', 'target': name, 'pose_variant': variant,
                'selection_mode': 'robust' if variant == 'robust' else 'nominal_fine',
                'robustness_status': 'evaluated' if enabled else 'not_evaluated_disabled_by_config',
                'sources': {'selected_pose': str(best), 'selected_config': str(plan)},
                'coil_hardware_name': f'{name} coil', 'coil_calibration_id': f'{network}-coil',
                'selected_pose': {'scalp_center_xyz': [1, 2, 3], 'scalp_vertex_one_based': 10,
                                  'simulated_orientation_degrees': best_identity(variant)['orientation_degrees'],
                                  'simulated_matsimnibs': MATRIX, 'prescribed_matsimnibs': MATRIX},
                'files': {path.name: {'sha256': publisher.sha(path), 'bytes': path.stat().st_size}
                          for path in source.iterdir()},
            })
            record['variants'][variant] = {'best_pose': str(best), 'neuronavigation': str(source)}
        comparison = target / 'reports/optimal-pose/index.html'
        comparison.parent.mkdir(parents=True)
        comparison.write_text('Research comparisons remain private and available')
        manifest['targets'].append({'network': network, 'display_name': name,
                                   'target_root': str(target), 'coarse_config': str(coarse)})
        variants['targets'].append(record)
    return manifest, variants


def add_dose_pairs(root, variants, combinations):
    motor = next(target for target in variants['targets'] if target['network'] == 'Somatomotor_Hand')
    treatment = next(target for target in variants['targets'] if target['network'] == 'Salience')
    for motor_variant, treatment_variant in combinations:
        report = root / 'research-dose' / treatment_variant / f'sm-hand-{motor_variant}'
        treatment_mode = 'robust' if treatment_variant == 'robust' else 'nominal_fine'
        lookup = {
            'subject': 'Test', 'calibration': {
                'selection_mode': 'robust' if motor_variant == 'robust' else 'nominal_fine',
                'selected_pose': best_identity(motor_variant),
                'coil_hardware_name': f'{motor["display_name"]} coil', 'id': 'Somatomotor_Hand-coil'},
            'policy': {'maximum_treatment_rmt_ratio': 1.2},
            'treatment_targets': ['SAL'],
            'treatment_sources': {'SAL': {'network': 'Salience', 'selection_mode': treatment_mode,
                                         'selected_pose': best_identity(treatment_variant),
                                         'calibration': {'coil_hardware_name': f'{treatment["display_name"]} coil',
                                                         'id': 'Salience-coil'}}},
            'hotspot_previews': {'SAL': {'provenance': {'simulated_pose_matrix': MATRIX,
                                                        'prescribed_pose_matrix': MATRIX}}},
            'dose_response_curves': {'SAL': {'60': []}},
            'entries': [{'rmt_mso': 60, 'maximum_treatment_mso': 72,
                         'treatments': {'SAL': {'treatment_mso': 60.2,
                                                'pose_selection_mode': treatment_mode}}}],
        }
        publisher.write(report / 'DoseLookup.json', lookup)
        variants['dose_pairings'].append({'calibration_variant': motor_variant,
                                         'treatment_variant': treatment_variant,
                                         'treatment_networks': ['Salience'], 'report': str(report)})


def fake_dose_modules():
    def html_report(path, lookup):
        Path(path).write_text('<!doctype html><meta name="tans-dose-interface" '
                              'content="tans-interactive-dose-v1"><script>const LOOKUP='
                              + json.dumps(lookup) + ';</script>')
    def validate_html(document, lookup):
        embedded, _ = json.JSONDecoder().raw_decode(document.split('const LOOKUP=', 1)[1])
        if embedded != lookup:
            raise ValueError('Embedded lookup mismatch')
    helper = SimpleNamespace(ensure_lookup=Mock(side_effect=copy.deepcopy),
                             validate_lookup=Mock(), validate_html=Mock(side_effect=validate_html))
    report = SimpleNamespace(html_report=Mock(side_effect=html_report),
                             write_tsv=Mock(side_effect=lambda path, lookup: Path(path).write_text('tsv\n')))
    return {'dose_report_defaults': helper, 'generate_calibration_dose_report': report}


class SinglePrescriptionExportTests(unittest.TestCase):
    def build(self, root, manifest, variants, modules=None):
        destination = root / 'export'
        destination.mkdir()
        with patch.dict('sys.modules', modules or fake_dose_modules()):
            result = publisher.build_package(destination, manifest, variants)
            validation = publisher.validate_package(destination)
        self.assertEqual(validation['status'], 'passed')
        self.assertEqual(result['schema_version'], 'tans-operator-export-v2')
        self.assertEqual(result['prescription_policy'], POLICY)
        return destination, result

    def assert_flat_target(self, destination, record, variant):
        self.assertEqual(record['prescribed_variant'], variant)
        self.assertEqual(set(record['variants']), {variant})
        self.assertEqual(record['robustness_enabled'], variant == 'robust')
        target = destination / record['name']
        self.assertFalse((target / 'fine').exists())
        self.assertFalse((target / 'robust').exists())
        trajectories = list(target.rglob('*_OptimalTrajectory_Brainsight.txt'))
        self.assertEqual(len(trajectories), 1)
        self.assertEqual(trajectories[0].parent, target)
        self.assertIn('_Prescribed_', trajectories[0].name)
        self.assertIn('_Prescribed_', Path(record['variants'][variant]['preview']).name)
        self.assertEqual(publisher.sha(trajectories[0]), record['variants'][variant]['source_trajectory_sha256'])

    def test_enabled_robust_is_only_exported_pose_and_research_is_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', True)])
            before = {path: path.read_bytes() for path in (root / 'targets').rglob('*') if path.is_file()}
            index_before = copy.deepcopy(variants)
            destination, result = self.build(root, manifest, variants)
            self.assert_flat_target(destination, result['targets'][0], 'robust')
            self.assertEqual(result['dose_pairings'], [])
            self.assertEqual(variants, index_before)
            self.assertEqual(before, {path: path.read_bytes() for path in (root / 'targets').rglob('*') if path.is_file()})
            for page in destination.rglob('*.html'):
                self.assertNotIn('<select', page.read_text().lower())

    def test_disabled_robustness_uses_fine_even_when_stale_robust_artifacts_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', False)], include_stale_robust=True)
            destination, result = self.build(root, manifest, variants)
            self.assert_flat_target(destination, result['targets'][0], 'fine')
            trajectory = destination / result['targets'][0]['variants']['fine']['trajectory']
            self.assertEqual(trajectory.read_bytes(), b'SAL:fine:trajectory')

    def test_mixed_motor_fine_treatment_robust_selects_exact_pair_without_selector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Somatomotor_Hand', 'SM-Hand', False),
                                                 ('Salience', 'SAL', True)])
            add_dose_pairs(root, variants, [('fine', 'fine'), ('fine', 'robust')])
            original = publisher.read(root / 'research-dose/robust/sm-hand-fine/DoseLookup.json')
            modules = fake_dose_modules()
            destination, result = self.build(root, manifest, variants, modules)
            self.assertEqual(len(result['dose_pairings']), 1)
            pair = result['dose_pairings'][0]
            self.assertEqual((pair['motor_variant'], pair['treatment_variant']), ('fine', 'robust'))
            self.assertEqual(pair['lookup'], 'SAL/dose-calibration/DoseLookup.json')
            self.assertEqual(pair['html'], 'SAL/dose-calibration/DoseCalibrationReport.html')
            lookup = publisher.read(destination / pair['lookup'])
            self.assertEqual(lookup['entries'], original['entries'])
            self.assertEqual(lookup['policy'], original['policy'])
            self.assertEqual(lookup['operator_prescription']['policy'], POLICY)
            self.assertEqual(lookup['operator_prescription']['calibration_variant'], 'fine')
            self.assertEqual(lookup['operator_prescription']['treatment_variant'], 'robust')
            modules['dose_report_defaults'].ensure_lookup.assert_called_once_with(original)
            for record in result['targets']:
                self.assert_flat_target(destination, record, 'fine' if record['network'] == 'Somatomotor_Hand' else 'robust')
            self.assertNotIn('<select', (destination / 'SAL/dose-calibration/index.html').read_text().lower())

    def test_missing_robust_index_entry_never_falls_back_to_fine(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', True)])
            variants['targets'][0]['variants'].pop('robust')
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.build(root, manifest, variants)

    def test_missing_robust_best_pose_never_falls_back_to_fine(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', True)])
            Path(variants['targets'][0]['variants']['robust']['best_pose']).unlink()
            with self.assertRaises(FileNotFoundError):
                self.build(root, manifest, variants)

    def test_wrong_selected_best_pose_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', True)])
            poses = variants['targets'][0]['variants']
            poses['robust']['best_pose'] = poses['fine']['best_pose']
            with self.assertRaises(ValueError):
                self.build(root, manifest, variants)

    def test_invalid_robustness_flag_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', True)])
            config = Path(manifest['targets'][0]['coarse_config'])
            values = publisher.read(config)
            values['robustness']['enabled'] = 'false'
            publisher.write(config, values)
            with self.assertRaises(ValueError):
                self.build(root, manifest, variants)

    def test_missing_selected_dose_pair_does_not_use_other_variant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Somatomotor_Hand', 'SM-Hand', False),
                                                 ('Salience', 'SAL', True)])
            add_dose_pairs(root, variants, [('fine', 'fine')])
            with self.assertRaises(ValueError):
                self.build(root, manifest, variants)

    def test_ambiguous_selected_pair_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Somatomotor_Hand', 'SM-Hand', False),
                                                 ('Salience', 'SAL', True)])
            add_dose_pairs(root, variants, [('fine', 'robust')])
            variants['dose_pairings'].append(copy.deepcopy(variants['dose_pairings'][0]))
            with self.assertRaises(ValueError):
                self.build(root, manifest, variants)

    def test_motor_folder_name_is_taken_from_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Somatomotor_Hand', 'MotorReference', False),
                                                 ('Salience', 'SAL', True)])
            add_dose_pairs(root, variants, [('fine', 'robust')])
            destination, _ = self.build(root, manifest, variants)
            document = (destination / 'SAL/dose-calibration/index.html').read_text()
            self.assertIn('MotorReference/index.html', document)
            self.assertNotIn('SM-Hand/index.html', document)

    def test_wrong_navigation_selection_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', True)])
            path = Path(variants['targets'][0]['variants']['robust']['neuronavigation']) / 'NeuronavigationManifest.json'
            nav = publisher.read(path)
            nav['selection_mode'] = 'nominal_fine'
            publisher.write(path, nav)
            with self.assertRaises(ValueError):
                self.build(root, manifest, variants)

    def test_second_technician_trajectory_is_rejected_even_if_integrity_manifest_is_updated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', True)])
            destination, result = self.build(root, manifest, variants)
            extra = destination / 'SAL/Test_SAL_Fine_OptimalTrajectory_Brainsight.txt'
            extra.write_bytes(b'unprescribed alternative')
            result['files'][extra.relative_to(destination).as_posix()] = {
                'sha256': publisher.sha(extra), 'bytes': extra.stat().st_size}
            publisher.write(destination / 'ExportManifest.json', result)
            with self.assertRaisesRegex(ValueError, 'exactly one technician trajectory'):
                publisher.validate_package(destination)

    def dosing_fixture(self, root):
        manifest, variants = fixture(root, [('Somatomotor_Hand', 'SM-Hand', False),
                                            ('Salience', 'SAL', True)])
        add_dose_pairs(root, variants, [('fine', 'robust')])
        modules = fake_dose_modules()
        destination, result = self.build(root, manifest, variants, modules)
        return destination, result, modules

    def alter_published_lookup(self, destination, manifest, modules, mutate):
        pair = manifest['dose_pairings'][0]
        lookup = publisher.read(destination / pair['lookup'])
        mutate(lookup)
        publisher.write(destination / pair['lookup'], lookup)
        modules['generate_calibration_dose_report'].html_report(destination / pair['html'], lookup)
        for key in ('lookup', 'html'):
            path = destination / pair[key]
            manifest['files'][pair[key]] = {'sha256': publisher.sha(path), 'bytes': path.stat().st_size}
        publisher.write(destination / 'ExportManifest.json', manifest)

    def test_changed_best_pose_at_same_path_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', True)])
            best_path = Path(variants['targets'][0]['variants']['robust']['best_pose'])
            best = publisher.read(best_path)
            best['x'] += 1
            publisher.write(best_path, best)
            with self.assertRaisesRegex(ValueError, 'Configured prescription: pose does not match'):
                self.build(root, manifest, variants)

    def test_cross_wired_same_subject_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Salience', 'SAL', True)])
            path = Path(manifest['targets'][0]['coarse_config'])
            values = publisher.read(path)
            values['network'] = 'Somatomotor_Hand'
            publisher.write(path, values)
            with self.assertRaisesRegex(ValueError, 'another network'):
                self.build(root, manifest, variants)

    def test_same_variant_stale_motor_or_treatment_pose_is_rejected(self):
        for role, field in [('motor', 'x'), ('motor', 'scalp_vertex'),
                            ('treatment', 'z'), ('treatment', 'orientation_degrees')]:
            with self.subTest(role=role, field=field), tempfile.TemporaryDirectory() as temporary:
                destination, manifest, modules = self.dosing_fixture(Path(temporary))
                def mutate(lookup):
                    source = lookup['calibration'] if role == 'motor' else lookup['treatment_sources']['SAL']
                    source['selected_pose'][field] += 1
                self.alter_published_lookup(destination, manifest, modules, mutate)
                with patch.dict('sys.modules', modules), self.assertRaisesRegex(ValueError, 'pose does not match'):
                    publisher.validate_package(destination)

    def test_same_variant_wrong_motor_or_treatment_coil_is_rejected(self):
        for role, field in [('motor', 'coil_hardware_name'), ('motor', 'id'),
                            ('treatment', 'coil_hardware_name'), ('treatment', 'id')]:
            with self.subTest(role=role, field=field), tempfile.TemporaryDirectory() as temporary:
                destination, manifest, modules = self.dosing_fixture(Path(temporary))
                def mutate(lookup):
                    calibration = lookup['calibration'] if role == 'motor' else lookup['treatment_sources']['SAL']['calibration']
                    calibration[field] = 'wrong coil'
                self.alter_published_lookup(destination, manifest, modules, mutate)
                with patch.dict('sys.modules', modules), self.assertRaisesRegex(ValueError, 'coil calibration does not match'):
                    publisher.validate_package(destination)

    def test_preview_pose_matrix_mismatch_is_rejected(self):
        for matrix in ('simulated_pose_matrix', 'prescribed_pose_matrix'):
            with self.subTest(matrix=matrix), tempfile.TemporaryDirectory() as temporary:
                destination, manifest, modules = self.dosing_fixture(Path(temporary))
                self.alter_published_lookup(destination, manifest, modules,
                    lambda lookup: lookup['hotspot_previews']['SAL']['provenance'][matrix][0].__setitem__(3, 5))
                with patch.dict('sys.modules', modules), self.assertRaisesRegex(ValueError, 'Treatment preview pose does not match'):
                    publisher.validate_package(destination)

    def test_handle_flip_uses_simulated_angle_and_matching_prescribed_matrix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, variants = fixture(root, [('Somatomotor_Hand', 'SM-Hand', False),
                                                 ('Salience', 'SAL', True)])
            add_dose_pairs(root, variants, [('fine', 'robust')])
            nav_path = Path(variants['targets'][1]['variants']['robust']['neuronavigation']) / 'NeuronavigationManifest.json'
            nav = publisher.read(nav_path)
            nav['selected_pose']['orientation_degrees'] = 190
            nav['selected_pose']['prescribed_orientation_degrees'] = 190
            flipped = [[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
            nav['selected_pose']['prescribed_matsimnibs'] = flipped
            publisher.write(nav_path, nav)
            lookup_path = Path(variants['dose_pairings'][0]['report']) / 'DoseLookup.json'
            lookup = publisher.read(lookup_path)
            lookup['hotspot_previews']['SAL']['provenance']['prescribed_pose_matrix'] = flipped
            publisher.write(lookup_path, lookup)
            self.build(root, manifest, variants)

    def test_pose_identity_requires_finite_values_exact_vertex_and_small_numeric_tolerance(self):
        best = best_identity('robust')
        nav = {'scalp_center_xyz': [1 + 5e-7, 2, 3], 'scalp_vertex_one_based': 10,
               'simulated_orientation_degrees': 10}
        publisher.validate_pose_identity(best, nav, 'test')
        for mutate in [lambda row: row.update(scalp_vertex_one_based=10.1),
                       lambda row: row.update(simulated_orientation_degrees=float('nan')),
                       lambda row: row.update(scalp_center_xyz=[1, 2, float('inf')])]:
            changed = copy.deepcopy(nav)
            mutate(changed)
            with self.assertRaises(ValueError):
                publisher.validate_pose_identity(best, changed, 'test')

    def test_v1_validation_does_not_apply_new_geometry_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, manifest, modules = self.dosing_fixture(Path(temporary))
            manifest['schema_version'] = 'tans-operator-export-v1'
            publisher.write(destination / 'ExportManifest.json', manifest)
            with patch.dict('sys.modules', modules), \
                    patch.object(publisher, 'validate_pose_identity', side_effect=AssertionError('v2 only')), \
                    patch.object(publisher, 'validate_coil_identity', side_effect=AssertionError('v2 only')), \
                    patch.object(publisher, 'validate_preview_pose', side_effect=AssertionError('v2 only')):
                self.assertEqual(publisher.validate_package(destination)['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
