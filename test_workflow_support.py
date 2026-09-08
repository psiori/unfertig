import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import workflow_support


class ManagedRestartTests(unittest.TestCase):
    def test_legacy_recipe_refuses_before_preflight_stop_or_start(self):
        for extra in ([], ['--startup-timeout','1200']):
            argv=['workflow_support.py','unfertig','restart','--repository','/tmp/artifact','--context','/tmp/wrapper',*extra]
            with patch('sys.argv',argv), patch('deployment_preflight.assess') as assess, patch.object(workflow_support,'run') as run, patch.object(workflow_support,'start_managed') as start:
                with self.assertRaisesRegex(ValueError,'Legacy in-transaction restart is disabled'):
                    workflow_support.main()
                assess.assert_not_called();run.assert_not_called();start.assert_not_called()

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
