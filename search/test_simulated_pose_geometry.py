import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
from simnibs.mesh_tools import mesh_io
from simulated_pose_geometry import fit_pose_matrices, saved_pose_matrix


class PoseGeometryTests(unittest.TestCase):
    def test_saved_matrix_roundtrip_and_missing_geometry_rejection(self):
        benchmark = {'orientations_degrees': [0, 15],
                     'simulated_pose_matrices': [np.eye(4).tolist()] * 2}
        np.testing.assert_array_equal(saved_pose_matrix(benchmark, 15), np.eye(4))
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            saved_pose_matrix(benchmark, 30)
        with self.assertRaisesRegex(ValueError, 'missing'):
            saved_pose_matrix({'orientations_degrees': [15]}, 15)

    def test_internal_center_is_rejected_before_simulation(self):
        nodes = mesh_io.Nodes(np.array([
            [0., 0., 2.], [1., 0., 2.], [0., 1., 2.]]))
        elements = mesh_io.Elements(triangles=np.array([[1, 2, 3]]))
        elements.tag1[:] = 1005
        elements.tag2[:] = 1005
        mesh = mesh_io.Msh(nodes=nodes, elements=elements)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'head.msh'
            mesh_io.write_msh(mesh, str(path))
            with patch('simulated_pose_geometry.extract_scalp', return_value=mesh) as extract:
                with self.assertRaisesRegex(ValueError, 'exterior skin'):
                    fit_pose_matrices(path, [0., 0., 0.], [[1., 0., 0.]], 1.)
                matrices, validation = fit_pose_matrices(
                    path, [0., 0., 2.], [[1., 0., 2.]], 1.)
                self.assertEqual(extract.call_count, 2)
            expected = mesh.calc_matsimnibs(
                np.array([0., 0., 2.]), np.array([1., 0., 2.]), 1.)
            np.testing.assert_allclose(matrices[0], expected, atol=1e-10)
            self.assertEqual(validation['requested_center_to_scalp_mm'], 0.)
            self.assertEqual(validation['surface_scope'], 'exterior_skin')


if __name__ == '__main__':
    unittest.main()
