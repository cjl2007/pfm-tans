import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("render_search_optimization_replay.py")
SPEC = importlib.util.spec_from_file_location("search_replay", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TestSearchOptimizationReplay(unittest.TestCase):
    def test_final_prescription_is_appended_without_rewinding_history(self):
        sequence = MODULE.build_render_sequence(205, 20, 165)
        self.assertEqual(sequence[-2], (204, 204, False))
        self.assertEqual(sequence[-1], (165, 204, True))

    def test_last_search_pose_becomes_final_prescription_when_selected(self):
        sequence = MODULE.build_render_sequence(12, 1, 11)
        self.assertEqual(len(sequence), 12)
        self.assertEqual(sequence[-1], (11, 11, True))

    def test_invalid_arguments_fail(self):
        with self.assertRaises(ValueError):
            MODULE.build_render_sequence(0, 1, 0)
        with self.assertRaises(ValueError):
            MODULE.build_render_sequence(5, 0, 1)
        with self.assertRaises(ValueError):
            MODULE.build_render_sequence(5, 1, 5)


if __name__ == "__main__":
    unittest.main()
