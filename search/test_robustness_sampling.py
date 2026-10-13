import unittest
import numpy as np
from scipy.sparse import csr_matrix
from generate_robustness_plan import directional_ring, ring_diagnostics
from evaluate_robust_placements import centers_within_radius


class RobustnessSamplingTests(unittest.TestCase):
    def star(self, radii):
        radii = np.asarray(radii, dtype=float)
        angles = 2 * np.pi * np.arange(len(radii)) / len(radii)
        points = np.vstack(([0., 0., 0.], np.column_stack((
            radii * np.cos(angles), radii * np.sin(angles), np.zeros(len(radii))))))
        leaves = np.arange(1, len(points))
        graph = csr_matrix((np.r_[radii, radii], (np.r_[np.zeros(len(leaves)), leaves],
                           np.r_[leaves, np.zeros(len(leaves))])), shape=(len(points), len(points)))
        graph.indices = graph.indices.astype(np.int32)
        graph.indptr = graph.indptr.astype(np.int32)
        return points, graph, np.tile([0., 0., 1.], (len(points), 1))

    def test_sparse_disk_uses_all_distinct_available_vertices(self):
        p, g, n = self.star([2.5] * 7)
        selected, distance = directional_ring(p, g, n, 0, 3., 8)
        self.assertEqual(len(selected), 7)
        self.assertEqual(len(set(selected)), 7)
        self.assertNotIn(0, selected)
        self.assertTrue(np.all(distance[selected] <= 3.))
        diagnostic = ring_diagnostics(p, n, 0, selected, distance, 3., 8)
        self.assertTrue(diagnostic['mesh_limited'])
        self.assertAlmostEqual(diagnostic['maximum_azimuth_gap_degrees'], 360 / 7)

    def test_insufficient_thin_ring_does_not_expand_outside_radius(self):
        p, g, n = self.star([4.7] * 7 + [3.5] + [5.6] * 3)
        selected, distance = directional_ring(p, g, n, 0, 5., 8)
        self.assertEqual(set(selected), set(range(1, 9)))
        self.assertTrue(np.all(distance[selected] <= 5.))
        self.assertEqual(selected, directional_ring(p, g, n, 0, 5., 8)[0])

    def test_empty_disk_fails_instead_of_using_nominal_or_outside_vertex(self):
        p, g, n = self.star([6.] * 8)
        with self.assertRaisesRegex(ValueError, 'No distinct'):
            directional_ring(p, g, n, 0, 5., 8)

    def test_evaluator_bins_by_actual_displacement(self):
        rows = [{'robust_center_id': 1, 'requested_radius_mm': 5, 'actual_geodesic_radius_mm': 5.6},
                {'robust_center_id': 2, 'requested_radius_mm': 10, 'actual_geodesic_radius_mm': 4.9},
                {'robust_center_id': 3, 'requested_radius_mm': 0, 'actual_geodesic_radius_mm': 0}]
        self.assertEqual(centers_within_radius(rows, 5.), {2, 3})


if __name__ == '__main__':
    unittest.main()
