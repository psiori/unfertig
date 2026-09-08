"""External completion never executes the failed attempt or rewrites its outcome."""
import copy
import json
import os
import unittest
import uuid
from unittest.mock import patch

import test_workflow
from workflow import Workflow, scope_digest
from processing import system_id
from storage import Conflict, digest, atomic, encode
from versions import FORMAT_VERSION


class ExternalCompletionTests(unittest.TestCase):
    setUp = test_workflow.WorkflowTests.setUp
    git = test_workflow.WorkflowTests.git
    fake_github = test_workflow.WorkflowTests.fake_github

    def historical(self, phase='implementation_failed', closed=True):
        todo = self.store.snapshot()['data']['todos'][0]
        key = 'a' * 32
        run = dict(run_id=key, system=system_id(), repository=str(self.repo),
                   worktree=str(self.repo / '.worktrees/unfertig' / key),
                   branch='codex/t0001-' + key[:8], base=self.git('rev-parse', 'HEAD'),
                   phase=phase, message='Original failed attempt', scope=scope_digest(todo))
        fields = dict(status='closed', completion_summary='Completed elsewhere', closed_by='SL',
                      date_closed='2026-09-08T12:00:00Z') if closed else {}
        self.workflow.save(todo['id'], run, **fields)
        return self.store.snapshot()['data']['todos'][0]

    def request(self, todo, **fields):
        return dict(id=todo['id'], action='complete_external', revision=digest(todo),
                    request_id=uuid.uuid4().hex, actor='SL', reason='Replacement implementation', **fields)

    def test_closed_failure_is_history_and_retry_does_not_reopen(self):
        todo = self.historical()
        run = self.workflow.status()['runs'][todo['id']]
        self.assertEqual(run['phase'], 'historical')
        self.assertEqual(run['historical_phase'], 'implementation_failed')
        self.assertIn('Original failed attempt', run['message'])
        with self.assertRaisesRegex((ValueError, Conflict), 'closed'):
            self.workflow.start(dict(id=todo['id'], action='retry', revision=digest(todo)))
        self.assertEqual(self.store.snapshot()['data']['todos'][0], todo)

    def test_reopen_restores_unreconciled_retry_but_not_superseded_attempt(self):
        todo = self.historical()
        self.store.mutate(dict(actor='SL', request_id=uuid.uuid4().hex, changes=[dict(
            collection='todos', id=todo['id'], revision=digest(todo), record=dict(todo, status='open', closed_by='', date_closed=''))]))
        self.assertEqual(self.workflow.status()['runs'][todo['id']]['phase'], 'implementation_failed')
        todo = self.store.snapshot()['data']['todos'][0]
        self.workflow.start(self.request(todo))
        current = self.store.snapshot()['data']['todos'][0]
        self.assertEqual(self.workflow.status()['runs'][todo['id']]['phase'], 'superseded')
        with self.assertRaisesRegex(Conflict, 'superseded'):
            self.workflow.start(dict(id=todo['id'], action='retry', revision=digest(current)))

    def test_missing_evidence_idempotence_restart_and_original_preservation(self):
        todo = self.historical()
        body = self.request(todo)
        self.workflow.start(body)
        current = self.store.snapshot()['data']['todos'][0]
        for key, value in todo['workflow'].items():
            self.assertEqual(current['workflow'][key], value)
        for key in ('description','author','created_by','status','completion_summary'):
            self.assertEqual(current[key], todo[key])
        entry = current['workflow']['external_completions'][0]
        self.assertEqual(entry['actor'], 'SL')
        self.assertEqual(entry['evidence']['integration']['status'], 'unverified')
        self.assertEqual(entry['evidence']['deployment']['status'], 'unverified')
        restarted = Workflow(self.store, self.workflow.url, self.options, self.processing)
        restarted.start(body)
        self.assertEqual(self.store.snapshot()['data']['todos'][0], current)
        with self.assertRaises(Conflict):
            restarted.start(dict(body, reason='Changed payload'))
        with self.assertRaises(Conflict):
            restarted.start(dict(body, request_id=uuid.uuid4().hex))
        restarted.tick()
        self.assertEqual(self.store.snapshot()['data']['todos'][0], current)

    def test_live_uncertain_and_foreign_workers_are_not_hidden_or_superseded(self):
        for phase in ('implementation_failed', 'implementing'):
            todo = self.historical(phase)
            receipt = self.workflow.process_receipt(todo['workflow'])
            receipt.parent.mkdir(parents=True, exist_ok=True)
            for state in ('launching', 'running'):
                atomic(receipt, encode(dict(format_version=FORMAT_VERSION, run_id=todo['workflow']['run_id'],
                    state=state, pid=os.getpid(), identity=self.workflow.process_identity(os.getpid()))))
                view = self.workflow.status()['runs'][todo['id']]
                self.assertEqual(view['phase'], 'activity_unknown')
                self.assertFalse(view['can_complete_external'])
                with self.assertRaises(Conflict):
                    self.workflow.start(self.request(todo))
                self.assertTrue(receipt.exists())
            receipt.unlink()
        self.workflow.save(todo['id'], dict(todo['workflow'], system='f'*64))
        self.assertEqual(self.workflow.status()['runs'][todo['id']]['phase'], 'activity_unknown')

    def test_replacement_squash_merge_verified_without_old_commit_or_branch(self):
        todo = self.historical()
        merged = self.git('rev-parse', 'HEAD')
        pr = dict(url='https://github.com/test/code/pull/23', state='MERGED', headRefName='replacement',
                  baseRefName='main', headRefOid='b'*40, mergeCommit=dict(oid=merged))
        def github(*args, **kwargs):
            return json.dumps(dict(nameWithOwner='test/code') if args[0] == 'repo' else pr)
        body = self.request(todo, pr_url=pr['url'])
        with patch.object(self.workflow, 'github', side_effect=github), patch.object(self.workflow, 'launch') as launch:
            self.workflow.start(body)
            launch.assert_not_called()
        current = self.store.snapshot()['data']['todos'][0]
        evidence = current['workflow']['external_completions'][0]['evidence']
        self.assertEqual(evidence['integration']['commit'], merged)
        self.assertEqual(evidence['integration']['status'], 'verified')
        self.assertEqual(evidence['implementation_commit'], 'b'*40)
        self.assertEqual(evidence['deployment']['status'], 'unverified')
        self.assertNotIn('commit', current['workflow'])
        self.assertNotIn('published_commit', current['workflow'])
        self.assertFalse((self.repo / '.worktrees').exists())

    def test_contradictory_or_unmerged_pr_remains_explicitly_unverified(self):
        for state, extra in [('OPEN', {}), ('CLOSED', {}), ('MERGED', {'integration_commit':'b'*40}),
                             ('MERGED', {'implementation_commit':'c'*40})]:
            todo = self.historical()
            pr = dict(url='https://github.com/test/code/pull/23', state=state, headRefName='replacement',
                      baseRefName='main', headRefOid='d'*40, mergeCommit=dict(oid=self.git('rev-parse','HEAD')))
            with patch.object(self.workflow, 'github', side_effect=lambda *a, **k: json.dumps(dict(nameWithOwner='test/code') if a[0]=='repo' else pr)):
                self.workflow.start(self.request(todo, pr_url=pr['url'], **extra))
            evidence = self.store.snapshot()['data']['todos'][0]['workflow']['external_completions'][0]['evidence']
            self.assertEqual(evidence['integration']['status'], 'unverified')
            self.assertNotEqual(evidence['integration']['message'], 'No PR supplied.')

    def test_concurrent_filesystem_edit_during_evidence_check_is_not_overwritten(self):
        todo = self.historical()
        def changed(*args):
            self.store.mutate(dict(actor='Other', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=todo['id'], revision=digest(todo), record=dict(todo, priority='high'))]))
            return {}
        with patch('external_completion.evidence', side_effect=changed):
            with self.assertRaises(Conflict):
                self.workflow.start(self.request(todo))
        self.assertEqual(self.store.snapshot()['data']['todos'][0]['priority'], 'high')
        self.assertNotIn('external_completions', self.store.snapshot()['data']['todos'][0]['workflow'])

    def test_unavailable_or_foreign_pr_is_retained_without_verified_claims(self):
        for response in (ValueError('GitHub unavailable'), json.dumps(dict(nameWithOwner='different/repo'))):
            todo = self.historical()
            kwargs = dict(side_effect=response) if isinstance(response, Exception) else dict(return_value=response)
            with patch.object(self.workflow, 'github', **kwargs):
                self.workflow.start(self.request(todo, pr_url='https://github.com/test/code/pull/23'))
            saved = self.store.snapshot()['data']['todos'][0]['workflow']['external_completions'][0]['evidence']
            self.assertEqual(saved['pr_url'], 'https://github.com/test/code/pull/23')
            self.assertEqual(saved['integration']['status'], 'unverified')
            self.assertEqual(saved['deployment']['status'], 'unverified')

    def test_recovery_validation_rejects_malformed_external_references(self):
        from external_completion import validate
        todo = self.historical()
        self.workflow.start(self.request(todo))
        entries = self.store.snapshot()['data']['todos'][0]['workflow']['external_completions']
        for key, value in (('pr_url', 'javascript:alert(1)'), ('implementation_commit', 'not-a-commit')):
            malformed = copy.deepcopy(entries)
            malformed[0]['evidence'][key] = value
            with self.assertRaises(ValueError):
                validate(malformed)

    def test_interrupted_transaction_recovers_one_reconciliation(self):
        todo = self.historical()
        body = self.request(todo)
        real_atomic = __import__('storage').atomic
        def fail(path, content):
            if path.name == todo['id'] + '.json':
                raise OSError('Interrupted record write')
            return real_atomic(path, content)
        with patch('storage.atomic', side_effect=fail):
            with self.assertRaises(OSError):
                self.workflow.start(body)
        self.store.recover()
        self.store.commit_pending()
        self.workflow.start(body)
        current = self.store.snapshot()['data']['todos'][0]
        self.assertEqual(len(current['workflow']['external_completions']), 1)

    def test_deployment_checks_runtime_separately_and_never_invokes_recovery(self):
        from external_completion import deployment
        from io import BytesIO
        commit = 'a'*40
        root = self.root
        self.processing['working_directory'] = str(root)
        snapshot = dict(context=dict(data=self.store.context['data'], repository=str(root),
                       app_root=str(root/'tools/unfertig'), runtime_commit=commit),
                       compatibility=dict(read_only=False), history=dict(pending=False, enabled=True))
        def git(*args, **kwargs):
            if args == ('rev-parse','HEAD:unfertig'):raise AssertionError('Development pin is independent of deployment')
            return '' if args[0] in ('merge-base','status') else commit
        with patch.object(self.workflow, 'managed_unfertig', return_value=True), patch.object(self.workflow, 'git', side_effect=git), patch('external_completion.urlopen', side_effect=lambda *a, **k: BytesIO(json.dumps(snapshot).encode())), patch.object(self.workflow, 'host_deployment') as host:
            self.assertEqual(deployment(self.workflow, commit, 'b'*40)['status'], 'verified')
            snapshot['context']['runtime_commit'] = 'c'*40
            result = deployment(self.workflow, commit, 'b'*40)
            self.assertEqual(result['status'], 'unverified')
            self.assertIn('Runtime health', result['message'])
            self.assertEqual(deployment(self.workflow, commit, '')['status'], 'unverified')
            host.assert_not_called()

    def test_external_format_migration_preserves_history_and_protects_old_writer(self):
        from versions import migrate, inspect, MIGRATIONS
        todo = self.historical()
        self.workflow.start(self.request(todo))
        current = self.store.snapshot()['data']['todos'][0]
        old = dict(current, format_version='1.14.0', extension={'preserve':True})
        result = MIGRATIONS['1.14.0'](copy.deepcopy(old), 'todo')
        self.assertEqual(result, dict(old, format_version='1.15.0'))
        self.assertEqual(migrate(result, 'todo'), dict(result, format_version=FORMAT_VERSION))
        self.assertEqual(inspect(result, supported='1.14.0')[0], 'read_only')
        self.assertEqual(inspect(dict(result, format_version='1.21.1'))[0], 'compatible')

    def test_public_http_action_and_token_guard(self):
        from server import Server
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        import threading
        todo = self.historical()
        self.options.update(test=[], preview=[], restart=[])
        server = Server(('127.0.0.1', 0), self.store)
        server.workflow = self.workflow
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        body = self.request(todo)
        try:
            def request(token):
                return Request(f'http://127.0.0.1:{server.server_port}/api/workflow/action',
                    data=json.dumps(body).encode(), method='PUT', headers={
                        'X-Board-Token':token, 'Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as error:
                urlopen(request('wrong'))
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            with urlopen(request(server.token)) as response:
                self.assertEqual(json.load(response)['runs'][todo['id']]['phase'], 'superseded')
            with urlopen(request(server.token)) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(len(self.store.snapshot()['data']['todos'][0]['workflow']['external_completions']), 1)
        finally:
            server.shutdown(); server.server_close(); worker.join()

    def test_closed_ticket_keeps_genuinely_active_worker_visible(self):
        todo = self.historical('implementing')
        class LiveWorker:
            def is_alive(self): return True
        self.workflow.workers[todo['id']] = LiveWorker()
        self.workflow.live[todo['id']] = {'message':'Still working'}
        try:
            run = self.workflow.status()['runs'][todo['id']]
            self.assertEqual(run['phase'], 'implementing')
            self.assertTrue(run['active'])
            self.assertFalse(run['can_complete_external'])
            with self.assertRaises(ValueError):
                self.workflow.start(self.request(todo))
        finally:
            self.workflow.workers.clear(); self.workflow.live.clear()

    def test_inactive_closed_or_superseded_merge_does_not_drain_other_work(self):
        todo = self.historical('merging')
        next_record = {k:v for k,v in todo.items() if k not in ('id','workflow')}
        next_record.update(status='open', closed_by='', date_closed='', completion_summary='', source_ideas=[])
        self.store.mutate(dict(actor='SL', request_id=uuid.uuid4().hex, changes=[dict(
            collection='todos', id=None, record=next_record)]))
        other = self.store.snapshot()['data']['todos'][1]
        run = dict(todo['workflow'], run_id='b'*32, branch='codex/t0002-bbbbbbbb',
                   worktree=str(self.repo/'.worktrees/unfertig'/('b'*32)), phase='queued',
                   queued_action='implement', queued_at='2026-09-08T12:00:00Z', scope=scope_digest(other))
        self.workflow.save(other['id'], run)
        for reconciled in (False, True):
            if reconciled:
                self.workflow.start(self.request(todo))
            self.assertFalse(self.workflow.draining())
            with patch.object(self.workflow, 'launch') as launch:
                self.workflow.dispatch()
                self.assertEqual(launch.call_count, 1)
                self.assertEqual(launch.call_args.args[0]['id'], other['id'])
