import csv
import json
import tempfile
import unittest
from pathlib import Path

import nibabel as nib
import numpy as np

from run_simulations import (center_command, validate_coarse_center_constraint,
                              validate_projection_coverage_policy)


class TestCoarseConstraint(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.surface = Path(self.temp.name) / "skin.surf.gii"
        points = np.asarray([[0, 0, 0], [10, 0, 0], [20, 0, 0],
                             [0, 10, 0], [10, 10, 0], [20, 10, 0]], np.float32)
        triangles = np.asarray([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]],
                               np.int32)
        nib.save(nib.gifti.GiftiImage(darrays=[
            nib.gifti.GiftiDataArray(points, intent="NIFTI_INTENT_POINTSET"),
            nib.gifti.GiftiDataArray(triangles, intent="NIFTI_INTENT_TRIANGLE")]),
            self.surface)
        self.cfg = {
            "skin": str(self.surface),
            "coarse_center_constraint": {
                "type": "geodesic_radius_from_target_patch_centroid_projection",
                "anchor_scalp_vertex": 1,
                "maximum_distance_mm": 20,
                "centroid_mode": "area_times_target_weight",
                "target_cluster_id": 1,
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def row(self, center_id, vertex, xyz):
        return {"center_id": str(center_id), "scalp_vertex": str(vertex),
                "x": str(xyz[0]), "y": str(xyz[1]), "z": str(xyz[2])}

    def test_accepts_centers_within_geodesic_radius(self):
        rows = [self.row(1, 1, (0, 0, 0)), self.row(2, 2, (10, 0, 0)),
                self.row(3, 3, (20, 0, 0))]
        report = validate_coarse_center_constraint(self.cfg, rows)
        self.assertTrue(report["verified"])
        self.assertAlmostEqual(report["maximum_observed_distance_mm"], 20)

    def test_rejects_center_outside_geodesic_radius(self):
        self.cfg["coarse_center_constraint"]["maximum_distance_mm"] = 19
        rows = [self.row(1, 3, (20, 0, 0))]
        with self.assertRaisesRegex(ValueError, "exceed"):
            validate_coarse_center_constraint(self.cfg, rows)

    def test_rejects_coordinate_vertex_mismatch(self):
        rows = [self.row(1, 2, (11, 0, 0))]
        with self.assertRaisesRegex(ValueError, "coordinates"):
            validate_coarse_center_constraint(self.cfg, rows)

    def test_requires_constraint(self):
        with self.assertRaisesRegex(ValueError, "unconstrained"):
            validate_coarse_center_constraint({"skin": str(self.surface)}, [])

    def test_accepts_locked_projection_coverage_policy(self):
        policy_path = Path(__file__).with_name("default_search_policy.json")
        policy = json.loads(policy_path.read_text())
        summary_path = Path(self.temp.name) / "CoverageSummary.json"
        summary_path.write_text(json.dumps({
            "centers": 2,
            "achieved_target_weighted_coverage_fraction": 0.86,
            "mandatory_centroid_anchor_scalp_vertices": [1],
        }))
        policy["coverage_summary"] = str(summary_path)
        cfg = {"skin": str(self.surface), "coarse_grid_policy": policy}
        rows = [self.row(1, 1, (0, 0, 0)), self.row(2, 2, (10, 0, 0))]
        report = validate_projection_coverage_policy(cfg, rows)
        self.assertTrue(report["verified"])
        self.assertEqual(report["centers"], 2)

    def test_rejects_changed_default_density(self):
        policy_path = Path(__file__).with_name("default_search_policy.json")
        policy = json.loads(policy_path.read_text())
        policy["target_weighted_coverage_fraction"] = 0.5
        cfg = {"skin": str(self.surface), "coarse_grid_policy": policy}
        with self.assertRaisesRegex(ValueError, "policy mismatch"):
            validate_projection_coverage_policy(cfg, [])

    def test_accepts_explicit_run_policy_density_override(self):
        default_path = Path(__file__).with_name("default_search_policy.json")
        policy = json.loads(default_path.read_text())
        policy["target_weighted_coverage_fraction"] = 0.90
        policy["policy_name"] += "_coverage_0.900"
        policy["policy_status"] = "explicit_run_override"
        resolved_path = Path(self.temp.name) / "ResolvedSearchPolicy.json"
        resolved_path.write_text(json.dumps(policy))
        summary_path = Path(self.temp.name) / "CoverageSummary.json"
        summary_path.write_text(json.dumps({
            "centers": 2,
            "map_mode": "binary",
            "coverage_measure": "binary_target_surface_area",
            "achieved_target_weighted_coverage_fraction": 0.91,
            "achieved_target_coverage_fraction": 0.91,
            "mandatory_centroid_anchor_scalp_vertices": [1],
        }))
        policy["policy_file"] = str(resolved_path)
        policy["coverage_summary"] = str(summary_path)
        cfg = {"skin": str(self.surface), "map_mode": "binary",
               "coarse_grid_policy": policy}
        rows = [self.row(1, 1, (0, 0, 0)), self.row(2, 2, (10, 0, 0))]
        report = validate_projection_coverage_policy(cfg, rows)
        self.assertTrue(report["verified"])
        self.assertEqual(report["map_mode"], "binary")
        self.assertEqual(report["coverage_measure"],
                         "binary_target_surface_area")
        self.assertAlmostEqual(report["requested_target_coverage_fraction"], 0.90)

    def test_center_command_forwards_resume_to_benchmark(self):
        cfg = {
            "subject": "TEST-SECOND-SUBJECT",
            "simnibs_python": "/simnibs/python",
            "workbench": "/workbench/wb_command",
            "grid_tsv": "/grid.tsv",
            "orientations_degrees": [0, 15],
            "head_mesh": "/head.msh", "m2m_dir": "/m2m",
            "coil": "/coil.ccd", "skin": "/skin.surf.gii",
            "distance_mm": 1, "didt_aperus": 1e6,
            "surfaces": {
                "left_midthickness": "/L.mid", "right_midthickness": "/R.mid",
                "left_white": "/L.white", "right_white": "/R.white",
                "left_pial": "/L.pial", "right_pial": "/R.pial",
                "left_roi": "/L.roi", "right_roi": "/R.roi",
            },
        }
        normal = center_command(cfg, 2, Path("/output"), resume=False)
        resumed = center_command(cfg, 2, Path("/output"), resume=True)
        self.assertEqual(normal[normal.index("--subject") + 1], cfg["subject"])
        self.assertIn("--workbench", normal)
        self.assertEqual(normal[normal.index("--workbench") + 1],
                         "/workbench/wb_command")
        self.assertNotIn("--resume", normal)
        self.assertEqual(resumed[-1], "--resume")


if __name__ == "__main__":
    unittest.main()
