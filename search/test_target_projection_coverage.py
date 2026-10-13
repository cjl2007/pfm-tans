import importlib.util
import unittest
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).with_name("generate_target_projection_coverage_plan.py")
SPEC = importlib.util.spec_from_file_location("coverage_plan", MODULE_PATH)
coverage_plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage_plan)


class TestTargetProjectionCoverage(unittest.TestCase):
    def setUp(self):
        self.points = np.asarray([
            [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0],
            [3.0, 0.0, 0.0], [4.0, 0.0, 0.0],
        ])
        triangles = np.asarray([[0, 1, 2], [2, 3, 4]])
        self.graph = coverage_plan.mesh_graph(self.points, triangles)
        self.demand = np.arange(5, dtype=np.int64)
        self.weights = np.ones(5)

    def test_duplicate_projections_are_aggregated(self):
        vertices, area, weighted, counts = coverage_plan.aggregate_projections(
            np.asarray([2, 2, 4]), np.asarray([1.0, 2.0, 3.0]),
            np.asarray([0.5, 1.0, 1.5]))
        np.testing.assert_array_equal(vertices, [2, 4])
        np.testing.assert_allclose(area, [3.0, 3.0])
        np.testing.assert_allclose(weighted, [1.5, 1.5])
        np.testing.assert_array_equal(counts, [2, 1])

    def test_kcenter_is_deterministic_and_unique(self):
        first = coverage_plan.deterministic_kcenter(
            self.graph, self.points, self.demand, self.weights,
            np.asarray([2.0, 0.0, 0.0]), 3)
        second = coverage_plan.deterministic_kcenter(
            self.graph, self.points, self.demand, self.weights,
            np.asarray([2.0, 0.0, 0.0]), 3)
        np.testing.assert_array_equal(first[0], second[0])
        self.assertEqual(len(np.unique(first[0])), 3)
        np.testing.assert_allclose(first[1], second[1])

    def test_more_centers_do_not_worsen_coverage(self):
        two = coverage_plan.deterministic_kcenter(
            self.graph, self.points, self.demand, self.weights,
            np.asarray([2.0, 0.0, 0.0]), 2)
        three = coverage_plan.deterministic_kcenter(
            self.graph, self.points, self.demand, self.weights,
            np.asarray([2.0, 0.0, 0.0]), 3)
        self.assertLessEqual(np.max(three[1]), np.max(two[1]))
        self.assertLessEqual(np.mean(three[1]), np.mean(two[1]))

    def test_tie_break_prefers_larger_weight_then_vertex(self):
        weights = np.asarray([1.0, 1.0, 1.0, 1.0, 2.0])
        selected, *_ = coverage_plan.deterministic_kcenter(
            self.graph, self.points, self.demand, weights,
            np.asarray([2.0, 0.0, 0.0]), 2, refinement_passes=0)
        np.testing.assert_array_equal(selected, [2, 4])

    def test_mandatory_centroid_anchor_is_never_relocated(self):
        selected, *_ = coverage_plan.deterministic_kcenter(
            self.graph, self.points, self.demand, self.weights,
            np.asarray([2.0, 0.0, 0.0]), 3,
            mandatory_vertices=np.asarray([1], dtype=np.int64))
        self.assertEqual(selected[0], 1)
        self.assertIn(1, selected)

    def test_density_selects_minimum_count_and_preserves_anchor(self):
        count, achieved = coverage_plan.center_count_for_coverage(
            self.graph, self.demand, self.weights,
            mandatory_vertices=np.asarray([2]), coverage_fraction=0.8,
            coverage_radius_mm=0.1, minimum_centers=1, maximum_centers=5)
        self.assertEqual(count, 4)
        self.assertGreaterEqual(achieved, 0.8)

    def test_meaningful_component_filter_excludes_microcomponents(self):
        rows = [
            {"cluster_id": 1, "vertex_count": 226, "surface_area_mm2": 598.2},
            {"cluster_id": 2, "vertex_count": 1, "surface_area_mm2": 4.1},
        ]
        self.assertEqual(coverage_plan.select_design_clusters(rows, 10, 25.0), [1])

    def test_component_filter_falls_back_to_largest(self):
        rows = [
            {"cluster_id": 1, "vertex_count": 2, "surface_area_mm2": 5.0},
            {"cluster_id": 2, "vertex_count": 1, "surface_area_mm2": 3.0},
        ]
        self.assertEqual(coverage_plan.select_design_clusters(rows, 10, 25.0), [1])

    def test_relative_weighted_component_filter_excludes_ventral_fragment(self):
        rows = [
            {"cluster_id": 7, "vertex_count": 431, "surface_area_mm2": 1253.7,
             "weighted_area_mm2": 509.24},
            {"cluster_id": 5, "vertex_count": 41, "surface_area_mm2": 86.14,
             "weighted_area_mm2": 35.72},
            {"cluster_id": 4, "vertex_count": 21, "surface_area_mm2": 64.03,
             "weighted_area_mm2": 23.25},
        ]
        self.assertEqual(
            coverage_plan.select_design_clusters(rows, 10, 25.0, 0.10), [7])

    def test_locked_default_policy(self):
        policy = coverage_plan.DEFAULT_POLICY
        self.assertEqual(policy["policy_status"], "working_default")
        self.assertEqual(policy["target_weighted_coverage_fraction"], 0.85)
        self.assertEqual(policy["coverage_radius_mm"], 5.0)
        self.assertEqual(policy["minimum_design_cluster_vertices"], 10)
        self.assertEqual(policy["minimum_design_cluster_area_mm2"], 25.0)
        self.assertEqual(
            policy["minimum_design_cluster_relative_weighted_area_fraction"], 0.10)
        self.assertEqual(policy["scoring_target_scope"],
                         "retained_eligible_components")
        self.assertEqual(policy["minimum_target_to_global_peak_ratio"], 0.95)
        self.assertTrue(
            policy["enforce_target_peak_ratio_for_nominal_selection"])
        self.assertEqual(policy["maximum_centers"], 40)

    def test_binary_coverage_semantics_are_explicit(self):
        semantics = coverage_plan.coverage_semantics("binary")
        self.assertEqual(
            semantics["coverage_measure"], "binary_target_surface_area")
        self.assertIn("binary target surface area",
                      semantics["density_definition"])

    def test_unknown_coverage_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "target map mode"):
            coverage_plan.coverage_semantics("ambiguous")


if __name__ == "__main__":
    unittest.main()
