"""Exercise benchmark metadata with isolated FEM stubs, never real simulations."""

import contextlib
import csv
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np


def load_worker():
    simnibs = ModuleType('simnibs')
    simnibs.run_simnibs = lambda session: None
    simnibs.sim_struct = SimpleNamespace(SESSION=lambda: None)
    geometry = ModuleType('simulated_pose_geometry')
    geometry.fit_pose_matrices = lambda *args, **kwargs: None
    spec = importlib.util.spec_from_file_location(
        'benchmark_subject_test_worker', Path(__file__).with_name('benchmark_pose_batch.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'simnibs': simnibs, 'simulated_pose_geometry': geometry}):
        spec.loader.exec_module(module)
    return module


class BenchmarkSubjectProvenanceTests(unittest.TestCase):
    def test_subject_required_before_any_simulation(self):
        worker = load_worker()
        with patch.object(sys, 'argv', ['benchmark_pose_batch.py']), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            worker.main()
        self.assertEqual(error.exception.code, 2)

    def run_stubbed(self, subject):
        worker = load_worker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            m2m = root / 'm2m'
            m2m.mkdir()
            paths = {}
            for name in ('head-mesh', 'coil', 'workbench', 'skin', 'left-midthickness',
                         'right-midthickness', 'left-white', 'right-white', 'left-pial',
                         'right-pial', 'left-roi', 'right-roi'):
                paths[name] = root / name
                paths[name].write_text('test input')
            grid = root / 'grid.tsv'
            row = {'center_id': 1, 'scalp_vertex': 1, 'x': 0, 'y': 0, 'z': 10,
                   'network_name': 'CUSTOM_NETWORK', 'target_map_mode': 'binary',
                   'strategy': 'CUSTOM_STRATEGY'}
            with grid.open('w') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row), delimiter='\t')
                writer.writeheader()
                writer.writerow(row)
            output = root / 'result'
            argv = ['benchmark_pose_batch.py', '--subject', subject, '--grid-tsv', str(grid),
                    '--m2m-dir', str(m2m), '--output', str(output)]
            for name, path in paths.items():
                argv += [f'--{name}', str(path)]
            session = SimpleNamespace()
            positions = []

            def add_position():
                position = SimpleNamespace()
                positions.append(position)
                return position

            tms = SimpleNamespace(add_position=add_position, pos=positions)
            session.add_tmslist = lambda: tms

            def fake_simulation(value):
                self.assertIs(value, session)
                self.assertEqual(value.fnamehead, str(paths['head-mesh']))
                self.assertEqual(value.subpath, str(m2m))
                self.assertEqual(tms.fnamecoil, str(paths['coil']))
                volumes = Path(value.pathfem) / 'subject_volumes'
                volumes.mkdir(parents=True)
                (volumes / 'test_magnE.nii.gz').write_bytes(b'stub volume')

            points = np.array([[0, 0, 10], [1, 0, 10], [0, 1, 10]], dtype=float)
            matrix = np.eye(4)
            with patch.object(sys, 'argv', argv), \
                    patch.object(worker.sim_struct, 'SESSION', return_value=session), \
                    patch.object(worker, 'load_surface', return_value=(points, np.array([[0, 1, 2]]))), \
                    patch.object(worker, 'fit_pose_matrices', return_value=([matrix], {'status': 'test'})), \
                    patch.object(worker, 'run_checked', return_value=0), \
                    patch.object(worker, 'run_simnibs', side_effect=fake_simulation) as simulate, \
                    contextlib.redirect_stdout(io.StringIO()):
                if not subject:
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        worker.main()
                    simulate.assert_not_called()
                    return
                worker.main()
                simulate.assert_called_once()
            data = json.loads((output / 'Benchmark.json').read_text())
            self.assertEqual(data['subject'], subject)
            self.assertEqual(data['network'], 'CUSTOM_NETWORK')
            self.assertEqual(data['map_mode'], 'binary')
            self.assertEqual(data['strategy'], 'CUSTOM_STRATEGY')
            self.assertEqual(data['inputs']['head_mesh'], str(paths['head-mesh']))
            self.assertEqual(data['m2m_dir'], str(m2m))
            self.assertEqual(data['simulated_pose_matrices'], [matrix.tolist()])
            self.assertTrue(data['simnibs_executed'])
            self.assertFalse(data['simulation_volumes_reused'])

    def test_new_subject_metadata_comes_from_explicit_cli_identity(self):
        for subject in ('BioSelect-23002', 'NEW-SUBJECT-987'):
            with self.subTest(subject=subject):
                self.run_stubbed(subject)

    def test_empty_subject_fails_without_simulation(self):
        self.run_stubbed('')


if __name__ == '__main__':
    unittest.main()
