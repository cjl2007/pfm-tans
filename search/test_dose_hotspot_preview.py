import base64
import copy
import unittest
from unittest.mock import patch

import nibabel as nib
import numpy as np

import dose_hotspot_preview as preview


class DoseHotspotPreviewTests(unittest.TestCase):
    def raster(self, reverse=False, missing_front=False):
        # Identical projected triangles: a hot red back face and cool blue front.
        points = np.array([[0, 0, 1], [4, 0, 1], [0, 4, 1],
                           [0, 0, 2], [4, 0, 2], [0, 4, 2]], dtype=float)
        faces = np.array([[0, 1, 2], [3, 4, 5]])
        fields = np.array([100, 100, 100, 2, 2, 2], dtype=float)
        if missing_front:
            fields[3:] = np.nan
        return preview.rasterize_cortex(
            points, faces[::-1] if reverse else faces, fields,
            np.array([1, 1, 1, 2, 2, 2]), np.array([.1, .1, .1, .8, .8, .8]), 4)

    def test_shared_depth_buffer_prevents_hidden_field_and_color_bleed(self):
        for reverse in (False, True):
            field, labels, gray, occupied = self.raster(reverse)
            self.assertTrue(occupied[0, 0])
            self.assertEqual(field[0, 0], 2)
            self.assertEqual(labels[0, 0], 2)
            self.assertAlmostEqual(gray[0, 0], .8)

    def test_missing_front_values_do_not_reveal_hidden_hot_cortex(self):
        field, labels, gray, occupied = self.raster(missing_front=True)
        self.assertTrue(occupied[0, 0])
        self.assertTrue(np.isnan(field[0, 0]))
        self.assertEqual(labels[0, 0], 2)
        self.assertAlmostEqual(gray[0, 0], .8)

    def test_field_interpolation_and_categorical_no_blending(self):
        points = np.array([[0, 0, 1], [4, 0, 1], [0, 4, 1]], dtype=float)
        field, labels, _, _ = preview.rasterize_cortex(
            points, np.array([[0, 1, 2]]), np.array([0, 4, 4]),
            np.array([1, 2, 3]), np.ones(3), 4)
        self.assertEqual(field[0, 0], 1.0)
        self.assertEqual(labels[0, 0], 1)
        self.assertEqual(labels[0, 2], 2)
        self.assertEqual(labels[2, 0], 3)

    def test_camera_is_right_handed_and_tracks_normal(self):
        for normal in ([-.6, .4, .6], [0, 0, 1]):
            basis = preview.camera_basis(normal)
            np.testing.assert_allclose(basis.T @ basis, np.eye(3), atol=1e-12)
            self.assertAlmostEqual(np.linalg.det(basis), 1)
            np.testing.assert_allclose(basis[:, 2], np.array(normal) / np.linalg.norm(normal))
        with self.assertRaises(ValueError):
            preview.camera_basis([0, 0, 0])

    def payload(self):
        base = np.full((1, 4, 4), 150, dtype=np.uint8)
        base[:, :, 3] = 255
        colored = base.copy()
        colored[:, :, :3] = [0, 0, 0]
        coil = np.zeros_like(base)
        return {"width": 4, "height": 1, "reference_didt_a_per_us": 2.0,
                "field_f32le_base64": base64.b64encode(np.array([np.nan, .5, 1, 2], dtype='<f4').tobytes()).decode(),
                "base_gray_png": preview.png_uri(base),
                "network_png": preview.png_uri(colored),
                "coil_outline_png": preview.png_uri(coil)}

    def test_absolute_threshold_scales_by_reference_didt_and_includes_equal(self):
        payload = self.payload()
        image = np.asarray(preview.preview_image(payload, 1, 2))
        np.testing.assert_array_equal(image[0, :, 0], [150, 150, 0, 0])
        image = np.asarray(preview.preview_image(payload, 1, 4))
        np.testing.assert_array_equal(image[0, :, 0], [150, 0, 0, 0])
        image = np.asarray(preview.preview_image(payload, 1, 0))
        np.testing.assert_array_equal(image[0, :, 0], [150, 150, 150, 150])

    def test_coil_outline_stays_on_top(self):
        payload = self.payload()
        coil = np.zeros((1, 4, 4), dtype=np.uint8)
        coil[0, 2] = [255, 255, 255, 255]
        payload['coil_outline_png'] = preview.png_uri(coil)
        image = np.asarray(preview.preview_image(payload, 1, 2))
        np.testing.assert_array_equal(image[0, 2], coil[0, 2])

    def test_invalid_intensity_is_rejected(self):
        for threshold, didt in ((-1, 1), (1, -1), (np.nan, 1), (1, np.inf)):
            with self.assertRaises(ValueError):
                preview.preview_image(self.payload(), threshold, didt)

    def test_attach_adds_only_previews_and_does_not_mutate_scores(self):
        lookup = {'subject': 'test', 'entries': [{'rmt_mso': 45, 'treatments': {'target': {'score': .6}}}],
                  'policy': {'maximum_treatment_rmt_ratio': 1.2},
                  'treatment_sources': {'target': {'selection_mode': 'robust'}}}
        before = copy.deepcopy(lookup)
        with patch.object(preview, 'render_source', return_value={'test': True}) as render:
            updated = preview.attach_hotspot_previews(lookup, resolution=640)
        self.assertEqual(lookup, before)
        self.assertEqual({k: v for k, v in updated.items() if k != 'hotspot_previews'}, before)
        self.assertEqual(updated['hotspot_previews'], {'target': {'test': True}})
        render.assert_called_once_with(lookup['treatment_sources']['target'], 'test', None, 640)

    def test_cifti_palette_preserves_black_network(self):
        axis = nib.cifti2.LabelAxis(['networks'], {0: ('None', (0, 0, 0, 0)),
                                                  13: ('Salience', (0, 0, 0, 1))})
        brain = nib.cifti2.BrainModelAxis.from_surface(np.arange(3), 3, name='CortexLeft')
        image = nib.Cifti2Image(np.array([[13, 13, 13]]), header=(axis, brain))
        palette = preview.palette_from_image(image)
        self.assertEqual(palette[13][0], 'Salience')
        np.testing.assert_array_equal(palette[13][1], [0, 0, 0])


if __name__ == '__main__':
    unittest.main()
