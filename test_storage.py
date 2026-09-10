"""Concurrency, recovery and Git tests use isolated temporary boards/repositories."""
import copy
import json
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import storage
from versions import semantic
from storage import BoardStore, Conflict, digest
from server import validate, Server
from test_server import fixture, STAMP


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='board-record-tests-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'data.json'
        data = fixture()
        second = copy.deepcopy(data['todos'][0]); second['id'] = 'T0002'; second['source_ideas'] = []
        data['todos'].append(second)
        data['extension'] = {'preserve': True}
        self.original = data
        self.expected = copy.deepcopy(data)
        for todo in self.expected['todos']:
            todo['effort'] = 'medium'
            todo['execution_profile'] = 'auto'
        self.path.write_text(json.dumps(data))
        self.store = BoardStore(self.path, validate, git=False)
        self.store.acquire(); self.addCleanup(self.store.close)
        self.store.initialize()

    def edit(self, index=0, **fields):
        data, _ = self.store.read()
        record = data['todos'][index]
        revision = digest(record)
        record.update(fields)
        return dict(request_id=uuid.uuid4().hex, actor='Test', changes=[dict(collection='todos', id=record['id'], revision=revision, record=record)])

    def test_completion_lifecycle_and_recovery(self):
        closure = dict(status='closed', closed_by='Test', date_closed=STAMP)
        with self.assertRaisesRegex(ValueError, 'Completion summary'):
            self.store.mutate(self.edit(**closure))
        summary = 'Added closure reporting. Verified lifecycle tests. No limitations.'
        request = self.edit(**closure, completion_summary=summary)
        real = storage.atomic
        def interrupted(target, content):
            if target.name == 'T0001.json':
                raise OSError('interrupted closure')
            return real(target, content)
        with patch('storage.atomic', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.store.mutate(request)
        self.store.recover()
        saved = self.store.mutate(request)['data']['todos'][0]
        self.assertEqual(saved['completion_summary'], summary)
        self.assertEqual(saved['description'], self.original['todos'][0]['description'])
        with self.assertRaisesRegex(ValueError, 'Completion summary'):
            self.store.mutate(self.edit(completion_summary='  '))
        self.store.mutate(self.edit(status='open', closed_by='', date_closed=''))
        self.assertEqual(self.store.read()[0]['todos'][0]['completion_summary'], '')
        with self.assertRaisesRegex(ValueError, 'Completion summary'):
            self.store.mutate(self.edit(**closure))
        self.store.mutate(self.edit(**closure, completion_summary='Rechecked the outcome; tests passed; no follow-up.'))

    def test_new_closed_record_and_invalid_summary(self):
        record = copy.deepcopy(self.original['todos'][0])
        record.pop('id')
        record.update(source_ideas=[], status='closed', closed_by='Test', date_closed=STAMP)
        request = dict(actor='Test', request_id=uuid.uuid4().hex,
                       changes=[dict(collection='todos', id=None, record=record)])
        for invalid in (None, 42, [], '  '):
            record['completion_summary'] = invalid
            with self.assertRaisesRegex(ValueError, 'Completion summary'):
                self.store.mutate(request)
        record['completion_summary'] = 'Already implemented; inspected requirements and tests; no follow-up.'
        saved = self.store.mutate(request)['data']['todos'][-1]
        self.assertEqual(saved['completion_summary'], record['completion_summary'])

    def test_legacy_closed_summary_absence_is_not_fabricated(self):
        old = copy.deepcopy(self.original)
        old['todos'][0].update(status='closed', closed_by='Original', date_closed=STAMP)
        validate(old)
        changed = copy.deepcopy(old)
        changed['todos'][0]['priority'] = 'urgent'
        validate(changed, old)
        from versions import migrate
        migrated = migrate(old['todos'][0], 'todo')
        self.assertNotIn('completion_summary', migrated)
        self.assertEqual(migrated['closed_by'], 'Original')
        self.assertEqual(migrate(migrated, 'todo'), migrated)

    def test_migration_lossless_and_repeatable(self):
        self.assertEqual(semantic(self.store.read()[0]), self.expected)
        self.assertEqual(json.loads((self.path.parent/'data.v1-backup.json').read_bytes()), self.original)
        self.assertNotIn('todos', json.loads(self.path.read_bytes()))
        self.store.initialize()
        self.assertEqual(semantic(self.store.read()[0]), self.expected)

    def test_interrupted_migration_recovers_exact_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'data.json'
            raw = json.dumps(self.original).encode(); path.write_bytes(raw)
            store = BoardStore(path, validate, git=False)
            real = storage.atomic
            def interrupted(target, content):
                if target.name == 'data.json': raise OSError('interrupted migration')
                return real(target, content)
            with patch('storage.atomic', side_effect=interrupted):
                with self.assertRaises(OSError): store.initialize()
            self.assertEqual((path.parent/'data.v1-backup.json').read_bytes(), raw)
            restarted = BoardStore(path, validate, git=False)
            restarted.initialize()
            self.assertEqual(semantic(restarted.read()[0]), self.expected)
            restarted.initialize()
            self.assertFalse(restarted.journal.exists())

    def test_single_writer_lease(self):
        other = BoardStore(self.path, validate, git=False)
        with self.assertRaises(ValueError): other.acquire()

    def test_independent_edits_and_idea_capture_concurrent(self):
        requests = [self.edit(0, name='First agent'), self.edit(1, description='Second agent')]
        requests.append(dict(request_id=uuid.uuid4().hex, changes=[dict(collection='ideas', id=None, record=dict(author='Human', date_entered=STAMP, text='Concurrent idea'))]))
        barrier = threading.Barrier(3); errors = []
        def work(body):
            barrier.wait()
            try: self.store.mutate(body)
            except Exception as error: errors.append(error)
        threads = [threading.Thread(target=work, args=(r,)) for r in requests]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
        saved, _ = self.store.read()
        self.assertEqual(saved['todos'][0]['name'], 'First agent')
        self.assertEqual(saved['todos'][1]['description'], 'Second agent')
        self.assertEqual(saved['ideas'][-1]['text'], 'Concurrent idea')

    def test_same_record_conflict_and_retry_identity(self):
        a = self.edit(name='A'); b = self.edit(name='B')
        self.store.mutate(a)
        with self.assertRaises(Conflict): self.store.mutate(b)
        self.store.mutate(a) # uncertain network retry succeeds without another edit
        a['changes'][0]['record']['name'] = 'Different payload'
        with self.assertRaises(Conflict): self.store.mutate(a)

    def test_creation_ids_and_duplicate_processing(self):
        new = copy.deepcopy(self.original['todos'][1]); new.pop('id')
        def create(record):
            return self.store.mutate(dict(request_id=uuid.uuid4().hex, changes=[dict(collection='todos', id=None, record=record)]))
        self.assertEqual(create(new)['assigned'][0]['id'], 'T0003')
        self.assertEqual(create(new)['assigned'][0]['id'], 'T0004')
        new['source_ideas'] = ['I0001']
        with self.assertRaises(Conflict): create(new)

    def test_two_processors_racing_for_one_idea(self):
        self.store.mutate(dict(request_id=uuid.uuid4().hex, changes=[dict(collection='ideas', id=None,
            record=dict(author='Human', date_entered=STAMP, text='One processing race'))]))
        todo = copy.deepcopy(self.original['todos'][1]); todo.pop('id'); todo['source_ideas'] = ['I0002']
        barrier = threading.Barrier(2); outcomes = []
        def create():
            body = dict(request_id=uuid.uuid4().hex, changes=[dict(collection='todos', id=None, record=todo)])
            barrier.wait()
            try:
                self.store.mutate(body); outcomes.append('saved')
            except Conflict: outcomes.append('conflict')
        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertCountEqual(outcomes, ['saved', 'conflict'])
        self.assertEqual(len([t for t in self.store.read()[0]['todos'] if 'I0002' in t['source_ideas']]), 1)

    def test_invalid_batch_does_not_partially_save(self):
        body = self.edit(name='Valid')
        bad = self.edit(1, source_ideas=['I9999'])['changes'][0]
        body['changes'].append(bad)
        with self.assertRaises(ValueError): self.store.mutate(body)
        self.assertEqual(semantic(self.store.read()[0]), self.expected)

    def test_interruption_rolls_forward_and_retry_does_not_duplicate(self):
        body = self.edit(name='Recovered')
        body['changes'].append(self.edit(1, name='Also recovered')['changes'][0])
        real = storage.atomic
        def interrupted(path, raw):
            if path.name == 'T0002.json': raise OSError('simulated power loss')
            return real(path, raw)
        with patch('storage.atomic', side_effect=interrupted):
            with self.assertRaises(OSError): self.store.mutate(body)
        self.assertTrue(self.store.journal.exists())
        self.store.initialize()
        self.store.mutate(body)
        self.assertEqual([t['name'] for t in self.store.read()[0]['todos']], ['Recovered', 'Also recovered'])
        self.assertFalse(self.store.journal.exists())

    def test_noop_does_not_touch_data(self):
        path = self.path.parent/'todos/T0001.json'
        stat = path.stat().st_mtime_ns
        self.store.mutate(self.edit(updated_at='2026-09-07T10:00:00Z'))
        self.assertEqual(stat, path.stat().st_mtime_ns)

    def test_editor_route_requires_browser_context_and_replays_through_record_api(self):
        server = Server(('127.0.0.1', 0), self.store)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':0.01}, daemon=True); thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}'
            with urlopen(url+'/api/state') as response: state = json.load(response)
            body = self.edit(category='concept')
            def send(route, browser=False):
                headers = {'X-Board-Token':state['token']}
                if browser: headers.update({'Sec-Fetch-Site':'same-origin','Sec-Fetch-Mode':'cors'})
                return urlopen(Request(url+route, data=json.dumps(body).encode(), headers=headers, method='PUT'))
            with self.assertRaises(HTTPError) as failure: send('/api/editor/changes')
            self.assertEqual(failure.exception.code, 400)
            with send('/api/editor/changes', True) as response: saved = json.load(response)
            with send('/api/changes') as response: replay = json.load(response)
            self.assertEqual(saved['data'], replay['data'])
            authorization = saved['data']['todos'][0]['scope_authorizations'][0]
            self.assertEqual(authorization['request_id'], body['request_id'])
            self.assertEqual(authorization['new']['category'], 'concept')
            forged = self.edit(scope_authorizations=[])
            with self.assertRaisesRegex(ValueError, 'managed by the owner editor'):
                self.store.mutate(forged)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_api_new_protocol_and_old_client_rejection(self):
        server = Server(('127.0.0.1', 0), self.store)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True); thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}'
            with urlopen(url+'/api/state') as r: state = json.load(r)
            self.assertEqual(state['api_version'], 2)
            def send(path, body):
                return urlopen(Request(url+path, data=json.dumps(body).encode(), headers={'X-Board-Token':state['token']}, method='PUT'))
            with self.assertRaises(HTTPError) as error: send('/api/state', state)
            self.assertEqual(error.exception.code, 409)
            with send('/api/changes', self.edit(name='HTTP edit')) as r:
                self.assertEqual(json.load(r)['data']['todos'][0]['name'], 'HTTP edit')
        finally:
            server.shutdown(); server.server_close(); thread.join()


class GitTests(RecordTests):
    def git(self, *args):
        return subprocess.run(['git', '-C', self.temp.name, *args], check=True, capture_output=True, text=True).stdout.strip()

    def setUp(self):
        super().setUp()
        self.git('init')
        self.git('config', 'user.name', 'Board Test')
        self.git('config', 'user.email', 'board@example.invalid')
        self.git('add', 'data.json', 'todos', 'data.v1-backup.json')
        self.git('commit', '-m', 'Initial fixture')
        self.store.git = True

    def test_git_only_data_noop_and_pending_retry(self):
        other = self.path.parent/'unrelated.txt'; other.write_text('staged work')
        self.git('add', 'unrelated.txt')
        self.store.mutate(self.edit(name='Meaningful human edit'))
        self.assertEqual(self.git('show', '--pretty=format:', '--name-only', 'HEAD'), 'todos/T0001.json')
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'unrelated.txt')
        head = self.git('rev-parse', 'HEAD')
        self.store.mutate(self.edit(updated_at='2026-09-08T00:00:00Z'))
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        lock = self.path.parent/'.git/index.lock'; lock.write_text('busy')
        result = self.store.mutate(self.edit(description='Saved despite git lock'))
        self.assertTrue(result['history']['pending'])
        self.assertEqual(self.store.read()[0]['todos'][0]['description'], 'Saved despite git lock')
        with self.assertRaises(Conflict): self.store.mutate(self.edit(name='Wait for history'))
        lock.unlink()
        self.assertTrue(self.store.commit_pending())
        self.assertFalse(self.store.snapshot()['history']['pending'])
        self.assertNotEqual(head, self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'unrelated.txt')

    def test_nested_migration_commits_only_board_paths(self):
        path = self.path.parent/'nested'/'data.json'
        path.parent.mkdir(); path.write_text(json.dumps(fixture()))
        store = BoardStore(path, validate)
        store.initialize()
        self.assertFalse(store.snapshot()['history']['pending'])
        files = self.git('show', '--pretty=format:', '--name-only', 'HEAD').splitlines()
        self.assertEqual(set(files), {'nested/data.json', 'nested/data.v1-backup.json', 'nested/todos/T0001.json'})

    def test_closed_status_committed(self):
        self.store.mutate(self.edit(status='closed', closed_by='Human', date_closed=STAMP, completion_summary='Verified the completed task; no follow-up required.'))
        saved=json.loads(self.git('show', 'HEAD:todos/T0001.json'))
        self.assertEqual(saved['status'], 'closed')


if __name__ == '__main__': unittest.main()
