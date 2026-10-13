#!/usr/bin/env python3
import unittest

import numpy as np

from run_simulations import (
    apply_scoring_domain, matlab_prctile, normalized_name,
    target_to_global_peak_ratio,
)


class TestScoringCompatibility(unittest.TestCase):
    def test_matlab_prctile_uses_half_sample_plotting_positions(self):
        values = np.arange(1.0, 11.0)
        np.testing.assert_allclose(
            matlab_prctile(values, [0, 10, 20, 50, 90, 100]),
            [1.0, 1.5, 2.5, 5.5, 9.5, 10.0], rtol=0, atol=1e-12)

    def test_hotspot_uses_strict_greater_than(self):
        values = np.arange(1.0, 101.0)
        cutoff = matlab_prctile(values, [99.0])[0]
        hotspot = values > cutoff
        self.assertEqual(int(hotspot.sum()), 1)
        self.assertEqual(float(values[hotspot][0]), 100.0)

    def test_harmless_network_name_normalization(self):
        self.assertEqual(normalized_name("SM-Hand"), normalized_name("SM_Hand"))
        self.assertEqual(normalized_name("Cingulo-opercular"),
                         normalized_name("Cingulo Opercular"))

    def test_nonfinite_percentile_input_fails(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            matlab_prctile([1.0, np.nan], [99.5])

    def test_scoring_domain_intersects_network_support_and_anatomy(self):
        weighted, binary, anatomy, scope = apply_scoring_domain(
            np.array([0.9, 0.8, 0.7, 0.6]),
            np.array([1.0, 1.0, 1.0, 0.0]),
            {"constrain_target_to_anatomical_mask": True,
             "restrict_weights_to_binary_support": True},
            np.array([1.0, 0.0, 1.0, 1.0]))
        np.testing.assert_array_equal(weighted, [0.9, 0.0, 0.7, 0.0])
        np.testing.assert_array_equal(binary, [1.0, 0.0, 1.0, 0.0])
        np.testing.assert_array_equal(anatomy, [True, False, True, True])
        self.assertEqual(scope, "binary_network_support_within_anatomical_mask")

    def test_nonbinary_anatomical_scoring_mask_fails(self):
        with self.assertRaisesRegex(ValueError, "explicitly binary"):
            apply_scoring_domain(
                np.ones(3), np.ones(3),
                {"constrain_target_to_anatomical_mask": True},
                np.array([1.0, 0.5, 0.0]))

    def test_scoring_domain_uses_only_retained_design_components(self):
        weighted, binary, _, scope = apply_scoring_domain(
            np.array([0.9, 0.8, 0.7, 0.6]),
            np.ones(4),
            {"restrict_target_to_design_components": True,
             "design_component_ids": [7]},
            component_values=np.array([7.0, 5.0, 7.0, 0.0]))
        np.testing.assert_array_equal(weighted, [0.9, 0.0, 0.7, 0.0])
        np.testing.assert_array_equal(binary, [1.0, 0.0, 1.0, 0.0])
        self.assertEqual(scope, "retained_eligible_components")

    def test_target_to_global_peak_ratio_allows_near_target_peak(self):
        ratio, target_maximum, global_maximum = target_to_global_peak_ratio(
            np.array([1.0, 0.96, 0.2]), np.array([0.0, 0.8, 0.0]))
        self.assertAlmostEqual(ratio, 0.96)
        self.assertEqual(target_maximum, 0.96)
        self.assertEqual(global_maximum, 1.0)


if __name__ == "__main__":
    unittest.main()
