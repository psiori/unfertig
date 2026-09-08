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

    def test_completion_summary_history_and_switch_retry(self):
        source = self.sources[0]
        snapshot = self.router.inspect_source(source)
        old = snapshot['data']['todos'][0]
        closed = dict(old, status='closed', closed_by='Test', date_closed='2026-09-08T12:00:00Z')
        request = dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
            collection='todos', id=old['id'], revision=digest(old), record=closed)])
        expected = preflight_context(snapshot['context'])
        with self.assertRaises(ValueError):
            self.router.transfer(source, '/api/changes', request, snapshot.get('token'), expected)
        closed['completion_summary'] = 'Implemented reporting. Transport checks passed. No limitations.'
        result = self.router.transfer(source, '/api/changes', request, snapshot.get('token'), expected)
        switched = dict(source, transports=dict(http=False, filesystem=True))
        retry = self.router.transfer(switched, '/api/changes', request, None, expected)
        self.assertEqual(result['data'], retry['data'])
        self.assertEqual(retry['data']['todos'][0]['completion_summary'], closed['completion_summary'])
        self.assertFalse(retry['history']['pending'])
        history = subprocess.check_output(['git', 'show', 'HEAD:todos/'+old['id']+'.json'], cwd=self.boards[1].root, text=True)
        self.assertIn(closed['completion_summary'], history)

    def discovered_router(self, omit_beta=False):
        for source, board in zip(self.sources, self.boards[1:]):
            config = json.loads(board.config.read_text())
            config['aggregation_source'] = dict(app_root='.', url=source['url'])
            board.config.write_text(json.dumps(config))
        if omit_beta:
            self.hidden_beta = self.beta.config.read_bytes()
            self.beta.config.unlink()
        self.inbox.context.update(sources=[], search_paths=[str(self.root / '*/config.json')],
                                  transports=self.enabled)
        self.router = Aggregation(self.inbox)
        return self.router

    def test_discovery_refresh_retains_cache_and_claim_then_recovers(self):
        router = self.discovered_router()
        before = self.refreshed(router)['sources'][0]
        original = router.transfer
        def lost(source, path='/api/state', *args, **kwargs):
            result = original(source, path, *args, **kwargs)
            if path == '/api/changes':
                raise Unreachable('Lost accepted response')
            return result
        with patch.object(router, 'transfer', side_effect=lost):
            blocked = router.route(self.request())['idea']['routing']
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('request', blocked)
        config = self.alpha.config.read_bytes()
        self.alpha.config.unlink()
        router.refresh_discovery()
        removed = router.entries['alpha']
        self.assertEqual(removed['status'], 'removed')
        self.assertEqual(removed['data'], before['data'])
        retry = router.route(self.request())['idea']['routing']
        self.assertEqual(retry['request'], blocked['request'])
        self.assertIn('removed', retry['reason'])
        # Restart retains the saved destination even without an in-memory cache.
        restarted = Aggregation(self.inbox)
        self.assertEqual(restarted.entries['alpha']['status'], 'removed')
        self.assertIsNone(restarted.entries['alpha']['data'])
        retried = restarted.route(self.request())['idea']['routing']
        self.assertEqual(retried['request'], blocked['request'])
        self.assertIn('removed', retried['reason'])
        # A newly configured board cannot steal a durable claim's project ID.
        replacement = json.loads(config)
        replacement['data'] = str(self.beta.path)
        replacement_path = self.beta.root / 'replacement.json'
        replacement_path.write_text(json.dumps(replacement))
        self.inbox.context['search_paths'] = [str(replacement_path)]
        replaced = Aggregation(self.inbox)
        self.assertEqual(replaced.entries['alpha']['status'], 'removed')
        self.assertTrue(any('changed' in r['error'] for r in replaced.discovery_reports))
        self.inbox.context['search_paths'] = [str(self.root / '*/config.json')]
        self.alpha.config.write_bytes(config)
        router.refresh_discovery()
        self.assertEqual(router.route(self.request())['idea']['routing']['status'], 'routed')
        self.assertEqual(len(self.alpha.read()[0]['todos']), 2)
        self.assertEqual(len(list(self.alpha.receipts.glob('*.json'))), 1)
        # Same receipt remains recoverable after changing transport.
        source = dict(self.sources[0], transports=dict(http=False, filesystem=True))
        result = filesystem(source, validate, blocked['request'], blocked['preflight'])
        self.assertEqual(len(result['data']['todos']), 2)

    def test_discovery_serializes_membership_refresh_during_route(self):
        router = self.discovered_router()
        entered, release, discovered = threading.Event(), threading.Event(), threading.Event()
        original = router.transfer
        def paused(source, path='/api/state', *args, **kwargs):
            if path == '/api/changes':
                entered.set()
                if not release.wait(3):
                    raise ValueError('Test timed out waiting for refresh')
            return original(source, path, *args, **kwargs)
        outcomes = []
        with patch.object(router, 'transfer', side_effect=paused):
            worker = threading.Thread(target=lambda: outcomes.append(router.route(self.request())))
            worker.start()
            self.assertTrue(entered.wait(2))
            router.store.context['search_paths'] = [str(self.root / 'beta/config.json')]
            refresh = threading.Thread(target=lambda: (router.refresh_discovery(), discovered.set()))
            refresh.start()
            self.assertFalse(discovered.wait(0.05))
            # Cached UI reads remain nonblocking while route/discovery is busy.
            self.assertTrue(router.view()['sources'])
            release.set(); worker.join(3); refresh.join(3)
        self.assertFalse(worker.is_alive()); self.assertFalse(refresh.is_alive())
        self.assertEqual(outcomes[0]['idea']['routing']['status'], 'routed')
        self.assertEqual(router.entries['alpha']['status'], 'removed')

    def test_discovery_old_refresh_cannot_resurrect_removed_source(self):
        router = self.discovered_router()
        self.refreshed(router)
        original = router.inspect_source
        entered, release = threading.Event(), threading.Event()
        def slow(source):
            snapshot = original(source)
            entered.set(); release.wait(3)
            return snapshot
        with patch.object(router, 'inspect_source', side_effect=slow):
            worker = threading.Thread(target=router.refresh_source, args=(router.sources[0],))
            worker.start(); self.assertTrue(entered.wait(2))
            self.alpha.config.unlink()
            router.refresh_discovery()
            release.set(); worker.join(3)
        self.assertEqual(router.entries['alpha']['status'], 'removed')
        self.assertIsNotNone(router.entries['alpha']['data'])

    def test_discovery_added_sources_selection_and_changed_identity_block(self):
        router = self.discovered_router(omit_beta=True)
        config = self.hidden_beta
        self.assertNotIn('beta', router.entries)
        self.beta.config.write_bytes(config)
        router.next_discovery = 0
        completed = threading.Event()
        original = router.refresh_discovery
        def refreshed():
            original(); completed.set()
        with patch.object(router, 'refresh_discovery', side_effect=refreshed):
            router.view()
            self.assertTrue(completed.wait(3))
        self.assertIn('beta', router.entries)
        idea = self.inbox.read()[0]['ideas'][0]
        self.inbox.mutate(dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
            collection='ideas', id=idea['id'], revision=digest(idea), record=dict(idea, selected_project='beta'))]))
        self.assertEqual(router.route(self.request('beta'))['idea']['routing']['status'], 'routed')
        # Reusing a retained project ID with a different service is never accepted.
        changed = json.loads(config)
        changed['aggregation_source']['url'] = 'http://127.0.0.1:1'
        self.beta.config.write_text(json.dumps(changed)); router.refresh_discovery()
        self.assertEqual(router.entries['beta']['status'], 'removed')
        self.assertTrue(any('changed' in r['error'] for r in router.discovery_reports))

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

    def test_live_pending_processing_and_retry_after_transport_switch(self):
        from processing import pending
        source = self.sources[0]
        snapshot = self.router.inspect_source(source)
        self.assertEqual(pending(snapshot), [])
        expected = preflight_context(snapshot['context'])
        capture = dict(request_id=uuid.uuid4().hex, actor='Test', changes=[dict(
            collection='ideas', id=None, record=dict(author='Requester',
            date_entered='2026-09-07T12:00:00Z', text='Added after briefing copy'))])
        self.router.transfer(source, '/api/changes', capture, snapshot.get('token'), expected)
        fresh = self.router.inspect_source(source)
        ideas = pending(fresh)
        self.assertEqual(len(ideas), 1)
        originals = copy.deepcopy(fresh['data']['ideas'])
        todo = dict(fresh['data']['todos'][0])
        todo.pop('id')
        todo.update(source_ideas=[ideas[0]['id']], author=ideas[0]['author'], created_by='Test')
        request = dict(request_id=uuid.uuid4().hex, actor='Test', changes=[dict(
            collection='todos', id=None, record=todo)])
        result = self.router.transfer(source, '/api/changes', request, fresh.get('token'), expected)
        switched = dict(source, transports=dict(http=False, filesystem=True))
        retry = self.router.transfer(switched, '/api/changes', request, None, expected)
        self.assertEqual(retry['assigned'], result['assigned'])
        with self.assertRaises((ValueError, Conflict)):
            self.router.transfer(switched, '/api/changes', dict(request, request_id=uuid.uuid4().hex), None, expected)
        final = self.router.inspect_source(switched)
        self.assertEqual(pending(final), [])
        self.assertEqual(final['data']['ideas'], originals)
        self.assertEqual(len(final['data']['todos']), len(fresh['data']['todos']) + 1)

    def test_categories_save_reload_conflict_and_transport_retry(self):
        from categories import CATEGORIES
        source = self.sources[0]
        for category in [*CATEGORIES, '']:
            snapshot = self.router.inspect_source(source)
            old = snapshot['data']['todos'][0]
            request = dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=old['id'], revision=digest(old), record=dict(old, category=category))])
            expected = preflight_context(snapshot['context'])
            self.router.transfer(source, '/api/changes', request, snapshot.get('token'), expected)
            switched = dict(source, transports=dict(http=False, filesystem=True))
            retry = self.router.transfer(switched, '/api/changes', request, None, expected)
            saved = retry['data']['todos'][0]
            self.assertEqual(saved['category'], category)
            for field in ('author', 'created_by', 'group', 'status', 'source_ideas'):
                self.assertEqual(saved[field], old[field])
            self.assertEqual(self.router.inspect_source(switched)['data']['todos'][0], saved)
            stale = dict(request, request_id=uuid.uuid4().hex)
            with self.assertRaises((ValueError, Conflict)):
                self.router.transfer(source, '/api/changes', stale, snapshot.get('token'), expected)
        snapshot = self.router.inspect_source(source)
        old = snapshot['data']['todos'][0]
        for invalid in ('unknown', None, [], 7):
            request = dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=old['id'], revision=digest(old), record=dict(old, category=invalid))])
            with self.assertRaises((ValueError, Conflict)):
                self.router.transfer(source, '/api/changes', request, snapshot.get('token'), preflight_context(snapshot['context']))

    def test_routed_effort_default_and_explicit_selection(self):
        from efforts import EFFORTS, processing_guidance
        self.assertIn('medium for ordinary work or unclear complexity', processing_guidance())
        for effort in [None, *EFFORTS]:
            if effort is not None:
                self.inbox.mutate(dict(actor='SL', request_id=uuid.uuid4().hex, changes=[dict(
                    collection='ideas', id=None, record=dict(author='SL', text='Another scoped task', date_entered='2026-09-08T00:00:00Z'))]))
            idea = self.inbox.read()[0]['ideas'][-1]
            request = self.request()
            request.update(idea_id=idea['id'], revision=digest(idea))
            if effort is not None:
                request['todo']['effort'] = effort
            result = self.router.route(request)
            saved = self.alpha.read()[0]['todos'][-1]
            self.assertEqual(saved['effort'], effort or 'medium')
            self.assertEqual(saved['source_refs'][0]['idea']['text'], idea['text'])
            self.assertEqual(saved['source_refs'][0]['idea']['captured_system'], idea['captured_system'])
            self.assertEqual(self.router.route(request), result)
            detail = self.router.source_record(dict(project_id='alpha', todo_id=saved['id']))
            self.assertEqual(detail['todo']['effort'], effort or 'medium')
            self.assertEqual(detail['context']['project_id'], 'alpha')

    def test_effort_creation_edit_conflict_and_switch(self):
        from efforts import EFFORTS
        source = self.sources[0]
        snapshot = self.router.inspect_source(source)
        expected = preflight_context(snapshot['context'])
        for effort in [None, *EFFORTS]:
            record = dict(name='Effort task', description='Disposable processing result', author='SL',
                          created_by='Codex', date_entered='2026-09-08T00:00:00Z', extension={'keep': True})
            if effort is not None:
                record['effort'] = effort
            request = dict(actor='Codex', request_id=uuid.uuid4().hex,
                           changes=[dict(collection='todos', id=None, record=record)])
            result = self.router.transfer(source, '/api/changes', request, snapshot.get('token'), expected)
            old = result['data']['todos'][-1]
            self.assertEqual(old['effort'], effort or 'medium')
            request = dict(actor='SL', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=old['id'], revision=digest(old), record=dict(old, effort='high', group='Changed'))])
            saved = self.router.transfer(source, '/api/changes', request, snapshot.get('token'), expected)['data']['todos'][-1]
            switched = dict(source, transports=dict(http=False, filesystem=True))
            self.assertEqual(self.router.transfer(switched, '/api/changes', request, None, expected)['data']['todos'][-1], saved)
            self.assertEqual(self.router.inspect_source(switched)['data']['todos'][-1], saved)
            with self.assertRaises((ValueError, Conflict)):
                self.router.transfer(source, '/api/changes', dict(request, request_id=uuid.uuid4().hex), snapshot.get('token'), expected)
            # An old client omitting effort retains it, including recovery from a conflict.
            edited = dict(saved, group='Recovered'); edited.pop('effort')
            request = dict(actor='SL', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=saved['id'], revision=digest(saved), record=edited)])
            recovered = self.router.transfer(source, '/api/changes', request, snapshot.get('token'), expected)['data']['todos'][-1]
            self.assertEqual(recovered['effort'], 'high')
            for field in ('author', 'created_by', 'source_ideas', 'extension'):
                self.assertEqual(recovered[field], old[field])
            for invalid in ('', 'unsupported', None, [], 1):
                request = dict(actor='SL', request_id=uuid.uuid4().hex, changes=[dict(
                    collection='todos', id=recovered['id'], revision=digest(recovered), record=dict(recovered, effort=invalid))])
                with self.assertRaises((ValueError, Conflict)):
                    self.router.transfer(source, '/api/changes', request, snapshot.get('token'), expected)

    def test_workflow_claims_cannot_be_forged_by_either_transport(self):
        source = self.sources[0]
        snapshot = self.router.inspect_source(source)
        old = snapshot['data']['todos'][0]
        request = dict(request_id=uuid.uuid4().hex, actor='Test', changes=[dict(collection='todos',
            id=old['id'], revision=digest(old), record=dict(old, workflow={'phase':'done'}))])
        with self.assertRaises((ValueError, Conflict)):
            self.router.transfer(source, '/api/changes', request, snapshot.get('token'), preflight_context(snapshot['context']))
        self.assertNotIn('workflow', self.alpha.read()[0]['todos'][0])

    def test_untested_merge_claim_survives_transport_switch_and_retry(self):
        source = self.sources[0]
        snapshot = self.alpha.snapshot()
        old = snapshot['data']['todos'][0]
        claim = dict(phase='merging', commit='a'*40, run_id='b'*32, system='c'*64,
                     repository=str(self.alpha.root), worktree=str(self.alpha.root/'preview'),
                     branch=f"codex/{old['id'].lower()}-bbbbbbbb", base='d'*40, message='Merging reviewed commit')
        self.alpha.mutate(dict(actor='Codex', request_id=uuid.uuid4().hex, changes=[dict(
            collection='todos', id=old['id'], revision=digest(old), record=dict(old, workflow=claim))]), workflow=True)
        snapshot = self.router.inspect_source(source)
        old = snapshot['data']['todos'][0]
        request = dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
            collection='todos', id=old['id'], revision=digest(old), record=dict(old, priority='high', effort='xhigh'))])
        expected = preflight_context(snapshot['context'])
        self.router.transfer(source, '/api/changes', request, snapshot.get('token'), expected)
        switched = dict(source, transports=dict(http=False, filesystem=True))
        retry = self.router.transfer(switched, '/api/changes', request, None, expected)
        self.assertEqual(retry['data']['todos'][0]['workflow'], claim)
        self.assertEqual(retry['data']['todos'][0]['effort'], 'xhigh')
        self.assertEqual(self.router.inspect_source(switched)['data']['todos'][0]['workflow'], claim)

    def test_queued_claim_evidence_survives_switch_and_cannot_be_changed(self):
        source = self.sources[0]
        snapshot = self.alpha.snapshot(); old = snapshot['data']['todos'][0]
        claim = dict(phase='merge_queued', queued_action='merge', queued_at='2026-09-08T00:00:00Z',
                     commit='a'*40, run_id='b'*32, system='c'*64, repository=str(self.alpha.root),
                     worktree=str(self.alpha.root/'preview'), branch=f"codex/{old['id'].lower()}-bbbbbbbb",
                     base='d'*40, message='Waiting', action_requests={'request':'e'*64},
                     pr_url='https://github.com/test/code/pull/1', integration_commit='f'*40)
        self.alpha.mutate(dict(actor='Codex',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=old['id'],revision=digest(old),record=dict(old,workflow=claim))]),workflow=True)
        snapshot=self.router.inspect_source(source); old=snapshot['data']['todos'][0]
        request=dict(actor='Test',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=old['id'],revision=digest(old),record=dict(old,depends_on=[],priority='high'))])
        expected=preflight_context(snapshot['context'])
        self.router.transfer(source,'/api/changes',request,snapshot.get('token'),expected)
        switched=dict(source,transports=dict(http=False,filesystem=True))
        retry=self.router.transfer(switched,'/api/changes',request,None,expected)
        self.assertEqual(retry['data']['todos'][0]['workflow'],claim)
        current=retry['data']['todos'][0]
        forged=dict(actor='Test',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=old['id'],revision=digest(current),record=dict(current,workflow=dict(claim,phase='done')))])
        with self.assertRaises((ValueError,Conflict)):
            self.router.transfer(source,'/api/changes',forged,snapshot.get('token'),expected)

    def test_migration_review_survives_switch_and_cannot_be_forged(self):
        source = self.sources[0]
        snapshot = self.alpha.snapshot(); old = snapshot['data']['todos'][0]
        claim = dict(phase='migration_required', queued_action='migrate', queued_at='2026-09-08T00:00:00Z',
                     commit='a'*40, run_id='b'*32, system='c'*64, repository=str(self.alpha.root),
                     worktree=str(self.alpha.root/'preview'), branch=f"codex/{old['id'].lower()}-bbbbbbbb",
                     base='d'*40, message='Waiting', action_requests={'request':'e'*64},
                     pr_url='https://github.com/test/code/pull/1', integration_commit='f'*40, deployment_review=dict(review_id='a'*32, candidate_commit='f'*40, current_formats=['1.8.0'], target_format='1.9.0'))
        self.alpha.mutate(dict(actor='Codex',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=old['id'],revision=digest(old),record=dict(old,workflow=claim))]),workflow=True)
        snapshot=self.router.inspect_source(source); old=snapshot['data']['todos'][0]
        request=dict(actor='Test',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=old['id'],revision=digest(old),record=dict(old,depends_on=[],priority='high'))])
        expected=preflight_context(snapshot['context'])
        self.router.transfer(source,'/api/changes',request,snapshot.get('token'),expected)
        switched=dict(source,transports=dict(http=False,filesystem=True))
        retry=self.router.transfer(switched,'/api/changes',request,None,expected)
        self.assertEqual(retry['data']['todos'][0]['workflow'],claim)
        current=retry['data']['todos'][0]
        forged=dict(actor='Test',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=old['id'],revision=digest(current),record=dict(current,workflow=dict(claim,phase='done')))])
        with self.assertRaises((ValueError,Conflict)):
            self.router.transfer(source,'/api/changes',forged,snapshot.get('token'),expected)

    def test_resolution_evidence_survives_recovery_switch_and_protected_writes(self):
        source = self.sources[0]
        for phase in ('resolving_conflict', 'testing_resolution', 'resolution_blocked', 'restart_failed'):
            snapshot = self.alpha.snapshot(); old = snapshot['data']['todos'][0]
            claim = dict(phase=phase, run_id='b'*32, system='c'*64,
                         repository=str(self.alpha.root), worktree=str(self.alpha.root/'candidate'),
                         branch=f"codex/{old['id'].lower()}-bbbbbbbb", base='d'*40,
                         message='Retained resolution evidence', conflicted_paths=['versions.py'],
                         queued_at='2026-09-08T00:00:00Z', action_requests={'original':'scope'},
                         git_diagnostics=dict(stdout='CONFLICT', stderr='Recorded preimage'),
                         integration_attempt=dict(main='a'*40, remote='d'*40))
            self.alpha.mutate(dict(actor='Codex', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=old['id'], revision=digest(old), record=dict(old, workflow=claim))]), workflow=True)
            current = self.router.inspect_source(source)
            record = current['data']['todos'][0]
            expected = preflight_context(current['context'])
            request = dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=old['id'], revision=digest(record), record=dict(record, priority='high'))])
            self.router.transfer(source, '/api/changes', request, current.get('token'), expected)
            switched = dict(source, transports=dict(http=False, filesystem=True))
            retry = self.router.transfer(switched, '/api/changes', request, None, expected)
            self.assertEqual(retry['data']['todos'][0]['workflow'], claim)
            record = retry['data']['todos'][0]
            forged = dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=old['id'], revision=digest(record), record=dict(record, workflow=dict(claim, queue_skip=True)))])
            with self.assertRaises((ValueError, Conflict)):
                self.router.transfer(switched, '/api/changes', forged, None, expected)

    def test_external_completion_survives_recovery_switch_and_protected_writes(self):
        source = self.sources[0]
        for phase in ('implementation_failed', 'implementing'):
            snapshot = self.alpha.snapshot(); old = snapshot['data']['todos'][0]
            claim = dict(phase=phase, run_id='b'*32, system='c'*64,
                         repository=str(self.alpha.root), worktree=str(self.alpha.root/'candidate'),
                         branch=f"codex/{old['id'].lower()}-bbbbbbbb", base='d'*40,
                         message='Retained resolution evidence', conflicted_paths=['versions.py'],
                         queued_at='2026-09-08T00:00:00Z', action_requests={'original':'scope'},
                         git_diagnostics=dict(stdout='CONFLICT', stderr='Recorded preimage'),
                         integration_attempt=dict(main='a'*40, remote='d'*40))
            claim['external_completions'] = [dict(actor='SL', reason='Replacement work', at='2026-09-08T12:00:00Z', outcome='superseded', evidence=dict(integration=dict(status='unverified', commit='', message='Missing PR'), deployment=dict(status='unverified', commit='', message='Missing deployment')))]
            self.alpha.mutate(dict(actor='Codex', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=old['id'], revision=digest(old), record=dict(old, workflow=claim))]), workflow=True)
            current = self.router.inspect_source(source)
            record = current['data']['todos'][0]
            expected = preflight_context(current['context'])
            request = dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=old['id'], revision=digest(record), record=dict(record, priority='high'))])
            self.router.transfer(source, '/api/changes', request, current.get('token'), expected)
            switched = dict(source, transports=dict(http=False, filesystem=True))
            retry = self.router.transfer(switched, '/api/changes', request, None, expected)
            self.assertEqual(retry['data']['todos'][0]['workflow'], claim)
            record = retry['data']['todos'][0]
            forged = dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=old['id'], revision=digest(record), record=dict(record, workflow=dict(claim, external_completions=[])))])
            with self.assertRaises((ValueError, Conflict)):
                self.router.transfer(switched, '/api/changes', forged, None, expected)

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
