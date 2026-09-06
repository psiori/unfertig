"""Publication tests use only disposable local repositories and bare remotes."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from publication import Publication, GitError
from storage import BoardStore, Conflict, digest
from server import validate, Server
from test_server import fixture, STAMP


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='unfertig-publication-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo, self.remote, self.other = [self.root / p for p in ('local', 'remote.git', 'other')]
        self.repo.mkdir()
        self.git(self.repo, 'init', '-b', 'main'); self.identity(self.repo)
        self.git(self.root, 'init', '--bare', str(self.remote))
        (self.repo / '.gitignore').write_text('.server.lock\n.transaction.json\n.history-pending.json\n.receipts/\n')
        path = self.repo / 'state' / 'data.json'; path.parent.mkdir()
        data = fixture()
        second = copy.deepcopy(data['todos'][0]); second.update(id='T0002', source_ideas=[])
        data['todos'].append(second); path.write_text(json.dumps(data))
        self.store = BoardStore(path, validate)
        self.store.acquire(); self.addCleanup(self.store.close); self.store.initialize()
        self.git(self.repo, 'add', '.gitignore'); self.git(self.repo, 'commit', '-m', 'Ignore runtime')
        self.git(self.repo, 'remote', 'add', 'origin', str(self.remote)); self.git(self.repo, 'push', '-u', 'origin', 'main')
        self.git(self.root, 'clone', '-b', 'main', str(self.remote), str(self.other)); self.identity(self.other)
        self.publication = Publication(self.store)

    def git(self, root, *args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=True).stdout.strip()

    def identity(self, root):
        for key, value in [('user.name', 'Publication Test'), ('user.email', 'publication@example.invalid'), ('commit.gpgsign', 'false')]:
            self.git(root, 'config', key, value)

    def edit(self, id='T0001', **fields):
        record = next(t for t in self.store.read()[0]['todos'] if t['id'] == id)
        revision = digest(record); record.update(fields)
        body = dict(request_id=uuid.uuid4().hex, actor='Test', changes=[dict(collection='todos', id=id, revision=revision, record=record)])
        self.store.mutate(body)

    def add_idea(self, text='New local idea'):
        self.store.mutate(dict(request_id=uuid.uuid4().hex, actor='Test', changes=[dict(collection='ideas', id=None, record=dict(author='Human', text=text, date_entered=STAMP))]))

    def remote_edit(self, id='T0002', **fields):
        path = self.other / 'state' / 'todos' / (id + '.json')
        record = json.loads(path.read_text()); record.update(fields)
        path.write_text(json.dumps(record, indent=2) + '\n'); self.remote_commit()

    def remote_commit(self):
        self.git(self.other, 'add', '.'); self.git(self.other, 'commit', '-m', 'Remote edit'); self.git(self.other, 'push')

    def push(self):
        return self.publication.push(self.publication.status()['confirmation'])

    def test_record_comparison_and_global_push(self):
        self.add_idea(); self.edit(name='Locally edited')
        status = self.publication.status()
        self.assertEqual(status['records']['ideas'], {'I0001': 'published', 'I0002': 'local'})
        self.assertEqual(status['records']['todos'], {'T0001': 'local', 'T0002': 'published'})
        (self.repo / 'unrelated.txt').write_text('Committed non-board change')
        self.git(self.repo, 'add', '.'); self.git(self.repo, 'commit', '-m', 'Non-board commit')
        self.assertIn('outside this board', self.publication.status()['message'])
        self.push()
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.git(self.remote, 'rev-parse', 'main'))
        self.assertEqual(self.publication.status()['ahead'], 0)
        self.assertEqual(self.publication.status()['records']['ideas']['I0002'], 'published')
        self.assertEqual(self.git(self.remote, 'show', 'main:unrelated.txt'), 'Committed non-board change')

    def test_independent_remote_merge_and_stale_revision(self):
        old = self.store.snapshot()['revisions']['todos']['T0002']
        self.edit(name='Local'); self.remote_edit(name='Remote'); self.push()
        data = self.store.read()[0]
        self.assertEqual([t['name'] for t in data['todos']], ['Local', 'Remote'])
        stale = copy.deepcopy(data['todos'][1]); stale['name'] = 'Old draft'
        with self.assertRaises(Conflict):
            self.store.mutate(dict(request_id=uuid.uuid4().hex, changes=[dict(collection='todos', id='T0002', revision=old, record=stale)]))

    def test_semantic_same_record_conflict_preserves_checkout(self):
        self.edit(name='Local name'); self.remote_edit('T0001', description='Remote description')
        before = self.git(self.repo, 'rev-parse', 'HEAD'), self.store.read()
        with self.assertRaises(Conflict): self.push()
        self.assertEqual((self.git(self.repo, 'rev-parse', 'HEAD'), self.store.read()), before)
        self.assertEqual(self.git(self.repo, 'status', '--porcelain'), '')
        self.assertNotIn('unfertig-publish-', self.git(self.repo, 'worktree', 'list'))

    def test_text_conflict_preserves_checkout(self):
        self.edit(name='Local name'); self.remote_edit('T0001', name='Other name')
        head = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaises(Conflict): self.push()
        self.assertEqual(head, self.git(self.repo, 'rev-parse', 'HEAD'))
        self.assertEqual(self.git(self.repo, 'status', '--porcelain'), '')

    def test_id_collision(self):
        self.add_idea('Local I0002')
        path = self.other / 'state' / 'data.json'; data = json.loads(path.read_text())
        data['ideas'].append(dict(id='I0002', author='Other', text='Remote I0002', date_entered=STAMP))
        path.write_text(json.dumps(data, indent=2) + '\n'); self.remote_commit()
        with self.assertRaises((Conflict, ValueError)): self.push()
        self.assertEqual(self.store.read()[0]['ideas'][-1]['text'], 'Local I0002')

    def test_dirty_and_staged_work_preserved(self):
        self.edit(name='Local')
        for staged in (False, True):
            path = self.repo / 'unrelated.txt'; path.write_text('Keep me')
            if staged: self.git(self.repo, 'add', str(path))
            before = self.git(self.repo, 'status', '--porcelain')
            self.assertFalse(self.publication.status()['can_push'])
            with self.assertRaises(Conflict): self.push()
            self.assertEqual(before, self.git(self.repo, 'status', '--porcelain'))
            self.assertEqual(path.read_text(), 'Keep me')

    def test_incoming_file_does_not_overwrite_ignored_local_content(self):
        self.edit(name='Local')
        (self.repo / '.git' / 'info' / 'exclude').write_text('private.txt\n')
        (self.repo / 'private.txt').write_text('Keep ignored local file')
        (self.other / 'private.txt').write_text('Incoming file')
        self.remote_commit()
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(Conflict, 'replace local content'): self.push()
        self.assertEqual((self.repo / 'private.txt').read_text(), 'Keep ignored local file')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)

    def test_pending_history_uncommitted(self):
        with patch.object(self.store, 'commit_pending', return_value=False): self.edit(name='Commit failed')
        status = self.publication.status()
        self.assertEqual(status['records']['todos']['T0001'], 'uncommitted')
        self.assertFalse(status['can_push'])
        with self.assertRaises(Conflict): self.push()
        self.assertTrue(self.store.pending.exists())

    def test_missing_upstream_and_ambiguous_remote(self):
        self.git(self.repo, 'config', 'branch.main.pushRemote', 'other')
        self.assertFalse(self.publication.status()['available'])
        self.git(self.repo, 'config', '--unset', 'branch.main.pushRemote'); self.git(self.repo, 'branch', '--unset-upstream')
        self.assertFalse(self.publication.status()['available'])

    def test_stale_confirmation(self):
        self.edit(name='First'); confirmation = self.publication.status()['confirmation']; self.edit(name='Second')
        with self.assertRaises(Conflict): self.publication.push(confirmation)
        self.assertNotEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.git(self.remote, 'rev-parse', 'main'))

    def test_remote_url_change_invalidates_confirmation(self):
        self.edit(name='Local')
        confirmation = self.publication.status()['confirmation']
        second = self.root / 'second.git'
        self.git(self.root, 'clone', '--bare', str(self.remote), str(second))
        self.git(self.repo, 'remote', 'set-url', 'origin', str(second))
        with self.assertRaises(Conflict): self.publication.push(confirmation)

    def test_existing_git_operation_blocks_push(self):
        self.edit(name='Local')
        marker = self.repo / '.git' / 'MERGE_HEAD'
        marker.write_text(self.git(self.repo, 'rev-parse', 'HEAD') + '\n')
        with self.assertRaises(Conflict): self.push()
        self.assertTrue(marker.exists())

    def test_remote_recovery_files_and_schema_are_rejected(self):
        self.edit(name='Local')
        recovery = self.other / 'state' / '.transaction.json'
        recovery.write_text('{}')
        self.git(self.other, 'add', '-f', str(recovery)); self.remote_commit()
        with self.assertRaises(GitError): self.push()
        self.assertFalse(self.store.journal.exists())
        self.git(self.other, 'rm', str(recovery))
        header = self.other / 'state' / 'data.json'
        data = json.loads(header.read_text()); data['schema_version'] = 999
        header.write_text(json.dumps(data)); self.remote_commit()
        self.publication.refresh()
        self.assertFalse(self.publication.status()['available'])
        self.assertIn('schema', self.publication.status()['message'])

    def test_remote_advances_during_push_is_not_forced(self):
        self.edit(name='Local')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        real = self.publication.git
        def race(root, *args, **kwargs):
            if args[0] == 'push': self.remote_edit(name='Racing remote')
            return real(root, *args, **kwargs)
        with patch.object(self.publication, 'git', side_effect=race):
            with self.assertRaises(GitError): self.push()
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertEqual(self.git(self.other, 'rev-parse', 'HEAD'), self.git(self.remote, 'rev-parse', 'main'))

    def test_failed_local_promotion_reports_remote_success(self):
        self.edit(name='Local'); self.remote_edit(name='Remote')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        real = self.publication.git
        def failed(root, *args, **kwargs):
            if Path(root).resolve() == self.repo.resolve() and args[0] == 'merge': raise GitError('Local promotion interrupted')
            return real(root, *args, **kwargs)
        with patch.object(self.publication, 'git', side_effect=failed):
            with self.assertRaisesRegex(GitError, 'Remote accepted'): self.push()
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertNotEqual(self.git(self.remote, 'rev-parse', 'main'), before)
        self.assertTrue(self.git(self.repo, 'for-each-ref', '--format=%(refname)', 'refs/unfertig/publication/'))

    def test_network_failure_marks_unknown(self):
        real = self.publication.git
        def failed(root, *args, **kwargs):
            if args[0] == 'fetch': raise GitError('Authentication unavailable')
            return real(root, *args, **kwargs)
        with patch.object(self.publication, 'git', side_effect=failed):
            with self.assertRaises(GitError): self.publication.refresh()
        self.assertEqual(self.publication.status()['records']['ideas']['I0001'], 'unknown')
        self.assertFalse(self.publication.status()['can_push']); self.publication.refresh()
        self.assertEqual(self.publication.status()['records']['ideas']['I0001'], 'published')

    def test_rejected_push_retains_live_state(self):
        self.edit(name='Local'); self.remote_edit(name='Remote')
        hook = self.remote / 'hooks' / 'pre-receive'; hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        head, board = self.git(self.repo, 'rev-parse', 'HEAD'), self.store.read()
        with self.assertRaises(GitError): self.push()
        self.assertEqual((self.git(self.repo, 'rev-parse', 'HEAD'), self.store.read()), (head, board))
        self.assertEqual(self.git(self.repo, 'status', '--porcelain'), '')

    def test_lost_success_response_verified(self):
        self.edit(name='Local'); self.remote_edit(name='Remote')
        real = self.publication.git; pushes = []
        def lost(root, *args, **kwargs):
            result = real(root, *args, **kwargs)
            if args[0] == 'push': pushes.append(args); raise GitError('Lost response')
            return result
        with patch.object(self.publication, 'git', side_effect=lost): self.push()
        self.assertEqual(len(pushes), 1); self.assertEqual(self.publication.status()['ahead'], 0)

    def test_concurrent_save_waits_without_loss(self):
        self.edit(name='First')
        entered, release, saved = threading.Event(), threading.Event(), threading.Event()
        real = self.publication.git; errors = []
        def delayed(root, *args, **kwargs):
            if args[0] == 'push': entered.set(); release.wait(5)
            return real(root, *args, **kwargs)
        def publish():
            try: self.push()
            except Exception as error: errors.append(error)
        def save():
            try: self.edit(name='Concurrent save'); saved.set()
            except Exception as error: errors.append(error)
        with patch.object(self.publication, 'git', side_effect=delayed):
            thread = threading.Thread(target=publish); thread.start(); self.assertTrue(entered.wait(5))
            with self.assertRaises(Conflict): self.publication.push('duplicate')
            worker = threading.Thread(target=save); worker.start()
            self.assertFalse(saved.wait(.05)); release.set(); thread.join(10); worker.join(10)
        self.assertEqual(errors, []); self.assertTrue(saved.is_set())
        self.assertEqual(self.publication.status()['records']['todos']['T0001'], 'local')
        self.assertEqual(self.store.read()[0]['todos'][0]['name'], 'Concurrent save')

    def test_http_token_and_confirmation(self):
        server = Server(('127.0.0.1', 0), self.store)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        with urlopen(base + '/api/publication') as response: self.assertTrue(json.load(response)['available'])
        for token, expected in [('', 403), (server.token, 409)]:
            request = Request(base + '/api/publication/push', data=b'{}', method='PUT', headers={'X-Board-Token': token, 'Content-Type': 'application/json'})
            with self.assertRaises(HTTPError) as error: urlopen(request)
            self.assertEqual(error.exception.code, expected)


if __name__ == '__main__': unittest.main()
