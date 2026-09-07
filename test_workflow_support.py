import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import workflow_support


class ManagedRestartTests(unittest.TestCase):
    def restart(self, *extra, revision='a'*40, start_error=None):
        calls = []
        def command(argv, cwd):
            calls.append(argv)
            if str(argv[1]).endswith('start_tools.sh'):
                # Model validation taking 87 seconds without sleeping in tests.
                if int(argv[-1]) < 87:
                    raise subprocess.CalledProcessError(1, argv)
                if start_error:
                    raise start_error
        argv = ['workflow_support.py', 'unfertig', 'restart',
                '--repository', '/tmp/artifact', '--context', '/tmp/wrapper', *extra]
        with patch('sys.argv', argv), patch.object(workflow_support, 'run', command), \
             patch.object(workflow_support.subprocess, 'check_output',
                          side_effect=['a'*40, revision, '']):
            workflow_support.main()
        return calls

    def test_validation_longer_than_a_minute_finishes_before_revision_check(self):
        calls = self.restart()
        self.assertEqual(calls[0][-1], '60')  # stop remains separately bounded
        self.assertIn(['sh', str(Path('/tmp/wrapper/start_tools.sh')), '--timeout', '900'], calls)

    def test_explicit_startup_budget(self):
        calls = self.restart('--startup-timeout', '1200')
        self.assertEqual(calls[1][-1], '1200')

    def test_actual_start_failure_is_not_success(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.restart(start_error=subprocess.CalledProcessError(1, ['start']))

    def test_wrong_installed_revision_still_fails(self):
        with self.assertRaisesRegex(ValueError, 'did not install'):
            self.restart(revision='b'*40)
