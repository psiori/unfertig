"""Update action safety; no fixture touches the installed host or its board."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from instance_maintenance import Maintenance
from maintenance_update import UpdateRequest


class UpdateRequestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='unfertig-update-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('scripts/run_uv.sh', 'scripts/request_tool_update.py', 'state/unfertig/config/launch.json'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        (self.root / 'state/unfertig/config/launch.json').write_text(json.dumps(dict(cooperative_restart=True, update=dict(branch='main'))))
        self.env = {**os.environ, 'UM_PROJECT_ROOT': str(self.root), 'UM_TOOL_DIR': str(self.root / 'tools/unfertig'),
                    'UM_RESTART_SESSION': 'fixture', 'UM_RESTART_REQUEST': str(self.root / 'restart.json'),
                    'UM_RESTART_STATUS': str(self.root / 'status.json')}
        self.updater = UpdateRequest(self.env)
        self.target = 'a' * 40

    def receipt(self, **fields):
        event = self.updater.event(self.target)
        value = dict(schema_version=1, id=event, tool='unfertig', commit=self.target,
                     repository='', branch='', state='pending', extension={'keep': True})
        value.update(fields)
        path = self.root / '.local/tools/update-requests/unfertig' / (event + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return value

    def test_supported_cli_only_stable_identity_and_truthful_receipts(self):
        with patch('maintenance_update.subprocess.run') as command:
            command.side_effect = lambda *a, **k: (self.receipt(), SimpleNamespace(returncode=0))[1]
            first = self.updater.submit(self.target)
            again = self.updater.submit(self.target)
        self.assertEqual(first, again)
        args, kwargs = command.call_args
        self.assertEqual(args[0], ['sh', str(self.root / 'scripts/run_uv.sh'),
                         str(self.root / 'scripts/request_tool_update.py'), '--tool', 'unfertig', '--event', first['id']])
        self.assertEqual(kwargs['timeout'], 10)
        self.assertEqual(kwargs['env']['UNFERTIG_PUBLISHED_COMMIT'], self.target)
        self.assertFalse((self.root / 'restart.json').exists(), 'Only the host writes the restart request')
        for state in ('failed', 'applying', 'complete'):
            receipt = self.receipt(state=state, message='retained outcome')
            with patch('maintenance_update.subprocess.run', return_value=SimpleNamespace(returncode=0)):
                self.assertEqual(self.updater.submit(self.target), receipt)
        self.assertEqual(UpdateRequest(self.env).view(self.target)['request']['state'], 'complete')

    def test_unknown_delivery_reuses_durable_request_without_claiming_installation(self):
        receipt = self.receipt()
        with patch('maintenance_update.subprocess.run', side_effect=subprocess.TimeoutExpired('fixture', 10)):
            with self.assertRaisesRegex(ValueError, 'uncertain'):
                self.updater.submit(self.target)
        self.assertEqual(self.updater.view(self.target)['request'], receipt)
        with patch('maintenance_update.subprocess.run', return_value=SimpleNamespace(returncode=1, stderr='Conflict', stdout='')):
            with self.assertRaisesRegex(ValueError, 'Conflict'):
                self.updater.submit(self.target)
        self.assertEqual(self.updater.retained(self.target), receipt)

    def test_preview_missing_host_invalid_config_and_receipt_fail_closed(self):
        self.assertFalse(UpdateRequest({}).availability()[0])
        self.assertFalse(UpdateRequest({**self.env, 'UM_TOOL_DIR': str(self.root / 'feature')}).availability()[0])
        for config in ({}, {'cooperative_restart': True, 'update': None}, {'cooperative_restart': True, 'update': {'branch': 'dev'}}):
            (self.root / 'state/unfertig/config/launch.json').write_text(json.dumps(config))
            with patch('maintenance_update.subprocess.run') as command:
                with self.assertRaises(ValueError):
                    self.updater.submit(self.target)
                command.assert_not_called()
        for fields in ({'schema_version': 2}, {'id': 'other'}, {'state': 'unknown'}, {'commit': 'b' * 40}):
            self.receipt(**fields)
            with self.assertRaisesRegex(ValueError, 'Invalid retained'):
                self.updater.retained(self.target)
        for target in ('', None, '--other', '../elsewhere'):
            with self.assertRaises(ValueError):
                self.updater.event(target)

    def test_action_checks_fresh_currency_and_preserves_cooperative_protocol(self):
        store = SimpleNamespace(context={}, git=False)
        server = SimpleNamespace(store=store, workflow=None, processing=None, shutdown=Mock())
        maintenance = Maintenance(server, self.env)
        maintenance.currency = Mock()
        maintenance.currency.view.return_value = dict(state='outdated', target_commit=self.target)
        maintenance.updater = Mock()
        maintenance.updater.view.return_value = {}
        with self.assertRaisesRegex(ValueError, 'changed or is unconfirmed'):
            maintenance.action(dict(action='update_restart', target_commit='b' * 40))
        maintenance.updater.submit.assert_not_called()
        value = maintenance.action(dict(action='update_restart', target_commit=self.target))
        maintenance.updater.submit.assert_called_once_with(self.target)
        self.assertEqual(value['phase'], 'idle')
        self.assertFalse(value['pending'])
        server.shutdown.assert_not_called()
        self.assertFalse((self.root / 'restart.json').exists())
        maintenance.pending = True
        maintenance.action(dict(action='update_restart', target_commit=self.target))
        maintenance.updater.submit.assert_called_once()
        maintenance.currency.view.return_value['state'] = 'unknown'
        with self.assertRaises(ValueError):
            maintenance.action(dict(action='update_restart', target_commit=self.target))


if __name__ == '__main__':
    unittest.main()
