import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from preview_check import PreviewCheck


class PreviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.script = self.root / 'check.py'
        self.script.write_text('pass\n')
        self.run = dict(worktree=str(self.root))
        self.head = 'a' * 40
        self.dirty = ''
        self.argv = [sys.executable, str(self.script)]
        self.w = SimpleNamespace(
            processing={'working_directory': str(self.root)},
            git=lambda *args, **kw: self.dirty if args[0] == 'status' else self.head,
            argv=lambda *args: list(self.argv), command=Mock())

    def test_success_is_one_use_and_never_persisted(self):
        token = PreviewCheck.verify(self.w, self.run, 'test')
        self.assertTrue(token.consume(self.w, self.run))
        self.assertFalse(token.consume(self.w, self.run))
        self.assertEqual(self.run, dict(worktree=str(self.root)))
        self.w.command.assert_called_once()

    def test_changed_revision_command_environment_interpreter_and_host_invalidate(self):
        for change in ('head', 'command', 'environment', 'interpreter', 'host'):
            with self.subTest(change=change):
                token = PreviewCheck.verify(self.w, self.run, 'test')
                if change == 'head': self.head = 'b' * 40
                if change == 'command': self.argv.append('changed')
                if change == 'host': self.script.write_text('raise RuntimeError()\n')
                with patch.dict(os.environ, {'PREVIEW_CHECK_TEST': 'changed'} if change == 'environment' else {}), \
                     patch('preview_check.sys.version', 'changed' if change == 'interpreter' else sys.version):
                    self.assertFalse(token.consume(self.w, self.run))

    def test_missing_failed_and_changed_during_check_never_attest(self):
        self.w.command.side_effect = ValueError('failed')
        with self.assertRaisesRegex(ValueError, 'failed'):
            PreviewCheck.verify(self.w, self.run, 'test')
        self.w.command.side_effect = lambda *a, **kw: setattr(self, 'head', 'b' * 40)
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            PreviewCheck.verify(self.w, self.run, 'test')
        self.w.command.side_effect = None
        token = PreviewCheck.verify(self.w, self.run, 'test')
        self.dirty = '?? untested.py'
        with self.assertRaisesRegex(ValueError, 'clean worktree'):
            token.consume(self.w, self.run)

