import unittest

from evaluate_robust_placements import lower_tail_mean
from generate_robustness_plan import circular_angle_difference


class TestRobustPlacement(unittest.TestCase):
    def test_orientation_distance_wraps_at_180_degrees(self):
        self.assertEqual(circular_angle_difference(175, 5), 10)
        self.assertEqual(circular_angle_difference(0, 175), 5)

    def test_lower_tail_mean_uses_at_least_one_value(self):
        self.assertEqual(lower_tail_mean([4, 1, 3, 2], 0.10), 1)

    def test_lower_tail_mean_averages_requested_tail(self):
        self.assertEqual(lower_tail_mean(range(1, 11), 0.20), 1.5)


if __name__ == "__main__":
    unittest.main()
