"""Regression tests for the read-only documentation checker."""
from pathlib import Path
import shutil
import hashlib
import json
import tempfile
import unittest
import yaml

from check_documentation import check

ROOT = Path(__file__).resolve().parents[1]


class DocumentationChecks(unittest.TestCase):
    def fixture(self, directory):
        for name in ('docs', 'examples'):
            shutil.copytree(ROOT/name, directory/name)
        for name in ('README.md', 'CHANGELOG.md'):
            shutil.copy2(ROOT/name, directory/name)

    def test_current_documentation_passes(self):
        result = check(ROOT, ROOT)
        self.assertEqual(result['errors'], [])

    def test_unknown_calibration_is_reported(self):
        with tempfile.TemporaryDirectory() as work:
            stage = Path(work)
            self.fixture(stage)
            path = stage/'examples/study_config.yaml'
            config = yaml.safe_load(path.read_text())
            config['dosing']['treatment_calibration'] = 'misspelled-hardware-id'
            path.write_text(yaml.safe_dump(config))
            result = check(stage, ROOT)
            self.assertTrue(any('Unknown example calibration' in error for error in result['errors']))

    def test_missing_gallery_asset_is_reported(self):
        with tempfile.TemporaryDirectory() as work:
            stage = Path(work)
            self.fixture(stage)
            (stage/'docs/assets/sal-optimization.png').unlink()
            result = check(stage, ROOT)
            self.assertTrue(any('Missing gallery asset' in error for error in result['errors']))

    def test_unknown_cli_flag_is_reported(self):
        with tempfile.TemporaryDirectory() as work:
            stage = Path(work)
            self.fixture(stage)
            reference = stage/'docs/CONFIGURATION.md'
            reference.write_text(reference.read_text()+'\n`--not-a-real-option`\n')
            result = check(stage, ROOT)
            self.assertTrue(any('unknown driver flags' in error for error in result['errors']))

    def test_corrupt_pdf_is_reported_even_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as work:
            stage = Path(work)
            self.fixture(stage)
            asset = stage/'docs/assets/sal-dosing.pdf'
            asset.write_bytes(b'not a PDF')
            manifest_path = stage/'docs/assets/manifest.json'
            manifest = json.loads(manifest_path.read_text())
            for item in manifest['assets']:
                if item['file']==asset.name:
                    item['sha256']=hashlib.sha256(asset.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            result = check(stage, ROOT)
            self.assertTrue(any('Invalid PDF envelope' in error for error in result['errors']))

    def test_changed_movie_fingerprint_is_reported(self):
        with tempfile.TemporaryDirectory() as work:
            stage = Path(work)
            self.fixture(stage)
            with (stage/'docs/assets/sal-search-replay.mp4').open('ab') as handle:
                handle.write(b'changed')
            result = check(stage, ROOT)
            self.assertTrue(any('Asset fingerprint mismatch' in error for error in result['errors']))


if __name__ == '__main__':
    unittest.main()
