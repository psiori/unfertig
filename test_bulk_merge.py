"""Batch actions against disposable boards, real Git and simulated GitHub."""
import copy
import json
from pathlib import Path
import threading
import unittest
import uuid
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import test_workflow as fixtures
import test_context_workflow as contexts
from bulk_merge import review, enqueue
from server import Server
from storage import Conflict, digest


class BulkMergeTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    git = fixtures.WorkflowTests.git
    fake_github = fixtures.WorkflowTests.fake_github
    run_stage = fixtures.WorkflowTests.run_stage
    add_ticket = fixtures.WorkflowTests.add_ticket
    start_ticket = fixtures.WorkflowTests.start_ticket
    await_workers = fixtures.WorkflowTests.await_workers
    restart_coordinator = fixtures.WorkflowTests.restart_coordinator
    await_receipt = fixtures.WorkflowTests.await_receipt

    def batch(self):
        result = review(self.workflow)
        return dict(repository=result['repository'], board=result['board'], entries=[e['action'] for e in result['entries']])

    def ready(self, count=1):
        self.run_stage('implement')
        for _ in range(count-1):
            self.start_ticket(self.add_ticket()); self.await_workers()
        return self.batch()

    def test_review_all_finished_is_read_only_and_explains_exclusions(self):
        batch = self.ready(2)
        second = batch['entries'][1]['id']
        self.workflow.save(second, dict(self.workflow.status()['runs'][second], phase='handoff_blocked'))
        before = self.store.snapshot()['data']
        result = review(self.workflow)
        self.assertEqual([e['id'] for e in result['entries']], ['T0001'])
        self.assertEqual(result['excluded'][0]['id'], second)
        self.assertIn('individual', result['excluded'][0]['reason'])
        self.assertEqual(self.store.snapshot()['data'], before)
        self.assertEqual(result['entries'][0]['repositories'][0]['commit'], batch['entries'][0]['commit'])
        self.assertNotIn('tested_commit', result['entries'][0]['action'])

    def test_partial_rejection_changed_remote_head_and_duplicate_receipts(self):
        batch = self.ready(2)
        second = batch['entries'][1]['id']
        original = self.workflow.pr_state
        def changed(run):
            value = original(run)
            if run['run_id'] == self.workflow.status()['runs'][second]['run_id']:
                value['headRefOid'] = 'f'*40
            return value
        with patch.object(self.workflow, 'pr_state', side_effect=changed), patch.object(self.workflow, 'dispatch') as dispatch:
            result = enqueue(self.workflow, batch)
            dispatch.assert_called_once()
        self.assertEqual([e['status'] for e in result['outcomes']], ['accepted', 'rejected'])
        self.assertIn('PR', result['outcomes'][1]['message'])
        self.assertEqual(self.workflow.status()['runs'][second]['phase'], 'ready')
        with patch.object(self.workflow, 'dispatch'):
            enqueue(self.workflow, batch)
            before = copy.deepcopy(self.store.snapshot()['data'])
            again = enqueue(self.workflow, batch)
            self.assertEqual([e['status'] for e in again['outcomes']], ['accepted', 'accepted'])
            self.assertEqual(self.store.snapshot()['data'], before)
        self.assertEqual(review(self.workflow)['entries'], [])
        forged = copy.deepcopy(batch); forged['entries'][0]['commit'] = 'f'*40
        with patch.object(self.workflow, 'dispatch'):
            self.assertEqual(enqueue(self.workflow, forged)['outcomes'][0]['status'], 'rejected')
        for run in self.workflow.status()['runs'].values():
            self.assertNotIn('tested_commit', run)

    def test_changed_scope_closed_foreign_dirty_and_preview_failure(self):
        batch = self.ready()
        run = self.workflow.status()['runs']['T0001']
        # Failed optional preview still permits integration without a test claim.
        self.workflow.save('T0001', dict(run, phase='test_failed'))
        self.assertEqual(len(review(self.workflow)['entries']), 1)
        with patch.object(self.workflow, 'dispatch'):
            result = enqueue(self.workflow, batch)
        self.assertEqual(result['outcomes'][0]['status'], 'rejected')  # revision changed
        dirty = Path(run['worktree'])/'unsaved'; dirty.write_text('draft')
        self.assertIn('uncommitted', review(self.workflow)['excluded'][0]['reason']); dirty.unlink()
        for changes, fields, reason in [({'system':'f'*64}, {}, 'another system'),
                                         ({'scope':'f'*64}, {}, 'scope changed'),
                                         ({}, {'status':'closed', 'closed_by':'Test', 'date_closed':'2026-09-08T12:00:00Z', 'completion_summary':'External work'}, 'Closed')]:
            if 'scope' in changes:
                with self.assertRaisesRegex(Conflict, 'scope changed'):
                    self.workflow.save('T0001', dict(run, **changes))
                # Seed historical inconsistent evidence through the fixture writer
                # to retain the bulk-review regression independently of save guards.
                todo = self.store.snapshot()['data']['todos'][0]
                self.store.mutate(dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(
                    collection='todos', id=todo['id'], revision=digest(todo), record=dict(todo, workflow=dict(run, **changes)))]), workflow=True)
            else:
                self.workflow.save('T0001', dict(run, **changes), **fields)
            self.assertIn(reason, review(self.workflow)['excluded'][0]['reason'])

    def test_closed_draft_merged_or_unavailable_pr_is_excluded(self):
        self.ready()
        run = self.workflow.status()['runs']['T0001']
        for changes in ({'state':'CLOSED'}, {'isDraft':True}, {'state':'MERGED'}):
            saved = copy.deepcopy(self.prs[run['pr_url']])
            self.prs[run['pr_url']].update(changes)
            self.assertFalse(review(self.workflow)['entries'])
            self.prs[run['pr_url']] = saved
        with patch.object(self.workflow, 'pr_state', side_effect=ValueError('GitHub unavailable')):
            self.assertIn('unavailable', review(self.workflow)['excluded'][0]['reason'])

    def test_uncertain_save_and_service_restart_replay_exact_requests(self):
        batch = self.ready(2)
        save = self.workflow.save
        def uncertain(ident, run, **fields):
            save(ident, run, **fields)
            if ident == 'T0001':
                raise OSError('Response lost after durable save')
        with patch.object(self.workflow, 'save', side_effect=uncertain), patch.object(self.workflow, 'dispatch'):
            result = enqueue(self.workflow, batch)
        self.assertEqual([e['status'] for e in result['outcomes']], ['unknown', 'accepted'])
        before = copy.deepcopy(self.store.snapshot()['data'])
        self.restart_coordinator()
        with patch.object(self.workflow, 'dispatch'):
            result = enqueue(self.workflow, batch)
        self.assertEqual([e['status'] for e in result['outcomes']], ['accepted', 'accepted'])
        self.assertEqual(self.store.snapshot()['data'], before)

    def test_batch_uses_existing_pause_restart_recovery_and_candidate_checks(self):
        batch = self.ready(2)
        ids = [e['id'] for e in batch['entries']]
        git = self.workflow.git
        def fail_publication(*args, **kwargs):
            if args[0] == 'push' and args[-1].endswith(':refs/heads/main'):
                raise OSError('Publication response lost')
            return git(*args, **kwargs)
        with patch.object(self.workflow, 'git', side_effect=fail_publication):
            enqueue(self.workflow, batch)
            self.await_workers()
        self.restart_coordinator(); self.workflow.tick()
        state = self.workflow.status()
        self.assertEqual(state['runs'][ids[0]]['phase'], 'push_failed')
        self.assertEqual(state['queue_blocked_by'], ids[0])
        self.assertEqual(state['runs'][ids[1]]['waiting_for'], ids[0])
        first = state['runs'][ids[0]]
        self.assertEqual(first['integration_tested_commit'], first['merge_commit'])
        self.assertNotIn('published_commit', first)
        with patch.object(self.workflow, 'dispatch'):
            self.assertTrue(all(e['status']=='accepted' for e in enqueue(self.workflow, batch)['outcomes']))
        self.assertEqual(self.workflow.status()['runs'][ids[1]]['phase'], 'merge_queued')
        self.start_ticket(ids[0], 'skip'); self.await_workers()
        self.assertEqual(self.workflow.status()['runs'][ids[0]]['phase'], 'push_failed')
        self.assertEqual(self.workflow.status()['runs'][ids[1]]['phase'], 'done')

    def test_batch_publication_survives_restart_without_legacy_deployment(self):
        batch = self.ready(2)
        ids = [e['id'] for e in batch['entries']]
        self.options['restart'] = ['/usr/bin/false']
        enqueue(self.workflow, batch)
        self.await_workers(); self.restart_coordinator(); self.workflow.tick(); self.await_workers()
        state = self.workflow.status()
        self.assertIsNone(state['queue_blocked_by'])
        for ident in ids:
            run = state['runs'][ident]
            self.assertEqual(run['phase'], 'done')
            self.assertEqual(run['completion_boundary'], 'publication')
            self.assertEqual(run['integration_tested_commit'], run['published_commit'])
            self.assertNotIn('tested_commit', run)
        with patch.object(self.workflow, 'dispatch'):
            self.assertTrue(all(e['status']=='accepted' for e in enqueue(self.workflow, batch)['outcomes']))

    def test_restart_drain_rejects_batch_without_creating_claims(self):
        batch = self.ready()
        before = copy.deepcopy(self.store.snapshot()['data'])
        self.workflow.restart_pending = True
        result = enqueue(self.workflow, batch)
        self.assertEqual(result['outcomes'][0]['status'], 'rejected')
        self.assertIn('Restart pending', result['outcomes'][0]['message'])
        self.assertEqual(self.store.snapshot()['data'], before)

    def test_empty_batch_wrong_owner_duplicate_ids_and_feature_gate(self):
        batch = self.batch()
        self.assertEqual(enqueue(self.workflow, batch)['outcomes'], [])
        with self.assertRaises(Conflict): enqueue(self.workflow, dict(batch, board='/other/board'))
        batch = self.ready()
        with self.assertRaises(ValueError): enqueue(self.workflow, dict(batch, entries=batch['entries']*2))
        self.options['enabled'] = False
        with self.assertRaises(ValueError): review(self.workflow)
        with self.assertRaises(ValueError): enqueue(self.workflow, batch)

    def test_http_review_batch_token_and_disabled_guards(self):
        self.ready()
        server = Server(('127.0.0.1', 0), self.store); server.workflow = self.workflow
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        def send(body, token):
            request = Request(origin+'/api/workflow/merge-batch', data=json.dumps(body).encode(), method='PUT',
                              headers={'Content-Type':'application/json', 'X-Board-Token':token})
            return json.load(urlopen(request))
        try:
            result = json.load(urlopen(origin+'/api/workflow/merge-review'))
            batch = dict(repository=result['repository'], board=result['board'], entries=[e['action'] for e in result['entries']])
            with self.assertRaises(HTTPError) as error: send(batch, 'wrong')
            self.assertEqual(error.exception.code, 403)
            with patch.object(self.workflow, 'dispatch'):
                self.assertEqual(send(batch, server.token)['outcomes'][0]['status'], 'accepted')
            self.options['enabled'] = False
            with self.assertRaises(HTTPError) as error: send(batch, server.token)
            self.assertEqual(error.exception.code, 403)
            self.assertIn(b'const button', urlopen(origin+'/bulk-merge.js').read())
        finally:
            server.shutdown(); server.server_close(); worker.join()


class ContextBatchTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    git = fixtures.WorkflowTests.git
    fake_github = contexts.ContextWorkflowTests.fake_github
    run_stage = contexts.ContextWorkflowTests.run_stage
    um = contexts.ContextWorkflowTests.um
    agent = contexts.ContextWorkflowTests.agent
    implement = contexts.ContextWorkflowTests.implement

    def test_context_only_review_includes_unchanged_child_and_exact_heads(self):
        self.um(); todo = self.implement()
        result = review(self.workflow)
        self.assertEqual(len(result['entries']), 1, result)
        entry = result['entries'][0]
        self.assertEqual(len(entry['repositories']), 2)
        self.assertEqual(entry['action']['repositories'], todo['workflow']['repository_heads'])
        batch = dict(repository=result['repository'], board=result['board'], entries=[entry['action']])
        bad = copy.deepcopy(batch); bad['entries'][0]['repositories']['context'] = 'f'*40
        with patch.object(self.workflow, 'dispatch'):
            self.assertEqual(enqueue(self.workflow, bad)['outcomes'][0]['status'], 'rejected')
            self.assertEqual(enqueue(self.workflow, batch)['outcomes'][0]['status'], 'accepted')

    def test_changed_secondary_pr_excludes_whole_todo(self):
        self.um(); self.changed = {'context', 'project'}; todo = self.implement()
        result = review(self.workflow); self.assertEqual(len(result['entries']), 1, result)
        context = next(r for r in todo['workflow']['repositories'] if r['id']=='context')
        # The proxy calls the class method, so change GitHub's ready state.
        self.prs[context['pr_url']]['isDraft'] = True
        self.assertFalse(review(self.workflow)['entries'])
