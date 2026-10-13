import tempfile
import unittest
from pathlib import Path

import numpy as np

from render_optimal_pose_qc import (
    mask_boundary_segments,
    projected_surface_outline_segments,
    resolve_panel_c_mode,
    sulcal_facecolors,
    visualization_options,
    weighted_scoring_mask,
    weighted_target_facecolors,
)


class TestOptimalPoseVisualization(unittest.TestCase):
    def test_peak_marker_defaults_to_bullseye(self):
        options = visualization_options({}, None, None, "/tmp/example_coil.stl")
        self.assertEqual(options["peak_marker"], "bullseye")
        self.assertEqual(options["panel_c_mode"], "auto")

    def test_auto_mode_uses_both_available_maps(self):
        self.assertEqual(
            resolve_panel_c_mode("auto", True, True), "dual_comparison")

    def test_auto_mode_expands_categorical_map_when_it_is_the_only_map(self):
        self.assertEqual(
            resolve_panel_c_mode("auto", True, False), "categorical")

    def test_auto_mode_expands_weighted_map_when_it_is_the_only_map(self):
        self.assertEqual(
            resolve_panel_c_mode("auto", False, True),
            "weighted_probability")

    def test_auto_mode_requires_at_least_one_target_representation(self):
        with self.assertRaisesRegex(ValueError, "requires a categorical"):
            resolve_panel_c_mode("auto", False, False)

    def test_explicit_dual_mode_requires_both_maps(self):
        with self.assertRaisesRegex(ValueError, "requires both"):
            resolve_panel_c_mode("dual_comparison", True, False)

    def test_weighted_panel_mode_can_be_selected(self):
        options = visualization_options(
            {}, None, None, "/tmp/example_coil.stl", "weighted_probability")
        self.assertEqual(options["panel_c_mode"], "weighted_probability")

    def test_dual_panel_mode_can_be_selected(self):
        options = visualization_options(
            {}, None, None, "/tmp/example_coil.stl", "dual_comparison")
        self.assertEqual(options["panel_c_mode"], "dual_comparison")

    def test_dual_context_panel_mode_can_be_selected(self):
        options = visualization_options(
            {}, None, None, "/tmp/example_coil.stl",
            "dual_context_comparison")
        self.assertEqual(options["panel_c_mode"], "dual_context_comparison")

    def test_peak_marker_can_be_set_in_visualization_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "visualization.json"
            path.write_text('{"peak_marker": "x"}')
            options = visualization_options({}, path, None, "/tmp/example_coil.stl")
        self.assertEqual(options["peak_marker"], "x")

    def test_coil_name_always_follows_rendered_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "visualization.json"
            path.write_text('{"coil_display_name": "wrong coil"}')
            options = visualization_options(
                {}, path, None, "/coils/MagVenture_C-B60.stl")
        self.assertEqual(options["coil_display_name"], "MagVenture C-B60")

    def test_hardware_variant_name_comes_from_run_provenance(self):
        options = visualization_options(
            {"coil_hardware_name": "MagVenture Cool-B65 A/P"},
            None, None, "/coils/MagVenture_Cool-B65.stl")
        self.assertEqual(
            options["coil_display_name"], "MagVenture Cool-B65 A/P")

    def test_cli_marker_override_takes_precedence(self):
        options = visualization_options(
            {"visualization": {"peak_marker": "x"}}, None, "crosshair",
            "/tmp/example_coil.stl")
        self.assertEqual(options["peak_marker"], "crosshair")

    def test_binary_outline_uses_within_triangle_contour(self):
        points = np.asarray([[0.0, 0.0, 0.0],
                             [2.0, 0.0, 0.0],
                             [0.0, 2.0, 0.0]])
        triangles = np.asarray([[0, 1, 2]], dtype=np.int64)
        segments = mask_boundary_segments(
            points, triangles, np.asarray([True, False, False]))
        expected = np.asarray([[[1.0, 0.0, 0.0],
                                [0.0, 1.0, 0.0]]])
        np.testing.assert_allclose(segments, expected)

    def test_weighted_target_colors_use_native_weights_without_rescaling(self):
        triangles = np.asarray([[0, 1, 2], [1, 2, 3]], dtype=np.int64)
        anatomy = np.asarray([-1.0, -0.5, 0.5, 1.0])
        target = np.asarray([0.0, 0.0, 0.0, 1.0])
        canonical = np.asarray([0.2, 0.8, 0.9])
        background = sulcal_facecolors(triangles, anatomy)
        result = weighted_target_facecolors(
            triangles, target, anatomy, canonical)
        np.testing.assert_allclose(result[0], background[0])
        expected_weight = 1.0 / 3.0
        expected = ((1.0 - expected_weight) * background[1, :3] +
                    expected_weight * canonical)
        np.testing.assert_allclose(result[1, :3], expected)

    def test_probabilistic_scoring_preserves_support_outside_hard_label(self):
        anatomy = np.asarray([True, True, False, True])
        labels = np.asarray([9, 4, 9, 4])
        weights = np.asarray([0.8, 0.3, 0.5, 0.0])
        result = weighted_scoring_mask(
            anatomy, labels, 9, weights, False, True)
        np.testing.assert_array_equal(result, anatomy)

    def test_scoring_can_be_restricted_to_hard_label_support(self):
        anatomy = np.asarray([True, True, False, True])
        labels = np.asarray([9, 4, 9, 4])
        weights = np.asarray([0.8, 0.3, 0.5, 0.0])
        result = weighted_scoring_mask(
            anatomy, labels, 9, weights, True, True)
        np.testing.assert_array_equal(
            result, np.asarray([True, False, False, False]))

    def test_projected_surface_outline_omits_internal_mesh_edges(self):
        points = np.asarray([[0.0, 0.0, 0.0],
                             [1.0, 0.0, 0.0],
                             [1.0, 1.0, 0.0],
                             [0.0, 1.0, 0.0]])
        triangles = np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
        segments = projected_surface_outline_segments(
            points, triangles, np.asarray([0.0, 0.0, 1.0]), resolution=80)
        midpoint = segments.mean(axis=1)
        diagonal = np.isclose(midpoint[:, 0], midpoint[:, 1], atol=0.04)
        interior = ((midpoint[:, 0] > 0.08) & (midpoint[:, 0] < 0.92) &
                    (midpoint[:, 1] > 0.08) & (midpoint[:, 1] < 0.92))
        self.assertFalse(np.any(diagonal & interior))

if __name__ == "__main__":
    unittest.main()
