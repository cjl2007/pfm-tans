import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from run_simulations import validate_reused_center
from simulated_pose_geometry import sha256


class ExteriorSkinResumeTests(unittest.TestCase):
    def test_resume_requires_matching_exterior_skin_and_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scalp = root / 'Skin.surf.gii'
            scalp.write_bytes(b'exterior fixture')
            grid = root / 'SearchGrid.tsv'
            grid.write_text('center_id\tscalp_vertex\tx\ty\tz\n1\t20\t1\t2\t3\n')
            cfg = {'skin': str(scalp), 'grid_tsv': str(grid),
                   'head_mesh': str(root / 'head.msh'), 'coil': str(root / 'coil.ccd'),
                   'orientations_degrees': [0, 15], 'distance_mm': 1., 'didt_aperus': 1e6}
            benchmark = {'scalp_vertex_one_based': 20, 'center_xyz': [1, 2, 3],
                'orientations_degrees': [0, 15], 'distance_mm': 1., 'didt_aperus': 1e6,
                'inputs': {key: cfg[key] for key in ('head_mesh', 'coil', 'skin')},
                'geometry_validation': {'status': 'validated'}}
            with self.assertRaisesRegex(ValueError, 'exterior-skin'):
                validate_reused_center(cfg, 1, benchmark)
            benchmark['geometry_validation'].update(
                surface_scope='exterior_skin', scalp_surface_sha256=sha256(scalp))
            validate_reused_center(cfg, 1, benchmark)
            benchmark['center_xyz'][0] = 9
            with self.assertRaisesRegex(ValueError, 'pose batch'):
                validate_reused_center(cfg, 1, benchmark)
            benchmark['center_xyz'][0] = 1
            scalp.write_bytes(b'changed surface')
            with self.assertRaisesRegex(ValueError, 'exterior-skin'):
                validate_reused_center(cfg, 1, benchmark)


if __name__ == '__main__':
    unittest.main()
