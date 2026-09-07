"""Maintained transport conformance suite: run identical semantics on each mode."""
import copy
import json
import subprocess
import sys
import threading
import uuid
import unittest
from unittest.mock import patch

from aggregation import Aggregation, Unreachable, exchange
from configuration import board_context, resolve
from storage import BoardStore, Conflict, digest, atomic, encode
from transports import filesystem, preflight_context
from server import validate
from versions import FORMAT_VERSION, migrate
import test_aggregation as fixtures


class Conformance:
    request = fixtures.AggregationTests.request
    refreshed = fixtures.AggregationTests.refreshed
    test_nonblocking_refresh_and_twenty_second_backoff = fixtures.AggregationTests.test_slow_source_is_nonblocking_and_retries_after_twenty_seconds

    def setUp(self):
        fixtures.AggregationTests.setUp(self)
        for source, board in zip(self.sources, self.boards[1:]):
            board.close()
            board.acquire(cooperative=True)
            config = board.root / 'config.json'
            config.write_text(json.dumps(dict(format_version=FORMAT_VERSION, data='data.json',
                                             project_id=source['project_id'], project_name=board.context['project_name'])))
            board.config = config
            board.context = board_context(resolve(board.root, config=config), board.root)
            source.update(config=str(config), app_root=str(board.root), transports=self.enabled)
        self.router = Aggregation(self.inbox)

    test_originals_receipts_attribution_and_git_owner = fixtures.AggregationTests.test_route_exact_original_retry_and_repository_commits
    test_simultaneous_claims_and_unrelated_edits = fixtures.AggregationTests.test_simultaneous_first_claim_and_child_edit
    test_preflight_and_validation = fixtures.AggregationTests.test_preflight_and_invalid_todo_do_not_claim_or_write
    test_duplicate_provenance = fixtures.AggregationTests.test_duplicate_source_provenance_rejected_with_new_request_id
    test_inbox_history_recovery = fixtures.AggregationTests.test_local_history_failure_after_destination_save

    def test_capture_system_is_authoritative_and_preserved(self):
        source = self.sources[0]
        snapshot = self.router.inspect_source(source)
        record = dict(author='Test', date_entered='2026-09-07T12:00:00Z', text='Transport capture', captured_system='b'*64)
        request = dict(request_id=uuid.uuid4().hex, actor='Test', changes=[dict(collection='ideas', id=None, record=record)])
        with patch('processing.system_id', return_value='a'*64):
            result = self.router.transfer(source, '/api/changes', request, snapshot.get('token'), preflight_context(snapshot['context']))
        idea = result['data']['ideas'][-1]
        self.assertEqual(idea['captured_system'], 'a'*64)
        retry = self.router.transfer(source, '/api/changes', request, snapshot.get('token'), preflight_context(snapshot['context']))
        self.assertEqual(retry['assigned'], result['assigned'])
        self.assertEqual(retry['data']['ideas'][-1], idea)

    def test_same_and_different_record_revisions(self):
        source = self.sources[0]
        snapshot = self.router.inspect_source(source)
        old = snapshot['data']['todos'][0]
        def edit(name):
            return dict(request_id=uuid.uuid4().hex, actor='Codex', changes=[dict(collection='todos',
                id=old['id'], revision=digest(old), record=dict(old, name=name))])
        expected = preflight_context(snapshot['context'])
        self.router.transfer(source, '/api/changes', edit('one'), snapshot.get('token'), expected)
        with self.assertRaises((ValueError, Conflict)):
            self.router.transfer(source, '/api/changes', edit('two'), snapshot.get('token'), expected)
        self.assertEqual(self.alpha.read()[0]['todos'][0]['name'], 'one')

    def test_views_have_equal_identity_and_records(self):
        for source, board in zip(self.sources, self.boards[1:]):
            snapshot = self.router.inspect_source(source)
            self.assertEqual(snapshot['data'], board.snapshot()['data'])
            self.assertEqual(snapshot['revisions'], board.snapshot()['revisions'])
            self.assertEqual(snapshot['context'], board.context)
        view = self.refreshed(self.router)['sources']
        self.assertEqual([s['data']['todos'][0]['id'] for s in view], ['T0001', 'T0001'])

    def test_pending_destination_history_blocks_then_recovers(self):
        atomic(self.alpha.pending, encode(dict(format_version=FORMAT_VERSION, paths=['data.json'], message='Recover test history')))
        result = self.router.route(self.request())['idea']
        self.assertEqual(result['routing']['status'], 'blocked')
        self.assertIn('history', result['routing']['reason'])
        self.alpha.commit_pending()
        self.assertEqual(self.router.route(self.request())['idea']['routing']['status'], 'routed')

    def test_accepted_destination_with_failed_history_retains_claim(self):
        original = BoardStore.commit_pending
        def fail_destination(store):
            if store.root == self.alpha.root:
                return False
            return original(store)
        with patch.object(BoardStore, 'commit_pending', fail_destination):
            result = self.router.route(self.request())['idea']
        self.assertEqual(result['routing']['status'], 'blocked')
        self.assertIn('request', result['routing'])
        self.assertEqual(len(self.alpha.read()[0]['todos']), 2)
        self.assertTrue(self.alpha.commit_pending())
        self.assertEqual(self.router.route(self.request())['idea']['routing']['status'], 'routed')
        self.assertEqual(len(self.alpha.read()[0]['todos']), 2)

    def test_accepted_journal_and_extensions_recover_through_either_transport(self):
        todo = dict(self.alpha.read()[0]['todos'][0], name='Accepted batch', extension={'keep': [1, 2]})
        atomic(self.alpha.journal, encode(dict(format_version=FORMAT_VERSION, files={'todos/T0001.json': todo},
            paths=['todos/T0001.json'], message='Recovery conformance', receipt=None)))
        result = self.router.inspect_source(self.sources[0])
        self.assertEqual(result['data']['todos'][0], todo)
        self.assertTrue(result['history']['pending'])
        self.assertFalse(self.alpha.journal.exists())


class HTTPConformance(Conformance, unittest.TestCase):
    enabled = {'http': True, 'filesystem': False}


class FilesystemConformance(Conformance, unittest.TestCase):
    enabled = {'http': False, 'filesystem': True}


class DualConformance(Conformance, unittest.TestCase):
    enabled = {'http': True, 'filesystem': True}

    def test_preferred_fallback_failback_and_no_rejection_fallback(self):
        source = self.sources[0]
        self.assertEqual(self.router.inspect_source(source)['transport'], 'http')
        with patch('aggregation.exchange', side_effect=Unreachable('timeout')):
            result = self.router.inspect_source(source)
            self.assertEqual(result['transport'], 'filesystem')
            self.assertEqual(result['fallback_reason'], 'timeout')
        self.assertEqual(self.router.inspect_source(source)['transport'], 'http')
        for reason in ('HTTP 409', 'HTTP 403', 'HTTP 500', 'incompatible', 'invalid JSON'):
            with patch('aggregation.exchange', side_effect=ValueError(reason)), patch('aggregation.filesystem') as fs:
                with self.assertRaises(ValueError): self.router.inspect_source(source)
                fs.assert_not_called()

    def test_lost_response_switches_transport_without_duplicate(self):
        real = exchange
        def lose(source, path='/api/state', body=None, token=None):
            result = real(source, path, body, token)
            if path == '/api/changes':
                raise Unreachable('accepted, response lost')
            return result
        with patch('aggregation.exchange', side_effect=lose):
            result = self.router.route(self.request())['idea']
        self.assertEqual(result['routing']['status'], 'routed')
        self.assertEqual(len(self.alpha.read()[0]['todos']), 2)
        self.assertEqual(len(list(self.alpha.receipts.glob('*.json'))), 1)
        self.router.route(self.request())
        self.assertEqual(len(self.alpha.read()[0]['todos']), 2)

    def test_simultaneous_http_and_filesystem_writes(self):
        source = self.sources[0]
        snapshot = exchange(source)
        todo = snapshot['data']['todos'][0]
        expected = preflight_context(snapshot['context'])
        barrier = threading.Barrier(2); outcomes = []
        def edit(fs):
            body = dict(request_id=uuid.uuid4().hex, actor='Codex', changes=[dict(collection='todos',
                id=todo['id'], revision=digest(todo), record=dict(todo, name=str(fs)))])
            barrier.wait()
            try:
                if fs: filesystem(source, validate, body, expected)
                else: exchange(source, '/api/changes', body, snapshot['token'])
                outcomes.append('saved')
            except (ValueError, Conflict): outcomes.append('conflict')
        threads = [threading.Thread(target=edit, args=(fs,)) for fs in (True, False)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(outcomes), ['conflict', 'saved'])

    def test_no_service_no_url_and_inaccessible_is_not_empty(self):
        for server in self.servers:
            server.shutdown(); server.server_close()
        source = dict(self.sources[0], transports={'http': False, 'filesystem': True})
        source.pop('url')
        self.router.sources = [source]
        with patch('aggregation.exchange', side_effect=AssertionError('HTTP must not run')):
            self.assertEqual(self.refreshed(self.router)['sources'][0]['transport'], 'filesystem')
            self.assertEqual(self.router.route(self.request())['idea']['routing']['status'], 'routed')
        self.router.sources = [dict(source, data=str(self.root / 'missing.json'))]
        result = self.refreshed(self.router)['sources'][0]
        self.assertEqual(result['status'], 'stale')
        fresh = Aggregation(self.inbox); fresh.sources = self.router.sources
        self.assertIsNone(fresh.view()['sources'][0]['data'])

    def test_real_refused_connection_falls_back_and_http_403_does_not(self):
        source = self.sources[0]
        with self.assertRaisesRegex(ValueError, '403'):
            self.router.transfer(source, '/api/changes', {}, 'invalid')
        self.servers[0].shutdown(); self.servers[0].server_close()
        result = self.router.inspect_source(source)
        self.assertEqual(result['transport'], 'filesystem')
        self.assertIn('cannot be reached', result['fallback_reason'])

    def test_legacy_writer_and_exclusive_migration_block_access(self):
        self.alpha.close(); self.alpha.acquire()
        with self.assertRaisesRegex(ValueError, 'owns this data directory'):
            filesystem(self.sources[0], validate)
        self.alpha.close(); self.alpha.acquire(cooperative=True)
        offline = BoardStore(self.alpha.path, validate)
        with self.assertRaises(ValueError): offline.acquire()

    def test_read_does_not_migrate_and_preserves_bytes(self):
        header = json.loads(self.alpha.path.read_bytes()); header['format_version'] = '1.2.0'
        atomic(self.alpha.path, encode(header)); before = self.alpha.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'stopped migration'):
            filesystem(self.sources[0], validate)
        self.assertEqual(self.alpha.path.read_bytes(), before)

    def test_interrupted_journal_rolls_forward_and_recovers_receipt(self):
        snapshot = filesystem(self.sources[0], validate)
        todo = dict(snapshot['data']['todos'][0], name='Accepted interrupted change')
        atomic(self.alpha.journal, encode(dict(format_version=FORMAT_VERSION, files={'todos/T0001.json':todo},
            paths=['todos/T0001.json'], message='Interrupted edit', receipt=None)))
        recovered = filesystem(self.sources[0], validate)
        self.assertEqual(recovered['data']['todos'][0]['name'], todo['name'])
        self.assertTrue(recovered['history']['pending'])
        self.assertFalse(self.alpha.journal.exists())

    def test_cross_process_lock_and_service_start_exclusion(self):
        script = """from storage import BoardStore
from server import validate
import sys
s=BoardStore(sys.argv[1],validate)
s.acquire(cooperative=True)
try:
 with s.lock: print('acquired')
finally: s.close()
"""
        with self.alpha.lock:
            result = subprocess.run([sys.executable, '-c', script, str(self.alpha.path)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Board operation busy', result.stderr)
        self.alpha.close(); self.alpha.acquire(cooperative=True, service=True)
        other = BoardStore(self.alpha.path, validate)
        with self.assertRaises(ValueError): other.acquire(cooperative=True, service=True)
        result = subprocess.run([sys.executable, '-c', script, str(self.alpha.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class TransportConfiguration(unittest.TestCase):
    def test_modes_defaults_and_rejection(self):
        from configuration import transports
        for value in ({'http':True,'filesystem':False}, {'http':False,'filesystem':True}, {'http':True,'filesystem':True}):
            self.assertEqual(transports(value), value)
        for value in ({'http':False,'filesystem':False}, {'http':1,'filesystem':False}, {}):
            with self.assertRaises(ValueError): transports(value)
        config = migrate(dict(format_version='1.2.0', mode='aggregation', extension=7), 'config')
        self.assertEqual(config['transports'], {'http':True,'filesystem':False})
        self.assertEqual(config['extension'], 7)
        self.assertEqual(migrate(config, 'config'), config)
