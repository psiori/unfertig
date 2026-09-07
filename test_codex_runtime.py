from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codex_runtime import resolve_executable
from processing import settings


class CodexDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        home = patch('codex_runtime.Path.home', return_value=self.root)
        home.start()
        self.addCleanup(home.stop)

    def test_explicit_path_wins(self):
        with patch('codex_runtime.shutil.which', side_effect=lambda p: p):
            self.assertEqual(resolve_executable('/custom/codex'), '/custom/codex')

    def test_stale_mac_path_falls_back_to_linux_path(self):
        with patch('codex_runtime.shutil.which', side_effect=lambda p: '/linux/bin/codex' if p == 'codex' else None):
            self.assertEqual(resolve_executable('/Applications/ChatGPT.app/Contents/Resources/codex'), '/linux/bin/codex')

    def test_known_installs_without_path(self):
        for platform, location in [('linux', '.local/share/mise/installs/codex/latest/bin/codex'),
                                   ('linux', '.local/bin/codex'),
                                   ('darwin', 'Applications/Codex.app/Contents/Resources/codex'),
                                   ('darwin', 'Applications/ChatGPT.app/Contents/Resources/codex')]:
            target = str(self.root / location)
            with self.subTest(platform=platform, location=location), patch('codex_runtime.sys.platform', platform), patch('codex_runtime.shutil.which', side_effect=lambda p: p if p == target else None):
                self.assertEqual(resolve_executable('/missing/codex'), target)

    def test_missing_then_installed_and_non_executable(self):
        target = self.root / 'codex'
        import shutil
        which = shutil.which
        with patch('codex_runtime.shutil.which', side_effect=lambda p: which(p) if p == str(target) else None):
            self.assertIsNone(resolve_executable(str(target)))
            target.write_text('#!/bin/sh\nexit 0\n')
            self.assertIsNone(resolve_executable(str(target)))
            target.chmod(0o755)
            self.assertEqual(resolve_executable(str(target)), str(target))

    def test_startup_resolution_does_not_mutate_configuration(self):
        configured = {'executable': '/missing/mac/codex'}
        with patch('codex_runtime.shutil.which', return_value='/host/codex'):
            result = settings(configured, self.root, self.root, self.root)
        self.assertEqual(result['executable'], '/host/codex')
        self.assertEqual(configured['executable'], '/missing/mac/codex')
