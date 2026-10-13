#!/usr/bin/env python3
"""Focused tests for the neuronavigation export contract."""

import unittest

import numpy as np

from export_neuronavigation_package import find_placement, normalized_target_name
from pose_prescription import (
    canonicalize_pose_matrix,
    prescribed_orientation,
    resolve_handle_policy,
)


class TestNeuronavigationExport(unittest.TestCase):
    def test_target_name_is_file_safe(self):
        self.assertEqual(normalized_target_name("AMN / CO"), "AMN-CO")

    def test_find_placement_requires_exact_saved_orientation(self):
        rows = [
            {"orientation_degrees": "0"},
            {"orientation_degrees": "5"},
        ]
        self.assertEqual(find_placement(rows, 5), rows[1])
        with self.assertRaises(ValueError):
            find_placement(rows, 10)

    def test_find_placement_rejects_duplicates(self):
        rows = [
            {"orientation_degrees": "5"},
            {"orientation_degrees": "5.0"},
        ]
        with self.assertRaises(ValueError):
            find_placement(rows, 5)

    def test_posterior_inferior_policy_flips_anterior_superior_handle(self):
        policy = resolve_handle_policy({"prescription": {"handle_orientation": {
            "mode": "prefer_posterior_inferior",
            "coil_180_degree_magnitude_equivalent": True,
        }}})
        matrix = np.eye(4)
        matrix[:3, 1] = [0.0, -np.sqrt(0.5), -np.sqrt(0.5)]
        prescribed, decision = canonicalize_pose_matrix(matrix, policy)
        self.assertTrue(decision["flip_applied"])
        np.testing.assert_allclose(prescribed[:3, 3], matrix[:3, 3])
        np.testing.assert_allclose(prescribed[:3, 2], matrix[:3, 2])
        np.testing.assert_allclose(
            decision["prescribed_physical_handle_direction_ras"],
            [0.0, -np.sqrt(0.5), -np.sqrt(0.5)])
        self.assertEqual(prescribed_orientation(135, True), 315)

    def test_auto_flip_requires_explicit_magnitude_equivalence(self):
        with self.assertRaises(ValueError):
            resolve_handle_policy({"prescription": {"handle_orientation": {
                "mode": "prefer_posterior_inferior",
            }}})


if __name__ == "__main__":
    unittest.main()
