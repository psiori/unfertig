"""Data compatibility tests never use the active board or real remotes."""
import copy
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
import uuid
from unittest.mock import patch

import storage
from storage import BoardStore, Conflict, digest
from server import validate
from versions import FORMAT_VERSION, PROTOCOL_VERSION, VersionError, inspect, migrate, semantic
from test_server import fixture


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='unfertig-version-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'data' / 'data.json'
        self.path.parent.mkdir(); (self.path.parent / 'todos').mkdir()
        self.config = self.root / 'config.json'
        self.write(self.config, {'data':'data/data.json','extension':{'keep':True}})
        data = fixture()
        self.write(self.path, {'schema_version':2, 'ideas':data['ideas'], 'extension':{'format_version':'user-extension-value'}})
        self.todo = self.path.parent / 'todos/T0001.json'
        self.write(self.todo, data['todos'][0])
        self.store = BoardStore(self.path, validate, git=False, config=self.config)
        self.store.acquire(); self.addCleanup(self.store.close)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2)+'\n')

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file() and p.name not in ('.server.lock', '.operation.lock', '.service.lock')}

    def edit(self, **fields):
        record = self.store.read()[0]['todos'][0]
        old = digest(record); record.update(fields)
        return dict(request_id=uuid.uuid4().hex, protocol_version=PROTOCOL_VERSION,
                    changes=[dict(collection='todos',id='T0001',revision=old,record=record)])

    def test_sequential_legacy_migration_and_defaults(self):
        original = fixture()['todos'][0]
        partial = copy.deepcopy(original)
        for key in ('group','tags','pr_url','commit_url','commit_hash','updated_at','source_ideas','priority','status'):
            partial.pop(key)
        self.write(self.todo, partial)
        calls = []
        import versions
        first, second = versions.MIGRATIONS['0.0.0'], versions.MIGRATIONS['1.0.0']
        def one(value, kind): calls.append((kind,'first')); return first(value,kind)
        def two(value, kind): calls.append((kind,'second')); return second(value,kind)
        with patch.dict(versions.MIGRATIONS, {'0.0.0':one,'1.0.0':two}): self.store.initialize()
        todo = self.store.read()[0]['todos'][0]
        self.assertEqual(todo['format_version'], FORMAT_VERSION)
        self.assertEqual(todo['priority'],'normal'); self.assertEqual(todo['tags'],[])
        self.assertEqual(todo['updated_at'], original['date_entered'])
        self.assertEqual(todo['author'],original['author'])
        self.assertIn(('todo','first'), calls); self.assertIn(('todo','second'), calls)
        self.assertEqual(json.loads(self.path.read_text())['ideas'], fixture()['ideas'])
        self.assertEqual(json.loads(self.config.read_text())['extension'], {'keep':True})
        self.assertEqual(json.loads(self.config.read_text())['format_version'], FORMAT_VERSION)
        before = self.files(); self.store.initialize(); self.assertEqual(self.files(),before)

    def test_discovery_step_defaults_preserves_explicit_settings_and_old_writer_guard(self):
        import versions
        for patterns in (None, [], ['../um-?/config.json']):
            config = dict(format_version='1.10.0', mode='aggregation', sources=[], extension={'keep': True})
            if patterns is not None:
                config['search_paths'] = patterns
            result = versions.MIGRATIONS['1.10.0'](copy.deepcopy(config), 'config')
            self.assertEqual(result['format_version'], '1.11.0')
            self.assertEqual(result['search_paths'], patterns or [])
            self.assertEqual(result['extension'], config['extension'])
            self.assertEqual(inspect(result, supported='1.10.0')[0], 'read_only')
            self.assertEqual(migrate(result, 'config'), dict(result, format_version=FORMAT_VERSION))

    def test_discovery_migration_interruption_retains_config_and_receipt(self):
        self.store.initialize()
        for path in (self.path, self.todo, self.config):
            value = json.loads(path.read_bytes())
            value['format_version'] = '1.10.0'
            if path == self.config:
                value.update(mode='aggregation', search_paths=['../um-*/config.json'])
            self.write(path, value)
        receipt = dict(format_version='1.10.0', request_id='a'*16, fingerprint='same-request', assigned=[])
        receipt_path = self.store.receipts / ('a'*16 + '.json')
        self.write(receipt_path, receipt)
        originals = self.store.read()[0]['ideas']
        real = storage.atomic
        def interrupt(path, raw):
            if path.resolve() == self.config.resolve():
                raise OSError('Interrupted config migration')
            return real(path, raw)
        with patch('storage.atomic', side_effect=interrupt), self.assertRaises(OSError):
            self.store.initialize()
        self.assertTrue(self.store.journal.exists())
        self.store.initialize()
        self.assertEqual(self.store.read()[0]['ideas'], originals)
        self.assertEqual(semantic(json.loads(receipt_path.read_bytes())), semantic(receipt))
        self.assertEqual(json.loads(self.config.read_bytes())['search_paths'], ['../um-*/config.json'])
        before = self.files(); self.store.initialize(); self.assertEqual(self.files(), before)

    def test_missing_required_content_is_not_fabricated(self):
        data = json.loads(self.todo.read_text()); data.pop('author'); self.write(self.todo,data)
        before = self.files()
        with self.assertRaises(ValueError): self.store.initialize()
        self.assertEqual(self.files(),before)

    def test_mixed_supported_versions_and_future_build(self):
        data = json.loads(self.todo.read_text()); data['format_version']='1.0.0'; self.write(self.todo,data)
        header = json.loads(self.path.read_text()); header['format_version']='1.13.9'; self.write(self.path,header)
        self.store.initialize()
        self.assertEqual(json.loads(self.todo.read_text())['format_version'],FORMAT_VERSION)
        self.assertEqual(json.loads(self.path.read_text())['format_version'],'1.13.9')
        self.assertFalse(self.store.snapshot()['compatibility']['read_only'])
        self.assertTrue(self.store.snapshot()['compatibility']['warnings'])

    def test_future_major_any_active_file_refuses_without_writes(self):
        locations = [self.path,self.todo,self.config,self.store.pending,self.store.journal,self.store.receipts/('a'*16+'.json')]
        originals = self.files()
        for path in locations:
            with self.subTest(path=path):
                previous = path.read_bytes() if path.exists() else None
                data = json.loads(previous) if previous else {}
                data['format_version']='2.0.0'; self.write(path,data)
                before = self.files()
                with self.assertRaisesRegex(VersionError,'Update Unfertig'): self.store.initialize()
                self.assertEqual(self.files(),before)
                if previous is None: path.unlink()
                else: path.write_bytes(previous)
        self.assertEqual(self.files(),originals)

    def test_future_minor_read_only_preserves_mixed_old_bytes(self):
        data = json.loads(self.todo.read_text()); data['format_version']='1.14.0'; self.write(self.todo,data)
        before = self.files(); self.store.initialize()
        snap = self.store.snapshot()
        self.assertTrue(snap['compatibility']['read_only'])
        self.assertEqual(snap['protocol_version'],PROTOCOL_VERSION)
        with self.assertRaises(Conflict): self.store.mutate(self.edit(name='Rejected'))
        self.assertEqual(self.files(),before)

    def test_future_build_edit_preserves_unknown_fields_and_version(self):
        data = json.loads(self.todo.read_text()); data.update(format_version='1.13.42', extension={'nested':[1,2]})
        self.write(self.todo,data); self.store.initialize()
        request = self.edit(name='Changed')
        request['changes'][0]['record'].pop('extension')
        request['changes'][0]['record'].pop('format_version')
        self.store.mutate(request)
        saved = json.loads(self.todo.read_text())
        self.assertEqual(saved['extension'],data['extension'])
        self.assertEqual(saved['format_version'],'1.13.42')
        request = self.edit(format_version='1.1.0')
        with self.assertRaisesRegex(VersionError,'downgrade'): self.store.mutate(request)

    def test_current_format_creation_defaults_and_future_effort_is_read_only(self):
        self.store.initialize()
        todo = dict(fixture()['todos'][0], format_version=FORMAT_VERSION, source_ideas=[])
        todo.pop('id')
        result = self.store.mutate(dict(actor='Codex', request_id=uuid.uuid4().hex,
            changes=[dict(collection='todos', id=None, record=todo)]))
        self.assertEqual(result['data']['todos'][-1]['effort'], 'medium')
        future = dict(result['data']['todos'][0], format_version='1.14.0', effort='future')
        self.write(self.todo, future)
        before = self.files(); self.store.initialize()
        snapshot = self.store.snapshot()
        self.assertTrue(snapshot['compatibility']['read_only'])
        self.assertEqual(snapshot['data']['todos'][0]['effort'], 'future')
        with self.assertRaises(Conflict):
            self.store.mutate(self.edit(name='Cannot overwrite future semantics'))
        self.assertEqual(self.files(), before)

    def test_effort_migration_defaults_preserves_and_recovers(self):
        from efforts import EFFORTS
        for effort in [None, *EFFORTS]:
            old = dict(fixture()['todos'][0], format_version='1.11.0', completion_summary='Preserve outcome', extension={'keep': True})
            if effort is not None:
                old['effort'] = effort
            migrated = migrate(old, 'todo')
            self.assertEqual(semantic(migrated), dict(semantic(old), effort=effort or 'medium'))
            self.assertEqual(migrate(migrated, 'todo'), migrated)
        old = dict(fixture()['todos'][0], format_version='1.11.0', completion_summary='Preserve outcome', effort='unsupported')
        self.write(self.todo, old); before = self.files()
        with self.assertRaisesRegex(ValueError, 'Unsupported effort'):
            self.store.initialize()
        self.assertEqual(self.files(), before)
        old['effort'] = 'xhigh'; self.write(self.todo, old)
        real = storage.atomic
        def interrupted(path, raw):
            if path.resolve() == self.todo.resolve():
                raise OSError('Interrupted effort migration')
            return real(path, raw)
        with patch('storage.atomic', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.store.initialize()
        self.store.initialize()
        self.assertEqual(self.store.snapshot()['data']['todos'][0]['effort'], 'xhigh')
        before = self.files(); self.store.initialize(); self.assertEqual(self.files(), before)

    def test_category_workflow_scope_and_prompt_vocabulary(self):
        from categories import CATEGORIES, DEFINITIONS, briefing
        from workflow import scope_digest
        todo = fixture()['todos'][0]
        legacy = digest({k: todo.get(k) for k in ('name', 'description', 'source_ideas', 'source_refs')})
        self.assertEqual(scope_digest(todo), legacy)
        self.assertEqual(scope_digest(dict(todo, category='')), legacy)
        for category, definition in CATEGORIES.items():
            selected = dict(todo, category=category)
            self.assertNotEqual(scope_digest(selected), legacy)
            text = briefing(selected)
            for value in definition.values():
                self.assertIn(value, text)
            self.assertIn(DEFINITIONS['boundary'], text)
        self.assertNotEqual(scope_digest(dict(todo, category='design')), scope_digest(dict(todo, category='implementation')))

    def test_category_migration_preserves_absence_and_explicit_values(self):
        for category in (None, '', 'research'):
            todo = dict(fixture()['todos'][0], format_version='1.6.0', extension={'keep': True})
            if category is not None:
                todo['category'] = category
            migrated = migrate(todo, 'todo')
            self.assertEqual(semantic(migrated), dict(semantic(todo), effort='medium'))
            self.assertEqual(migrate(migrated, 'todo'), migrated)
        self.store.initialize()
        self.store.mutate(self.edit(category='research'))
        # Legacy clients omitting optional fields cannot erase a saved selection.
        request = self.edit(group='City')
        request['changes'][0]['record'].pop('category')
        self.store.mutate(request)
        self.assertEqual(self.store.read()[0]['todos'][0]['category'], 'research')

    def test_invalid_or_unregistered_version_never_rewrites(self):
        for version in (1,None,'1.01.0','1.1','0.5.0','1.0.9'):
            with self.subTest(version=version):
                data = fixture()['todos'][0]; data['format_version']=version; self.write(self.todo,data)
                before=self.files()
                with self.assertRaises(VersionError): self.store.initialize()
                self.assertEqual(self.files(),before)

    def test_interrupted_migration_recovers_and_retains_receipt_identity(self):
        receipt = dict(request_id='a'*16, fingerprint='existing-fingerprint', assigned=[{'collection':'todos','id':'T0001'}])
        self.write(self.store.receipts/('a'*16+'.json'),receipt)
        todo = json.loads(self.todo.read_text()); todo.update(format_version='1.6.0', category='debugging'); self.write(self.todo, todo)
        original_ideas=json.loads(self.path.read_text())['ideas']
        real=storage.atomic
        def interrupted(path,raw):
            if path.resolve()==self.todo.resolve(): raise OSError('Interrupted')
            return real(path,raw)
        with patch('storage.atomic',side_effect=interrupted):
            with self.assertRaises(OSError): self.store.initialize()
        self.assertTrue(self.store.journal.exists())
        self.store.initialize()
        self.assertFalse(self.store.journal.exists())
        self.assertEqual(self.store.read()[0]['todos'][0]['category'], 'debugging')
        self.assertEqual(json.loads(self.path.read_text())['ideas'],original_ideas)
        saved=json.loads((self.store.receipts/('a'*16+'.json')).read_text())
        self.assertEqual(semantic(saved),receipt)
        self.assertEqual(saved['format_version'],FORMAT_VERSION)
        before=self.files(); self.store.initialize(); self.assertEqual(self.files(),before)

    def test_future_nested_journal_refuses_before_partial_recovery(self):
        self.store.initialize()
        header=json.loads(self.path.read_text()); header['extension']={'change':True}
        todo=json.loads(self.todo.read_text()); todo['format_version']='2.0.0'
        self.write(self.store.journal,dict(format_version=FORMAT_VERSION,files={'data.json':header,'todos/T0001.json':todo},paths=['data.json','todos/T0001.json'],message='Future',receipt=None))
        before=self.files()
        with self.assertRaises(VersionError): self.store.initialize()
        self.assertEqual(self.files(),before)

    def test_corrupt_journal_never_partially_recovers(self):
        self.store.initialize()
        header=json.loads(self.path.read_text()); header['extension']={'change':True}
        todo=json.loads(self.todo.read_text()); todo['source_ideas']=['I9999']
        self.write(self.store.journal,dict(format_version=FORMAT_VERSION,files={'data.json':header,'todos/T0001.json':todo},paths=['data.json','todos/T0001.json'],message='Corrupt',receipt=None))
        before=self.files()
        with self.assertRaises(ValueError): self.store.initialize()
        self.assertEqual(self.files(),before)

    def test_backups_are_immutable_and_not_interpreted_as_active_formats(self):
        backup=self.path.parent/'data.v1-backup.json'; backup.write_bytes(b'{"format_version":"999.0.0","historical":true}\n')
        original=backup.read_bytes(); self.store.initialize()
        self.assertEqual(backup.read_bytes(),original)

    def test_semantic_audit_does_not_strip_nested_user_metadata(self):
        self.store.initialize()
        self.assertEqual(semantic(self.store.read()[0])['extension'],{'format_version':'user-extension-value'})

    def test_future_protocol_request_is_refused(self):
        self.store.initialize(); request=self.edit(name='Nope'); request['protocol_version']='3.0.0'
        before=self.files()
        with self.assertRaises(VersionError): self.store.mutate(request)
        self.assertEqual(self.files(),before)
        request['protocol_version']='2.1.0'
        with self.assertRaises(VersionError): self.store.mutate(request)

    def test_migration_commit_failure_retains_history_for_retry(self):
        def git(*args):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)
        git('init', '-q'); git('config', 'user.name', 'Migration Test')
        git('config', 'user.email', 'migration@example.invalid')
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        self.store.git = True
        self.store.initialize()
        self.assertTrue(self.store.pending.exists())
        self.assertEqual(json.loads(self.store.pending.read_text())['format_version'], FORMAT_VERSION)
        with self.assertRaises(Conflict): self.store.initialize()
        hook.unlink(); self.store.initialize()
        self.assertFalse(self.store.pending.exists())
        git('log', '-1', '--oneline')


if __name__=='__main__': unittest.main()
