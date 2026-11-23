"""Incomplete scans must never look like successful source checks."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import quarantine_check as checker


class IncompleteScan(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)

    def check_error(self, name=None, source=''):
        if name:
            (self.root / name).write_text(source)
        with self.assertRaises(checker.ScanError):
            checker.scan(self.root)
        result = subprocess.run([sys.executable, checker.__file__, str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('NOT ASSESSED', result.stderr)

    def test_missing_path(self):
        with self.assertRaises(checker.ScanError):
            checker.scan(self.root / 'absent')

    def test_empty_directory(self):
        self.check_error()

    def test_unsupported_only(self):
        self.check_error('README.md', '# not code')

    def test_invalid_python(self):
        self.check_error('agent.py', 'import anthropic\ndef broken(')

    def test_invalid_notebook_json(self):
        self.check_error('agent.ipynb', '{')

    def test_invalid_notebook_shape(self):
        self.check_error('agent.ipynb', json.dumps({'cells': [1]}))

    def test_notebook_magic_is_not_silently_skipped(self):
        self.check_error('agent.ipynb', json.dumps({'cells': [{'cell_type': 'code', 'source': ['%run agent.py']}]}))

    def test_unreadable_file(self):
        (self.root / 'agent.py').write_text('pass')
        with mock.patch.object(pathlib.Path, 'read_text', side_effect=PermissionError):
            with self.assertRaises(checker.ScanError):
                checker.scan(self.root)

    def test_invalid_utf8_is_not_silently_replaced(self):
        (self.root / 'agent.py').write_bytes(b'\xff')
        with self.assertRaises(checker.ScanError):
            checker.scan(self.root)

    def test_valid_non_llm_source_is_completed(self):
        (self.root / 'helper.py').write_text('def add(a, b):\n return a + b\n')
        self.assertEqual(checker.scan(self.root), [])

    def test_multiple_targets_keep_incomplete_status(self):
        clean = self.root / 'helper.py'
        clean.write_text('pass')
        result = subprocess.run([sys.executable, checker.__file__, str(clean), str(self.root / 'missing')], capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_symlink_source_is_not_followed(self):
        (self.root / 'real.py').write_text('pass')
        (self.root / 'link.py').symlink_to(self.root / 'real.py')
        with self.assertRaises(checker.ScanError):
            checker.scan(self.root)
