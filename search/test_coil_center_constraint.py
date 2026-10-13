import json
from pathlib import Path
import tempfile
import unittest

import nibabel as nib
import numpy as np

from coil_center_constraint import (
    CoilCenterConstraint, first_ray_hit, resolve_coil_center_constraint,
    validate_reused_constraint,
)
from run_simulations import score_outputs
from generate_robustness_plan import choose_finalists
from selected_pose import selected_pose_paths


class TestCoilCenterConstraint(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        # Two crowns at z=0 and a deeper target behind the non-target crown.
        self.left = np.array([[0, 0, 0], [2, 0, 0], [0, 2, 0],
                              [4, 0, 0], [6, 0, 0], [4, 2, 0],
                              [0, 0, -2], [2, 0, -2], [0, 2, -2]], np.float32)
        right = np.array([[10, 0, 0], [12, 0, 0], [10, 2, 0]], np.float32)
        surfaces = {}
        for hemi, points in [('left', self.left), ('right', right)]:
            path = self.root / (hemi + '.surf.gii')
            faces = np.arange(len(points), dtype=np.int32).reshape(-1, 3)
            nib.save(nib.gifti.GiftiImage(darrays=[
                nib.gifti.GiftiDataArray(points, intent=1008),
                nib.gifti.GiftiDataArray(faces, intent=1009)]), path)
            surfaces[hemi + '_pial'] = str(path)
        self.axis = (nib.cifti2.BrainModelAxis.from_surface(np.arange(9), 9, 'CortexLeft') +
                     nib.cifti2.BrainModelAxis.from_surface(np.arange(3), 3, 'CortexRight'))
        self.mask = np.array([0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0], float)
        mask_path = self.save_cifti('mask.nii', self.mask)
        self.cfg = {'scoring': {'anatomical_mask': str(mask_path)}, 'surfaces': surfaces}

    def save_cifti(self, name, values):
        path = self.root / name
        data = np.atleast_2d(values)
        header = nib.cifti2.Cifti2Header.from_axes([
            nib.cifti2.ScalarAxis([str(i) for i in range(len(data))]), self.axis])
        nib.save(nib.Cifti2Image(data, header=header), path)
        return path

    @staticmethod
    def pose(x=0.5):
        matrix = np.diag([1.0, -1.0, -1.0, 1.0])
        matrix[:3, 3] = [x, 0.5, 3.0]
        return matrix

    def test_default_on_and_explicit_study_and_target_overrides(self):
        self.assertTrue(resolve_coil_center_constraint({})['enabled'])
        off = {'coil_center_constraint': {'enabled': False}}
        on = {'coil_center_constraint': {'enabled': True}}
        self.assertFalse(resolve_coil_center_constraint(off)['enabled'])
        self.assertFalse(resolve_coil_center_constraint(on, off)['enabled'])
        self.assertTrue(resolve_coil_center_constraint(off, on)['enabled'])
        for value in ['false', 0, None]:
            with self.assertRaises(ValueError):
                resolve_coil_center_constraint({'coil_center_constraint': {'enabled': value}})
        with self.assertRaises(ValueError):
            resolve_coil_center_constraint({'coil_center_constraint': {'enable': False}})

    def test_disabled_requires_no_anatomical_inputs(self):
        gate = CoilCenterConstraint({'coil_center_constraint': {'enabled': False}})
        self.assertTrue(gate.evaluate_saved('/missing', 1, 0)['passes_coil_center_constraint'])
        self.assertEqual(gate.evaluate(None)['coil_axis_hit_status'], 'disabled')

    def test_first_non_target_intersection_cannot_be_skipped_for_deeper_target(self):
        result = CoilCenterConstraint(self.cfg).evaluate(self.pose())
        self.assertFalse(result['passes_coil_center_constraint'])
        self.assertEqual(result['coil_axis_hit_z'], 0)
        self.assertEqual(result['coil_axis_hit_distance_mm'], 3)
        self.assertEqual(result['coil_axis_hit_triangle'], 0)

    def test_entry_into_anatomical_zone_passes(self):
        result = CoilCenterConstraint(self.cfg).evaluate(self.pose(4.5))
        self.assertTrue(result['passes_coil_center_constraint'])
        self.assertEqual(result['coil_axis_hit_mask_value'], 1)
        self.assertEqual(result['coil_axis_hit_hemisphere'], 'L')

    def test_other_hemisphere_and_ray_miss_fail(self):
        gate = CoilCenterConstraint(self.cfg)
        right = gate.evaluate(self.pose(10.5))
        self.assertEqual(right['coil_axis_hit_hemisphere'], 'R')
        self.assertFalse(right['passes_coil_center_constraint'])
        miss = gate.evaluate(self.pose(20))
        self.assertEqual(miss['coil_axis_hit_status'], 'no_pial_intersection')
        self.assertFalse(miss['passes_coil_center_constraint'])

    def test_axis_direction_controls_hit_and_face_winding_does_not(self):
        triangle = self.left[[[3, 4, 5]]]
        for faces in [triangle, triangle[:, ::-1]]:
            hit = first_ray_hit([-1, 0.5, 3], [5.5, 0, -3], faces)
            np.testing.assert_allclose(hit['xyz'], [4.5, 0.5, 0])
            self.assertAlmostEqual(hit['distance_mm'], np.hypot(5.5, 3))
        self.assertIsNone(first_ray_hit([4.5, 0.5, 3], [0, 0, 1], triangle))

    def test_binary_mask_boundary_uses_barycentric_membership(self):
        self.mask[3:6] = [0, 1, 1]
        self.save_cifti('mask.nii', self.mask)
        gate = CoilCenterConstraint(self.cfg)
        self.assertFalse(gate.evaluate(self.pose(4.2))['passes_coil_center_constraint'])
        self.assertTrue(gate.evaluate(self.pose(4.8))['passes_coil_center_constraint'])

    def test_invalid_or_empty_mask_and_missing_geometry_fail(self):
        for values in [np.zeros(12), np.full(12, 0.3), np.full(12, np.nan)]:
            self.save_cifti('mask.nii', values)
            with self.assertRaisesRegex(ValueError, 'binary'):
                CoilCenterConstraint(self.cfg)
        with self.assertRaisesRegex(ValueError, 'anatomical_mask'):
            CoilCenterConstraint({})
        self.save_cifti('mask.nii', self.mask)
        self.cfg['surfaces'].pop('right_pial')
        with self.assertRaisesRegex(ValueError, 'both native pial'):
            CoilCenterConstraint(self.cfg)

    def scoring_fixture(self):
        cfg = {**self.cfg, 'network': 'Test', 'hotspot_percentiles': [50, 99.5],
               'orientations_degrees': [0], 'strategy': 'fine_local_after_coarse_winner',
               'target_map': str(self.save_cifti('target.nii', self.mask)),
               'vertex_area': str(self.save_cifti('area.nii', np.ones(12)))}
        rows = []
        for center, x in [(1, 0.5), (2, 4.5)]:
            folder = self.root / f'Center_{center:05d}'
            folder.mkdir()
            (folder / 'Benchmark.json').write_text(json.dumps({
                'orientations_degrees': [0], 'simulated_pose_matrices': [self.pose(x).tolist()]}))
            field = np.arange(12, dtype=float) * 0.01
            field[3:9] += 1
            if center == 2:
                field[0:3] += 2
            temporary = self.save_cifti(f'field-{center}.nii', field)
            (folder / 'magnE.dtseries.nii').write_bytes(temporary.read_bytes())
            rows.append({'center_id': center, 'scalp_vertex': center,
                         'x': x, 'y': 0.5, 'z': 3})
        return cfg, rows

    def test_ineligible_high_score_cannot_win_and_disable_restores_ranking(self):
        cfg, grid = self.scoring_fixture()
        rows = score_outputs(cfg, grid, self.root)
        self.assertEqual(rows[0]['center_id'], 2)
        self.assertLess(rows[0]['mean_on_target_score'], rows[1]['mean_on_target_score'])
        self.assertFalse(rows[1]['eligible_for_nominal_selection'])
        cfg['coil_center_constraint'] = {'enabled': False}
        rows = score_outputs(cfg, grid, self.root)
        self.assertEqual(rows[0]['center_id'], 1)
        self.assertTrue(all(row['eligible_for_nominal_selection'] for row in rows))

    def test_no_eligible_pose_records_failures_and_raises(self):
        cfg, grid = self.scoring_fixture()
        with self.assertRaisesRegex(RuntimeError, 'No simulated pose passes all'):
            score_outputs(cfg, grid[:1], self.root)
        self.assertTrue((self.root / 'CoarsePoseScores.tsv').is_file())
        record = json.loads((self.root / 'CoilCenterConstraint.json').read_text())
        self.assertEqual(record['poses_eligible_for_selection'], 0)
        self.assertFalse((self.root / 'BestCoarsePose.json').exists())

    def test_robustness_keeps_off_zone_perturbation_scores(self):
        cfg, grid = self.scoring_fixture()
        cfg['strategy'] = 'robustness_perturbation_validation'
        rows = score_outputs(cfg, grid[:1], self.root)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['passes_coil_center_constraint'])
        self.assertFalse(rows[0]['coil_center_constraint_enforced_for_selection'])
        self.assertTrue(rows[0]['eligible_for_nominal_selection'])
        # A diagnostic perturbation maximum may be outside the zone; reusing
        # its simulation cache must not treat it as the final prescription.
        validate_reused_constraint(cfg, self.root, check_winner=False)

    def test_robustness_finalists_exclude_high_scoring_ineligible_pose(self):
        rows = [dict(center_id=1, scalp_vertex=1, orientation_degrees=0,
                     mean_on_target_score=1, passes_target_peak_ratio='True',
                     eligible_for_nominal_selection='False'),
                dict(center_id=2, scalp_vertex=2, orientation_degrees=0,
                     mean_on_target_score=0.8, passes_target_peak_ratio='True',
                     eligible_for_nominal_selection='True')]
        result = choose_finalists(rows, None, None, 0.98, 1, 4)
        self.assertEqual(result, [rows[1]])

    def test_reuse_rejects_unchecked_changed_or_failing_selection(self):
        cfg, grid = self.scoring_fixture()
        with self.assertRaisesRegex(ValueError, 'lacks the current'):
            validate_reused_constraint(cfg, self.root)
        rows = score_outputs(cfg, grid, self.root)
        best_path = self.root / 'BestCoarsePose.json'
        best_path.write_text(json.dumps(rows[0]))
        validate_reused_constraint(cfg, self.root)
        best_path.write_text(json.dumps(rows[1]))
        with self.assertRaisesRegex(ValueError, 'outside'):
            validate_reused_constraint(cfg, self.root)
        best_path.write_text(json.dumps(rows[0]))
        self.mask[0] = 1
        self.save_cifti('mask.nii', self.mask)
        with self.assertRaisesRegex(ValueError, 'lacks the current'):
            validate_reused_constraint(cfg, self.root)

    def test_selected_pose_resolution_requires_checked_geometry(self):
        cfg, grid = self.scoring_fixture()
        rows = score_outputs(cfg, grid, self.root)
        cfg['robustness'] = {'enabled': False}
        coarse = self.root / 'CoarseRunConfig.json'
        coarse.write_text(json.dumps(cfg))
        (self.root / '03_FinePlan').mkdir()
        (self.root / '03_FinePlan/FineRunConfig.json').write_text(json.dumps(cfg))
        (self.root / '04_FineSimulation').symlink_to(self.root, target_is_directory=True)
        best_path = self.root / 'BestCoarsePose.json'
        best_path.write_text(json.dumps(rows[0]))
        selected_pose_paths(self.root)
        best_path.write_text(json.dumps(rows[1]))
        with self.assertRaisesRegex(ValueError, 'outside'):
            selected_pose_paths(self.root)
        (self.root / 'CoilCenterConstraint.json').unlink()
        with self.assertRaisesRegex(ValueError, 'lacks the current'):
            selected_pose_paths(self.root)


if __name__ == '__main__':
    unittest.main()
