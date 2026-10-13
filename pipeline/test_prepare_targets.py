#!/usr/bin/env python3
import unittest

import numpy as np

from prepare_targets import (normalized_name, resolve_unique_name,
                             stable_components, weighted_quantile)


class TargetPreparationTests(unittest.TestCase):
    def test_normalized_name(self):
        self.assertEqual(normalized_name("Somatomotor_Hand"), "somatomotorhand")
        self.assertEqual(normalized_name("Somatomotor Hand"), "somatomotorhand")

    def test_network_number_is_one_based_prior_index(self):
        names = ["Visual", "Somatomotor_Hand", "Salience"]
        self.assertEqual(resolve_unique_name(names, "2", "Prior network"), 1)
        with self.assertRaisesRegex(ValueError, "between 1 and 3"):
            resolve_unique_name(names, "4", "Prior network")

    def test_weighted_quantile_matches_empirical_definition(self):
        values = np.array([3.0, 1.0, 2.0])
        weights = np.array([1.0, 1.0, 8.0])
        actual = weighted_quantile(values, weights, [0.1, 0.5, 0.95])
        np.testing.assert_array_equal(actual, [1.0, 2.0, 3.0])

    def test_components_include_isolated_vertices_and_have_stable_ids(self):
        faces = np.array([[0, 1, 2], [3, 4, 5]])
        support = np.array([True, True, True, True, True, True, True])
        hemisphere = np.array(["L"] * 7)
        labels = stable_components(faces, support, hemisphere)
        np.testing.assert_array_equal(labels, [1, 1, 1, 2, 2, 2, 3])


if __name__ == "__main__":
    unittest.main()
