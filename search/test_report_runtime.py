"""Reports must consume saved FEM results from the orchestration Python."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SEARCH = ROOT / 'search'
PIPELINE = ROOT / 'pipeline'
BLOCK_SIMNIBS = """import importlib.abc
import sys
class BlockSimNIBS(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'simnibs' or fullname.startswith('simnibs.'):
            raise ModuleNotFoundError('SimNIBS intentionally unavailable to reports')
sys.meta_path.insert(0, BlockSimNIBS())
"""


class ReportRuntimeTests(unittest.TestCase):
    def run_without_simnibs(self, source):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, MPLCONFIGDIR=directory, PYTHONDONTWRITEBYTECODE='1')
            bootstrap = (f'import sys; sys.path[:0] = {[str(SEARCH), str(PIPELINE)]!r}\n'
                         + BLOCK_SIMNIBS + textwrap.dedent(source))
            result = subprocess.run([sys.executable, '-B', '-c', bootstrap],
                                    env=env, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_saved_pose_and_hash_helpers_need_no_simnibs(self):
        self.run_without_simnibs('''
            import hashlib
            from pathlib import Path
            import tempfile
            import numpy as np
            from simulated_pose_geometry import saved_pose_matrix, sha256
            record = {'orientations_degrees': [15],
                      'simulated_pose_matrices': [np.eye(4).tolist()]}
            np.testing.assert_array_equal(saved_pose_matrix(record, 15), np.eye(4))
            for changed in ({'orientations_degrees': [15]},
                            {**record, 'simulated_pose_matrices': [np.zeros((4, 4)).tolist()]}):
                try:
                    saved_pose_matrix(changed, 15)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Invalid saved pose was accepted')
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'surface'
                path.write_bytes(b'known surface bytes')
                assert sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()
        ''')

    def test_report_clis_load_without_simnibs(self):
        for script in ('render_target_selection_report.py', 'render_optimal_pose_qc.py',
                       'render_search_optimization_replay.py',
                       'generate_calibration_dose_report.py'):
            with self.subTest(script=script):
                self.run_without_simnibs(f'''
                    import runpy
                    sys.argv = [{script!r}, '--help']
                    runpy.run_path({str(SEARCH)!r} + '/' + {script!r}, run_name='__main__')
                ''')

    def test_dose_publishing_helpers_load_without_simnibs(self):
        self.run_without_simnibs('''
            import dose_hotspot_preview
            import dose_report_defaults
            import generate_reports
            import generate_pose_variants
            import publish_operator_export
        ''')

    def test_pose_fitting_still_requires_simnibs(self):
        self.run_without_simnibs('''
            from simulated_pose_geometry import fit_pose_matrices
            try:
                fit_pose_matrices('unused.msh', [0, 0, 0], [[0, 1, 0]], 1)
            except ModuleNotFoundError as error:
                assert 'intentionally unavailable' in str(error)
            else:
                raise AssertionError('Fitting silently bypassed the SimNIBS dependency')
        ''')


if __name__ == '__main__':
    unittest.main()
