#!/usr/bin/env python3

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import numpy as np


MODULE_PATH = Path(__file__).with_name("prepare_subject.py")
SPEC = importlib.util.spec_from_file_location("prepare_subject", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrepareSubjectTests(unittest.TestCase):
    def test_missing_inverse_is_derived_from_forward_with_fsl(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            transforms = root / 'anat/T1w/xfms'
            transforms.mkdir(parents=True)
            matrix = np.eye(4)
            matrix[:3, 3] = [12, -25, 40]
            np.savetxt(transforms / 'acpc.mat', matrix)
            output = root / 'surfaces'
            output.mkdir()
            def convert(command, label):
                self.assertEqual(command[1], '-omat')
                self.assertEqual(command[3], '-inverse')
                np.savetxt(command[2], np.linalg.inv(np.loadtxt(command[4])))
            with mock.patch.object(MODULE, 'run', side_effect=convert):
                inverse = MODULE.acpc_inverse_affine(root, output)
            np.testing.assert_allclose(np.loadtxt(inverse) @ matrix, np.eye(4))
            self.assertFalse((transforms / 'acpc_inv.mat').exists())
            np.savetxt(transforms / 'acpc_inv.mat', np.eye(4))
            with self.assertRaisesRegex(ValueError, 'does not invert'):
                MODULE.acpc_inverse_affine(root, output)

    def test_resume_rejects_incomplete_or_differently_sourced_charm(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / 'charm_log.html'
            log.write_text('DEBUG: options: SUBJECT acpc_t1.nii.gz acpc_t2.nii.gz\nINFO: charm run finished: now')
            with self.assertRaisesRegex(ValueError, 'exact native'):
                MODULE.validate_completed_native_charm(root, ['charm', 'SUBJECT', 'native_t1', 'native_t2'])

    def test_inflated_surface_uses_same_native_transform(self):
        self.assertIn('inflated', MODULE.SURFACE_TYPES)

    def test_unverified_and_modified_scalp_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            scalp = Path(directory) / 'Skin.surf.gii'
            scalp.write_bytes(b'verified surface fixture')
            manifest = {'resolved_subject_adapter': {'skin': str(scalp)},
                        'source': {'head_mesh_sha256': 'source-fixture'}}
            with self.assertRaisesRegex(ValueError, 'unverified'):
                MODULE.validate_scalp_manifest(manifest)
            proof = {'schema_version': 'tans-scalp-extraction-v3',
                     'surface_scope': 'exterior_skin',
                     'status': 'validated', 'element_type': 2, 'element_tag': 1005,
                     'source_head_mesh_sha256': 'source-fixture',
                     'surface_sha256': MODULE.sha256(scalp)}
            scalp.with_suffix('.gii.json').write_text(json.dumps(proof))
            MODULE.validate_scalp_manifest(manifest)
            proof['schema_version'] = 'tans-scalp-extraction-v2'
            scalp.with_suffix('.gii.json').write_text(json.dumps(proof))
            with self.assertRaisesRegex(ValueError, 'exterior-only'):
                MODULE.validate_scalp_manifest(manifest)
            proof['schema_version'] = 'tans-scalp-extraction-v3'
            scalp.with_suffix('.gii.json').write_text(json.dumps(proof))
            scalp.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'changed after validation'):
                MODULE.validate_scalp_manifest(manifest)

    def test_native_charm_command_matches_validated_invocation(self):
        command = MODULE.build_charm_command(
            Path("/simnibs/bin/charm"), "CIR002",
            Path("/subject/anat/T1w/T1w.nii.gz"),
            Path("/subject/anat/T2w/T2w.nii.gz"))
        self.assertEqual(command, [
            "/simnibs/bin/charm", "CIR002",
            "/subject/anat/T1w/T1w.nii.gz",
            "/subject/anat/T2w/T2w.nii.gz", "--forceqform"])

    def test_acpc_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.build_charm_command(
                Path("/simnibs/bin/charm"), "CIR002",
                Path("/subject/T1w_acpc_dc_restore.nii.gz"),
                Path("/subject/T2w.nii.gz"))

    def test_registered_t2_input_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.build_charm_command(
                Path("/simnibs/bin/charm"), "CNen001",
                Path("/subject/anat/T1w/T1w.nii.gz"),
                Path("/subject/anat/T2w/T2_reg.nii.gz"))


if __name__ == "__main__":
    unittest.main()
