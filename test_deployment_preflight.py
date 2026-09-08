"""Managed migration detection exercises real candidate processes on disposable data."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from deployment_preflight import assess, command, files, require_unchanged
from server import validate
from storage import BoardStore
from test_server import fixture
from versions import FORMAT_VERSION


class DeploymentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='deployment-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.context = self.root / 'wrapper'
        self.context.mkdir()
        self.candidate = self.root / 'candidate'
        self.installed = self.context / 'tools/unfertig'
        for app in (self.candidate, self.installed):
            app.mkdir(parents=True)
            for path in Path(__file__).parent.glob('*.py'):
                shutil.copy2(path, app / path.name)
            for name in ('categories.json', 'agent_advice.json'):
                shutil.copy2(Path(__file__).parent / name, app / name)
            command(['git', 'init', '-q'], app)
            command(['git', 'config', 'user.name', 'Test'], app)
            command(['git', 'config', 'user.email', 'test@example.invalid'], app)
            command(['git', 'add', '.'], app)
            command(['git', 'commit', '-qm', 'Disposable runtime'], app)
        self.data = self.context / 'state/unfertig/data'
        self.data.mkdir(parents=True)
        self.config = self.context / 'state/unfertig/config/config.json'
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps(dict(format_version=FORMAT_VERSION, mode='embedded',
                                               repository='../../..', data='../data/data.json',
                                               extension={'keep': ['all', 'values']})))
        (self.data / 'data.json').write_text(json.dumps(fixture()))
        store = BoardStore(self.data / 'data.json', validate, git=False, config=self.config)
        store.acquire()
        try:
            store.initialize()
        finally:
            store.close()
        (self.data / '.receipts').mkdir(exist_ok=True)
        (self.data / '.receipts/request-1234567890.json').write_text(json.dumps(dict(
            format_version=FORMAT_VERSION, request_id='request-1234567890', fingerprint='original', assigned=[],
            extension={'keep': True})))

    def downgrade(self):
        for root in (self.data, self.config.parent):
            for path in root.rglob('*.json'):
                # Historical backups remain immutable evidence.
                if 'backup' in path.name:
                    continue
                value = json.loads(path.read_bytes())
                value['format_version'] = '1.8.0'
                path.write_text(json.dumps(value))
        path = self.installed / 'versions.py'
        path.write_text(path.read_text().replace(f"FORMAT_VERSION = {FORMAT_VERSION!r}", "FORMAT_VERSION = '1.8.0'"))
        command(['git', 'add', '.'], self.installed)
        command(['git', 'commit', '-qm', 'Simulate older writer'], self.installed)

    def test_current_update_changes_no_live_bytes(self):
        before = files(self.data), files(self.config.parent)
        result = assess(self.candidate, self.context)
        self.assertEqual(result['state'], 'unchanged_storage')
        self.assertEqual((files(self.data), files(self.config.parent)), before)

    def test_18_to_current_preserves_live_board_receipts_and_config(self):
        self.downgrade()
        before = files(self.data), files(self.config.parent)
        result = assess(self.candidate, self.context)
        self.assertEqual(result['state'], 'migration_required')
        self.assertEqual(result['current_formats'], ['1.8.0'])
        self.assertEqual(result['target_format'], FORMAT_VERSION)
        self.assertEqual((files(self.data), files(self.config.parent)), before)
        with self.assertRaisesRegex(ValueError, 'Ordinary Merge & restart cannot'):
            require_unchanged(self.candidate, self.context)

    def test_validation_failure_preserves_installed_and_live_files(self):
        self.downgrade()
        before = files(self.data), files(self.config.parent), files(self.installed)
        path = self.candidate / 'versions.py'
        path.write_text(path.read_text().replace("# New coordinator phases require newer-writer protection; no permission grant.",
                                               "value['extension'] = 'destroyed'"))
        command(['git', 'add', '.'], self.candidate)
        command(['git', 'commit', '-qm', 'Invalid migration candidate'], self.candidate)
        with self.assertRaisesRegex(ValueError, 'original content'):
            assess(self.candidate, self.context)
        self.assertEqual((files(self.data), files(self.config.parent), files(self.installed)), before)

    def test_pending_history_is_retained_without_recovery(self):
        pending = self.data / '.history-pending.json'
        pending.write_text('{"retained":"exactly"}')
        before = files(self.data)
        with self.assertRaisesRegex(ValueError, 'pending board transactions/history'):
            assess(self.candidate, self.context)
        self.assertEqual(files(self.data), before)

    def test_absolute_live_config_is_never_given_to_candidate(self):
        value = json.loads(self.config.read_bytes())
        value['data'] = str(self.data / 'data.json')
        self.config.write_text(json.dumps(value))
        before = files(self.data), files(self.config.parent)
        with self.assertRaisesRegex(ValueError, 'nonportable paths'):
            assess(self.candidate, self.context)
        self.assertEqual((files(self.data), files(self.config.parent)), before)

    def test_config_only_migration_reports_older_config_format(self):
        value = json.loads(self.config.read_bytes())
        value['format_version'] = '1.8.0'
        self.config.write_text(json.dumps(value))
        result = assess(self.candidate, self.context)
        self.assertEqual(result['state'], 'migration_required')
        self.assertEqual(result['current_formats'], ['1.8.0', FORMAT_VERSION])

    def test_normal_restart_does_not_stop_on_migration(self):
        import workflow_support
        argv = ['workflow_support.py', 'unfertig', 'restart', '--repository', str(self.candidate),
                '--context', str(self.context)]
        self.downgrade()
        with patch('sys.argv', argv), patch.object(workflow_support, 'run') as run, \
                self.assertRaisesRegex(ValueError, 'Migration required'):
            workflow_support.main()
        run.assert_not_called()
