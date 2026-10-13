import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("generate_master_report.py")
SPEC = importlib.util.spec_from_file_location("master_report", MODULE_PATH)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class TestMasterReport(unittest.TestCase):
    def test_document_summarizes_targets_counts_runtime_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config" / "resolved.yaml"
            config.parent.mkdir()
            config.write_text("subject: TEST\n")
            target = root / "targets" / "Salience"
            target.mkdir(parents=True)
            (target / "RuntimeSummary.json").write_text(json.dumps({
                "coarse_poses": 12, "fine_poses": 5, "robustness_poses": 7,
                "baseline_coarse_plus_fine_wall_seconds": 70,
                "robustness_total_wall_seconds": 30,
                "coarse_wall_seconds": 50, "fine_wall_seconds": 20,
                "robustness_planning_wall_seconds": 1,
                "robustness_simulation_wall_seconds": 28,
                "robustness_analysis_wall_seconds": 1,
            }))
            pose = target / "BestRobustPose.json"
            pose.write_text(json.dumps({
                "fine_center_id": 3, "orientation_degrees": 45,
                "robustness": {"position_error_mm": 5,
                               "orientation_error_degrees": 5},
            }))
            optimal = target / "reports" / "optimal-pose"
            optimal.mkdir(parents=True)
            (optimal / "OptimalPoseOverview.png").write_bytes(b"png")
            manifest = {
                "subject": "TEST", "configuration": str(config),
                "map_mode": "weighted", "targets": [{
                    "network": "Salience", "display_name": "Salience",
                    "target_root": str(target), "map_mode": "weighted",
                    "anatomical_zone": "LPFC", "coverage_fraction": 0.9,
                }],
            }
            index = {"targets": [{
                "network": "Salience", "coil_hardware_name": "Cool-B65 A/P",
                "selected_pose": str(pose), "optimal_pose": str(optimal),
                "target_selection": str(target / "missing"),
                "search_replay": str(target / "missing"),
                "neuronavigation": str(target / "missing"),
            }], "dose_calibration": None}
            output = root / "reports" / "index.html"
            document = REPORT.build_document(manifest, index, output)
        self.assertIn("TEST", document)
        self.assertIn("24", document)
        self.assertIn("1 min 40 s", document)
        self.assertIn("Cool-B65 A/P", document)
        self.assertIn("OptimalPoseOverview.png", document)
        self.assertIn("Not recorded", document)

    def test_duration_format(self):
        self.assertEqual(REPORT.duration(None), "Not recorded")
        self.assertEqual(REPORT.duration(3661), "1 h 01 min")

    def test_single_prescribed_package_links_and_research_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'targets/Salience'
            comparison = target / 'reports/optimal-pose/index.html'
            comparison.parent.mkdir(parents=True)
            comparison.write_text('research comparison')
            source = target / 'work/publishing/neuronavigation'
            source.mkdir(parents=True)
            (source / 'TEST_Salience_OptimalTrajectory_Brainsight.txt').write_text('old selected')
            package = root / 'shared/export_TEST/Salience'
            (package / 'metadata').mkdir(parents=True)
            (package / 'dose-calibration').mkdir()
            (package / 'index.html').write_text('prescribed export')
            (package / 'dose-calibration/index.html').write_text('one matched lookup')
            trajectory = package / 'TEST_Salience_Prescribed_OptimalTrajectory_Brainsight.txt'
            trajectory.write_text('validated prescribed pose')
            (package.parent / 'TEST_T1w.nii.gz').write_bytes(b'MRI')
            (package / 'metadata/NeuronavigationManifest.json').write_text(json.dumps({
                'selected_pose': {'prescribed_orientation_degrees': 190}}))
            # Neither stale technician directories nor research source variants
            # should become alternate trajectory links in the master report.
            for variant in ('fine', 'robust'):
                stale = package / variant
                stale.mkdir()
                (stale / f'TEST_{variant}_OptimalTrajectory_Brainsight.txt').write_text('stale')
            item = {'network': 'Salience', 'display_name': 'Salience',
                    'target_root': str(target)}
            report = {'selected_pose': str(target / 'missing.json'),
                      'target_selection': str(target / 'missing'),
                      'optimal_pose': str(comparison.parent),
                      'search_replay': str(target / 'missing'),
                      'neuronavigation': str(source), 'operator_export': str(package)}
            record = REPORT.target_record(item, report, root / 'reports/index.html')
            self.assertIn('Research fine / robust comparison', record['links'])
            self.assertEqual(record['links']['Prescribed Brainsight trajectory'],
                             '../shared/export_TEST/Salience/' + trajectory.name)
            self.assertEqual(record['links']['Prescribed-pose dosing'],
                             '../shared/export_TEST/Salience/dose-calibration/index.html')
            self.assertEqual(record['links']['Navigation T1w'], '../shared/export_TEST/TEST_T1w.nii.gz')
            self.assertNotIn('Brainsight trajectory', record['links'])
            self.assertFalse(any('/fine/' in value or '/robust/' in value
                                 for value in record['links'].values()))
            self.assertEqual(record['prescription']['prescribed_orientation_degrees'], 190)
            without_explicit_export = dict(report)
            without_explicit_export.pop('operator_export')
            inferred = REPORT.target_record(item, without_explicit_export,
                                           root / 'reports/index.html', subject='TEST')
            self.assertEqual(inferred['links'], record['links'])

    def test_duplicate_prescribed_trajectories_are_not_silently_chosen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'targets/Salience'
            package = root / 'shared/export/Salience'
            package.mkdir(parents=True)
            (package / 'index.html').write_text('invalid duplicate package')
            for subject in ('TEST', 'OTHER'):
                (package / f'{subject}_Salience_Prescribed_OptimalTrajectory_Brainsight.txt').write_text('pose')
            report = {key: str(target / 'missing') for key in (
                'selected_pose', 'target_selection', 'optimal_pose', 'search_replay', 'neuronavigation')}
            with self.assertRaisesRegex(ValueError, 'Multiple prescribed trajectories'):
                REPORT.target_record({'target_root': str(target)}, report, root / 'reports/index.html')

    def test_operator_callout_has_one_planned_reference_not_pose_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config/resolved.yaml'
            config.parent.mkdir()
            config.write_text('subject: TEST\n')
            dose = root / 'shared/export/Salience/dose-calibration'
            dose.mkdir(parents=True)
            (dose / 'index.html').write_text('single prescribed dose')
            document = REPORT.build_document(
                {'subject': 'TEST', 'configuration': str(config), 'targets': []},
                {'targets': [], 'dose_calibration': str(dose)}, root / 'reports/index.html')
            self.assertIn('One planned motor reference and one treatment prescription are preselected', document)
            self.assertNotIn('Select the SM-Hand pose used for rMT and the treatment pose', document)


if __name__ == "__main__":
    unittest.main()
