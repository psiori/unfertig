import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import workflow_support


class ManagedRestartTests(unittest.TestCase):
    def restart(self, *extra, revision='a'*40, start_error=None):
        calls = []
        argv = ['workflow_support.py', 'unfertig', 'restart',
                '--repository', '/tmp/artifact', '--context', '/tmp/wrapper', *extra]
        with patch('deployment_preflight.assess', return_value={}), patch('sys.argv', argv), patch.object(workflow_support, 'run', lambda argv, cwd: calls.append(argv)), \
             patch.object(workflow_support, 'start_managed', side_effect=start_error) as start, \
             patch.object(workflow_support.subprocess, 'check_output', side_effect=['a'*40, revision, '']):
            workflow_support.main()
        return calls, start.call_args.args

    def test_wait_budget_is_separate_from_stop(self):
        calls, args = self.restart()
        self.assertEqual(calls[0][-1], '60')
        self.assertEqual(args, (Path('/tmp/wrapper'), 900))

    def test_explicit_startup_budget(self):
        _, args = self.restart('--startup-timeout', '1200')
        self.assertEqual(args[1], 1200)

    def test_actual_start_failure_is_not_success(self):
        with self.assertRaises(ValueError):
            self.restart(start_error=ValueError('start failed'))

    def test_wrong_installed_revision_still_fails(self):
        with self.assertRaisesRegex(ValueError, 'did not install'):
            self.restart(revision='b'*40)

    @staticmethod
    def status(phase, session='one', tools=None, code=0):
        state = dict(phase=phase, run_id=session, tools=tools if tools is not None else [dict(name='unfertig', state='running')])
        return subprocess.CompletedProcess([], code, json.dumps(state), '')

    def test_client_timeout_then_slow_validation_eventually_succeeds(self):
        initial = subprocess.CompletedProcess([], 1, '', 'Still waiting for tools')
        with patch.object(workflow_support.subprocess, 'run', side_effect=[initial, self.status('starting'), self.status('running')]) as run, \
             patch.object(workflow_support.time, 'sleep'), \
             patch.dict(workflow_support.os.environ, {'VIRTUAL_ENV':'wrong-environment'}):
            workflow_support.start_managed(Path('/tmp/wrapper'), 900)
        self.assertEqual(run.call_count, 3)
        self.assertIn('--status', run.call_args.args[0])
        self.assertNotIn('VIRTUAL_ENV', run.call_args.kwargs['env'])

    def test_validation_failure_is_not_hidden_by_polling(self):
        with patch.object(workflow_support.subprocess, 'run', return_value=self.status('failed', code=1)), \
             self.assertRaisesRegex(ValueError, 'startup failed'):
            workflow_support.start_managed(Path('/tmp/wrapper'), 900)

    def test_running_without_board_is_not_success(self):
        with patch.object(workflow_support.subprocess, 'run', return_value=self.status('running', tools=[])), \
             self.assertRaisesRegex(ValueError, 'without the Unfertig'):
            workflow_support.start_managed(Path('/tmp/wrapper'), 900)

    def test_replaced_session_is_not_accepted(self):
        with patch.object(workflow_support.subprocess, 'run', side_effect=[self.status('starting'), self.status('running', session='two')]), \
             patch.object(workflow_support.time, 'sleep'), self.assertRaisesRegex(ValueError, 'session changed'):
            workflow_support.start_managed(Path('/tmp/wrapper'), 900)

    def test_wait_expiry_reports_pending_not_stopped(self):
        with patch.object(workflow_support.subprocess, 'run', return_value=self.status('starting')), \
             patch.object(workflow_support.time, 'monotonic', side_effect=[0, 901]), \
             self.assertRaisesRegex(ValueError, 'still pending.*supervisor may continue'):
            workflow_support.start_managed(Path('/tmp/wrapper'), 900)
