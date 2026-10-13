"""Report-driver checks that do not require importing SimNIBS in the parent."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml


def load_module(name, filename):
    directory = Path(os.environ.get('TEST_POSE_RUNTIME_DIR', Path(__file__).parent))
    spec = importlib.util.spec_from_file_location(name, directory / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = load_module('pose_runtime_generator', 'generate_pose_variants.py')
validator = load_module('pose_runtime_validator', 'validate_pose_variants.py')


class PoseVariantRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'subject with spaces'
        self.root.mkdir()
        self.reports = self.root / 'reports'
        self.reports.mkdir()
        self.published = self.reports / 'PoseVariants.json'
        self.published.write_bytes(b'previous valid index\n')
        self.previous = self.published.read_bytes()
        self.manifest_path = self.root / 'MultinetworkRunManifest.json'
        self.config_path = self.root / 'resolved.yaml'
        self.config = {'simulation': {'simnibs_python': sys.executable}}
        self.config_path.write_text(yaml.safe_dump(self.config))
        self.manifest = {'subject': 'Test', 'configuration': str(self.config_path),
                         'targets': [{'network': 'Somatomotor_Hand'}]}
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.index = {'subject': 'Test', 'targets': [], 'dose_pairings': [],
                      'new_simulations': 0}
        self.proof = {'status': 'passed', 'clinical_clearance': False,
                      'pose_variants_checked': 2, 'dose_pairings_checked': 1}

    def validation_command(self, command, label):
        self.assertEqual(command[0], str(Path(sys.executable).resolve()))
        self.assertEqual(Path(command[1]).name, 'validate_pose_variants.py')
        self.assertEqual(command[command.index('--manifest') + 1], str(self.manifest_path))
        staged = Path(command[command.index('--index') + 1])
        self.assertEqual(json.loads(staged.read_text()), self.index)
        self.assertEqual(self.published.read_bytes(), self.previous)
        return Path(command[command.index('--output') + 1])

    def test_staged_validation_uses_configured_interpreter_before_publication(self):
        def run(command, label):
            self.validation_command(command, label).write_text(json.dumps(self.proof))

        with mock.patch('generate_reports.run', side_effect=run) as invoked, \
                mock.patch.dict(sys.modules, {'simnibs': None}):
            result = generator.validate_and_publish(self.manifest_path, self.index, self.config)
        invoked.assert_called_once()
        self.assertEqual(result['validation'], self.proof)
        self.assertEqual(json.loads(self.published.read_text()), result)
        self.assertEqual(list(self.reports.glob('.pose-validation-*')), [])

    def test_nonzero_validator_preserves_previous_index(self):
        def fail(command, label):
            self.validation_command(command, label)
            raise RuntimeError('validation subprocess failed')

        with mock.patch('generate_reports.run', side_effect=fail), \
                self.assertRaisesRegex(RuntimeError, 'validation subprocess failed'):
            generator.validate_and_publish(self.manifest_path, self.index, self.config)
        self.assertEqual(self.published.read_bytes(), self.previous)
        self.assertNotIn('validation', self.index)
        self.assertEqual(list(self.reports.glob('.pose-validation-*')), [])

    def test_missing_validation_output_preserves_previous_index(self):
        with mock.patch('generate_reports.run'), self.assertRaises(FileNotFoundError):
            generator.validate_and_publish(self.manifest_path, self.index, self.config)
        self.assertEqual(self.published.read_bytes(), self.previous)

    def test_failed_or_unsafe_validation_summary_is_not_published(self):
        for proof in ({'status': 'failed', 'clinical_clearance': False},
                      {'status': 'passed', 'clinical_clearance': True},
                      {'status': 'passed'}):
            with self.subTest(proof=proof):
                def run(command, label):
                    self.validation_command(command, label).write_text(json.dumps(proof))
                with mock.patch('generate_reports.run', side_effect=run), \
                        self.assertRaisesRegex(ValueError, 'did not pass'):
                    generator.validate_and_publish(self.manifest_path, self.index, self.config)
                self.assertEqual(self.published.read_bytes(), self.previous)

    def test_real_subprocess_can_run_when_parent_has_no_simnibs(self):
        interpreter = self.root / 'configured python'
        interpreter.write_text(
            '#!' + sys.executable + '\n'
            'import json, pathlib, sys\n'
            'assert pathlib.Path(sys.argv[1]).name == "validate_pose_variants.py"\n'
            'index = pathlib.Path(sys.argv[sys.argv.index("--index") + 1])\n'
            'assert json.loads(index.read_text())["subject"] == "Test"\n'
            'output = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])\n'
            'output.write_text(json.dumps({"status": "passed", "clinical_clearance": False}))\n')
        interpreter.chmod(0o755)
        config = {'simulation': {'simnibs_python': str(interpreter)}}
        with mock.patch.dict(sys.modules, {'simnibs': None}):
            result = generator.validate_and_publish(self.manifest_path, self.index, config)
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertEqual(json.loads(self.published.read_text()), result)

    def test_generate_uses_validation_boundary(self):
        priors = self.root / 'Priors.mat'
        visual = self.root / 'visual.json'
        priors.touch()
        visual.write_text('{}')
        with mock.patch.object(generator, 'render_target_variants', return_value={'network': 'Test'}), \
                mock.patch.object(generator, 'render_dose_variants', return_value=[]), \
                mock.patch.object(generator, 'validate_and_publish', return_value={'validated': True}) as boundary, \
                mock.patch.dict(sys.modules, {'simnibs': None}):
            result = generator.generate(self.manifest_path, priors, visual)
        self.assertEqual(result, {'validated': True})
        manifest, index, config = boundary.call_args[0]
        self.assertEqual(manifest, self.manifest_path)
        self.assertEqual(config, self.config)
        self.assertEqual(index['targets'], [{'network': 'Test'}])
        self.assertEqual(index['new_simulations'], 0)

    def test_validator_cli_keeps_full_existing_checks(self):
        staged = self.root / 'staged.json'
        staged.write_text(json.dumps(self.index))
        output = self.root / 'Validation.json'
        argv = ['validate_pose_variants.py', '--manifest', str(self.manifest_path),
                '--index', str(staged), '--output', str(output)]
        with mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(validator, 'validate_variants', return_value=self.proof) as validate:
            validator.main()
        validate.assert_called_once_with(self.manifest, self.index, self.config)
        self.assertEqual(json.loads(output.read_text()), self.proof)

    def test_validator_cli_failure_does_not_write_pass_result(self):
        staged = self.root / 'staged.json'
        staged.write_text(json.dumps(self.index))
        output = self.root / 'Validation.json'
        argv = ['validate_pose_variants.py', '--manifest', str(self.manifest_path),
                '--index', str(staged), '--output', str(output)]
        with mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(validator, 'validate_variants', side_effect=ValueError('geometry mismatch')), \
                self.assertRaisesRegex(ValueError, 'geometry mismatch'):
            validator.main()
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
