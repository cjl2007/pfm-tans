"""Portable-export containment checks supported by the Python 3.7 driver."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


directory = Path(os.environ.get('TEST_EXPORT_RUNTIME_DIR', Path(__file__).parent))
spec = importlib.util.spec_from_file_location(
    'export_path_runtime_publisher', directory / 'publish_operator_export.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class ExportPathRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.root = self.parent / 'export'
        self.root.mkdir()
        (self.root / 'nested').mkdir()
        (self.root / 'image.png').write_bytes(b'png fixture')
        (self.root / 'index.html').write_text('<a href="nested/view.html">View</a>')
        (self.root / 'nested/view.html').write_text(
            '<img src="../image.png"><a href="../index.html?view=pose#top">Back</a>'
            '<a href="#peak">Peak</a>')
        self.outside = self.parent / 'outside.png'
        self.outside.write_bytes(b'outside')
        self.refresh_manifest()

    def refresh_manifest(self, extra=None):
        manifest = {'schema_version': 'tans-operator-export-v1', 'subject': 'Test',
                    'clinical_clearance': False, 'targets': [], 'dose_pairings': [],
                    'files': {}}
        for path in self.root.rglob('*'):
            if path.is_file() and path.name != 'ExportManifest.json':
                manifest['files'][path.relative_to(self.root).as_posix()] = {
                    'sha256': publisher.sha(path), 'bytes': path.stat().st_size}
        if extra:
            manifest['files'].update(extra)
        (self.root / 'ExportManifest.json').write_text(json.dumps(manifest))

    def test_relative_to_compatibility_matches_containment(self):
        self.assertTrue(publisher.is_relative_to(self.root, self.root))
        self.assertTrue(publisher.is_relative_to(self.root / 'nested/file', self.root))
        self.assertFalse(publisher.is_relative_to(self.outside, self.root))
        self.assertFalse(publisher.is_relative_to(self.parent / 'export-other/image.png', self.root))

    def test_inside_resolves_safe_paths_and_rejects_escape(self):
        self.assertEqual(publisher.inside(self.root, 'nested/../image.png'), self.root / 'image.png')
        for path in ('../outside.png', str(self.outside), '../export-other/image.png'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'escapes export'):
                publisher.inside(self.root, path)

    def test_closed_relative_links_validate_in_base_python(self):
        result = publisher.validate_package(self.root)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['local_links_checked'], 4)

    def test_html_traversal_is_rejected_even_when_external_file_exists(self):
        for url in ('../outside.png', '%2e%2e/outside.png', '..%2Foutside.png'):
            with self.subTest(url=url):
                (self.root / 'index.html').write_text('<img src="' + url + '">')
                self.refresh_manifest()
                with self.assertRaisesRegex(ValueError, 'Broken or external'):
                    publisher.validate_package(self.root)

    def test_nonportable_urls_remain_rejected(self):
        for url in (str(self.outside), 'https://example.org/image.png',
                    '//example.org/image.png', 'file:///tmp/image.png',
                    'javascript:alert(1)'):
            with self.subTest(url=url):
                (self.root / 'index.html').write_text('<a href="' + url + '">Link</a>')
                self.refresh_manifest()
                with self.assertRaisesRegex(ValueError, 'Nonportable URL'):
                    publisher.validate_package(self.root)

    def test_symlinks_are_rejected_even_within_export(self):
        for destination in (self.outside, self.root / 'image.png'):
            with self.subTest(destination=destination):
                alias = self.root / 'alias.png'
                alias.symlink_to(destination)
                self.refresh_manifest()
                with self.assertRaisesRegex(ValueError, 'contains a symlink'):
                    publisher.validate_package(self.root)
                alias.unlink()
        alias = self.root / 'outside-directory'
        alias.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'escapes export'):
            publisher.inside(self.root, 'outside-directory/outside.png')

    def test_manifest_cannot_reference_external_artifact(self):
        self.refresh_manifest({'../outside.png': {'sha256': publisher.sha(self.outside),
                                                  'bytes': self.outside.stat().st_size}})
        with self.assertRaisesRegex(ValueError, 'escapes export'):
            publisher.validate_package(self.root)


if __name__ == '__main__':
    unittest.main()
