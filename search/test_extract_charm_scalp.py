import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
from simnibs.mesh_tools import mesh_io

from extract_charm_scalp import extract_scalp, partition_external_scalp, validate_surface


class ScalpExtractionTests(unittest.TestCase):
    def setUp(self):
        nodes = mesh_io.Nodes(np.array([
            [0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.],
            [0., 0., 2.], [1., 0., 2.], [0., 1., 2.]]))
        elements = mesh_io.Elements(
            triangles=np.array([[1, 2, 3], [5, 6, 7]]),
            tetrahedra=np.array([[1, 2, 3, 4]]))
        elements.tag1[:] = [1002, 1005, 5]
        elements.tag2[:] = elements.tag1
        self.mesh = mesh_io.Msh(nodes=nodes, elements=elements)

    def test_filters_are_intersected_not_unioned(self):
        broken = self.mesh.crop_mesh(tags=[1005], elm_type=2)
        self.assertEqual(broken.elm.nr, 2)
        with patch.object(self.mesh, 'partition_skin_surface', return_value=([], [], [], [2])):
            scalp = extract_scalp(self.mesh)
        self.assertEqual(scalp.elm.nr, 1)
        self.assertEqual(scalp.nodes.nr, 3)
        np.testing.assert_array_equal(scalp.elm.tag1, [1005])
        np.testing.assert_array_equal(scalp.nodes.node_coord[:, 2], [2, 2, 2])

    def test_roundtrip_accepts_only_scalp_surface(self):
        with patch.object(self.mesh, 'partition_skin_surface', return_value=([], [], [], [2])):
            scalp = extract_scalp(self.mesh)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'Skin.surf.gii'
            mesh_io.write_gifti_surface(scalp, str(path))
            validate_surface(path, scalp)
            mesh_io.write_gifti_surface(self.mesh.crop_mesh(elm_type=2), str(path))
            with self.assertRaisesRegex(ValueError, 'does not match exterior-only'):
                validate_surface(path, scalp)

    def test_actual_partition_excludes_nested_cavity_with_same_skin_tag(self):
        outer = np.array([[-10., -10., -10.], [30., -10., -10.],
                          [-10., 30., -10.], [-10., -10., 30.]])
        inner = outer * .1
        faces = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])
        normals = np.cross(outer[faces[:, 1]] - outer[faces[:, 0]],
                           outer[faces[:, 2]] - outer[faces[:, 0]])
        inward = np.sum(normals * (outer[faces].mean(axis=1) - outer.mean(axis=0)), axis=1) < 0
        faces[inward] = faces[inward, ::-1]
        nodes = mesh_io.Nodes(np.vstack((outer, inner, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])))
        elements = mesh_io.Elements(
            triangles=np.vstack((faces + 1, faces[:, ::-1] + 5, [[9, 10, 11]])),
            tetrahedra=np.array([[1, 2, 3, 4]]))
        elements.tag1[:] = [1005] * 8 + [1002, 5]
        elements.tag2[:] = elements.tag1
        mesh = mesh_io.Msh(nodes=nodes, elements=elements)
        original_tags = mesh.elm.tag1.copy()
        scalp, metadata = partition_external_scalp(mesh)
        np.testing.assert_allclose(scalp.nodes.node_coord, outer)
        self.assertEqual(scalp.elm.nr, 4)
        self.assertEqual(metadata['excluded_internal_skin_triangles'], 4)
        self.assertEqual(metadata['surface_scope'], 'exterior_skin')
        np.testing.assert_array_equal(mesh.elm.tag1, original_tags)

    def test_surface_only_mesh_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'complete CHARM volume mesh'):
            extract_scalp(self.mesh.crop_mesh(elm_type=2))


if __name__ == '__main__':
    unittest.main()
