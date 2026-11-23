import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_check


class WorkflowBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name) / 'caller'
        self.root.mkdir()

    def test_root_is_allowed(self):
        self.assertEqual(run_check.resolve_target(self.root, '.'), self.root.resolve())

    def test_parent_and_absolute_target_refused(self):
        for target in ['..', str(self.root.resolve())]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                run_check.resolve_target(self.root, target)

    def test_symlink_escape_refused(self):
        (self.root / 'escape').symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            run_check.resolve_target(self.root, 'escape')

    def test_caller_module_cannot_shadow_trusted_stdlib(self):
        # A caller may contain an adversarial json.py; it must be read, never imported.
        (self.root / 'json.py').write_text('raise RuntimeError("caller code executed")\n')
        (self.root / 'agent.py').write_text('import anthropic\nuser_prompt = f"Review {policy_text}"\n')
        result = subprocess.run([sys.executable, '-I', run_check.__file__, str(self.root), '.'],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn('RuntimeError:', result.stderr)
