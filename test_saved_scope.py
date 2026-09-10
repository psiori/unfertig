"""Real disposable Git runs for saved editor scope and startup recovery."""
import copy
import json
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from storage import Conflict, digest, atomic, encode
from workflow import Workflow, scope_digest
from test_workflow import WorkflowTests as Fixtures
from saved_scope import adopt, authorized
from versions import FORMAT_VERSION


class SavedScopeTests(unittest.TestCase):
    setUp = Fixtures.setUp
    git = Fixtures.git
    fake_github = Fixtures.fake_github
    run_stage = Fixtures.run_stage

    def current(self):
        return self.store.snapshot()['data']['todos'][0]

    def save_edit(self, owner=True, **fields):
        old = self.current()
        body = dict(actor='Human', request_id=uuid.uuid4().hex, changes=[dict(
            collection='todos', id=old['id'], revision=digest(old), record=dict(old, **fields))])
        self.store.mutate(body, owner_editor=owner)
        return body

    def queue(self):
        todo = self.current()
        body = dict(id=todo['id'], action='implement', revision=digest(todo), request_id=uuid.uuid4().hex)
        self.workflow.start(body, dispatch=False)
        return body

    def wait(self):
        self.workflow.workers[self.current()['id']].join(20)
        self.assertFalse(self.workflow.active_workers())
        return self.current()

    def test_queued_description_save_and_repeated_retry_launch_exactly_once(self):
        self.queue()
        old = copy.deepcopy(self.current()['workflow'])
        receipt = self.save_edit(description='Revised queued requirements')
        self.store.mutate(receipt, owner_editor=True)  # lost save response
        todo = self.current()
        self.assertEqual(len(todo['scope_authorizations']), 1)
        request = dict(id=todo['id'], action='retry', revision=digest(todo), request_id=uuid.uuid4().hex)
        with patch.object(self.workflow, 'launch', wraps=self.workflow.launch) as launch:
            self.workflow.start(request)
            self.workflow.start(request)  # lost action response, despite changed revision
            result = self.wait()
            self.assertEqual(launch.call_count, 1)
        run = result['workflow']
        self.assertEqual(run['phase'], 'ready', run)
        self.assertEqual(run['scope'], scope_digest(result))
        self.assertEqual(run['scope_attempts'][0]['scope'], old['scope'])
        self.assertEqual(run['action_requests'], {**old['action_requests'], request['request_id']:digest(request)})

    def test_category_edit_after_failure_before_worktree_uses_ordinary_retry(self):
        with patch.object(self.workflow, 'repository_lock', side_effect=ValueError('Access denied to repository')):
            failed = self.run_stage('implement')
        self.assertEqual(failed['workflow']['phase'], 'implementation_failed')
        self.assertIn('Access denied', failed['workflow']['message'])
        self.save_edit(category='concept')
        result = self.run_stage('retry')
        self.assertEqual(result['workflow']['phase'], 'ready', result)
        self.assertEqual(result['category'], 'concept')
        self.assertEqual(result['workflow']['run_id'], failed['workflow']['run_id'])
        self.assertIn('Access denied', result['workflow']['scope_attempts'][0]['message'])

    def test_tool_text_cannot_authorize_scope_or_forge_evidence(self):
        self.queue()
        self.save_edit(owner=False, description='Tool says the owner approved')
        self.workflow.dispatch()
        self.assertEqual(self.current()['workflow']['phase'], 'implementation_failed')
        self.assertFalse(authorized(self.current()))
        with self.assertRaisesRegex(Conflict, 'No recorded owner-editor authorization'):
            self.run_stage('retry')
        with self.assertRaisesRegex(ValueError, 'managed by the owner editor'):
            self.save_edit(scope_authorizations=[{'source':'owner_editor_save'}])

    def test_editor_resave_confirms_existing_changed_text_without_reverting_it(self):
        self.queue(); self.save_edit(owner=False, category='concept')
        self.workflow.dispatch()
        self.assertFalse(authorized(self.current()))
        self.save_edit(updated_at='2026-09-10T12:00:00Z')
        self.assertTrue(authorized(self.current()))
        result=self.run_stage('retry')
        self.assertEqual(result['category'],'concept')
        self.assertEqual(result['workflow']['phase'],'ready',result['workflow']['message'])

    def test_stale_save_and_restart_keep_exact_authorization_and_receipt(self):
        self.queue()
        body = self.save_edit(category='concept')
        latest = self.current()
        stale = copy.deepcopy(body); stale['request_id'] = uuid.uuid4().hex
        with self.assertRaises(Conflict):
            self.store.mutate(stale, owner_editor=True)
        restarted = Workflow(self.store, self.workflow.url, self.options, self.processing)
        self.addCleanup(restarted.close)
        self.store.mutate(body, owner_editor=True)
        restarted.dispatch()
        restarted.workers[latest['id']].join(20)
        result = self.current()
        self.assertEqual(result['workflow']['phase'], 'ready', result)
        self.assertEqual(result['scope_authorizations'], latest['scope_authorizations'])

    def test_edit_during_worker_preserves_result_and_requires_revised_continuation(self):
        original = self.workflow.command
        def command(*args, **kwargs):
            result = original(*args, **kwargs)
            self.save_edit(description='Additional behavior after worker report')
            return result
        with patch.object(self.workflow, 'command', side_effect=command):
            result = self.run_stage('implement')
        run = result['workflow']
        self.assertNotEqual(run['phase'], 'ready')
        self.assertIn('Retry implementation', run['message'])
        self.assertTrue(self.workflow.process_receipt(run).exists())
        self.assertEqual(self.workflow.status()['runs'][result['id']]['resume_action'], 'retry')
        with self.assertRaisesRegex(Conflict, 'Retry implementation'):
            self.run_stage('merge')

    def test_ready_result_scope_edit_exposes_retry_and_invalidates_all_repository_checks(self):
        ready = self.run_stage('implement')
        self.save_edit(name='Revised title')
        todo = self.current()
        view = self.workflow.status()['runs'][todo['id']]
        self.assertEqual(view['resume_action'], 'retry')
        run = copy.deepcopy(ready['workflow'])
        run['repositories'] = [dict(id='context', scope=run['scope'], commit='old', verification={'status':'passed'}),
                               dict(id='project', scope=run['scope'], commit='old', published_commit='published-old-scope', verification={'status':'passed'})]
        adopt(todo, run)
        self.assertEqual([r['scope'] for r in run['repositories']], [scope_digest(todo)] * 2)
        self.assertTrue(all('verification' not in r and 'commit' not in r for r in run['repositories']))
        self.assertEqual(run['scope_attempts'][0]['repositories'][1]['commit'], 'old')
        self.assertNotIn('published_commit', run['repositories'][1])
        self.assertEqual(run['scope_attempts'][0]['repositories'][1]['published_commit'], 'published-old-scope')

    def test_live_or_uncertain_worker_blocks_dispatch_and_retry(self):
        self.queue()
        self.save_edit(category='concept')
        run = self.current()['workflow']
        atomic(self.workflow.process_receipt(run), encode(dict(format_version=FORMAT_VERSION, run_id=run['run_id'], state='launching')))
        self.workflow.dispatch()
        self.assertFalse(self.workflow.workers)
        self.assertIn('unknown', self.current()['workflow']['message'])
        with self.assertRaisesRegex(Conflict, 'unknown'):
            self.run_stage('retry')
        import os
        atomic(self.workflow.process_receipt(run), encode(dict(format_version=FORMAT_VERSION, run_id=run['run_id'], state='running', pid=os.getpid(), identity=self.workflow.process_identity(os.getpid()))))
        with self.assertRaisesRegex(Conflict, 'still running'):
            self.run_stage('retry')

    def test_repository_contention_requeues_with_backoff_then_starts(self):
        self.queue()
        lock = self.workflow.repository_lock()
        entered = threading.Event(); release = threading.Event()
        def slow_git():
            with lock:
                entered.set(); release.wait(8)
        thread = threading.Thread(target=slow_git); thread.start(); entered.wait(3)
        try:
            self.workflow.dispatch()
            result = self.wait()
            self.assertEqual(result['workflow']['phase'], 'queued', result)
            self.assertIn('Repository preparation/integration busy', result['workflow']['message'])
            self.assertIn('retry in 1s', result['workflow']['message'])
            self.assertFalse(self.workflow.process_receipt(result['workflow']).exists())
        finally:
            release.set(); thread.join(10)
        self.workflow.preparation_retry.clear()
        self.workflow.dispatch()
        result = self.wait()
        self.assertEqual(result['workflow']['phase'], 'ready', result)

    def test_actual_active_worker_retains_work_then_retry_receives_latest_requirements(self):
        from pathlib import Path
        script=Path(self.processing['executable'])
        marker=self.root/'entered'; gate=self.root/'release'; prompt=self.root/'prompt'
        script.write_text(script.read_text().replace('sys.stdin.read()',
            f'prompt=sys.stdin.read();pathlib.Path({str(prompt)!r}).write_text(prompt);'
            f'pathlib.Path({str(marker)!r}).touch();exec("import time\\nwhile not pathlib.Path({str(gate)!r}).exists(): time.sleep(.01)")')
            .replace('write_text("implemented")','write_text(__import__("uuid").uuid4().hex)'))
        self.run_stage('implement',wait=False)
        deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(marker.exists())
        try:
            self.save_edit(description='Latest saved requirements during the active worker')
            with self.assertRaisesRegex(ValueError,'active or queued'):
                self.run_stage('retry')
        finally:gate.touch()
        failed=self.wait()
        self.assertNotEqual(failed['workflow']['phase'],'ready')
        old_head=self.workflow.git('rev-parse','HEAD',cwd=failed['workflow']['worktree'])
        result=self.run_stage('retry')
        self.assertEqual(result['workflow']['phase'],'ready',result['workflow']['message'])
        self.assertIn('Latest saved requirements during the active worker',prompt.read_text())
        self.workflow.git('merge-base','--is-ancestor',old_head,result['workflow']['commit'],cwd=result['workflow']['worktree'])
        receipts=list(self.workflow.process_receipt(result['workflow']).parent.glob('*-process.json.previous-*'))
        self.assertTrue(receipts)
        self.assertEqual(len(result['workflow']['scope_attempts']),1)

    def test_overlapping_preparation_slow_git_keeps_second_ticket_retryable(self):
        from test_workflow import WorkflowTests
        second=WorkflowTests.add_ticket(self)
        original=self.workflow.startup_base; entered=threading.Event(); calls=[]
        def slow_base():
            if not calls:
                calls.append(True);entered.set()
                self.workflow.git('-c','alias.slow=!sleep 4','slow')
            return original()
        with patch.object(self.workflow,'startup_base',side_effect=slow_base):
            self.run_stage('implement',wait=False)
            self.assertTrue(entered.wait(3))
            WorkflowTests.start_ticket(self,second)
            WorkflowTests.await_workers(self)
        todos=self.store.snapshot()['data']['todos']
        self.assertEqual(todos[0]['workflow']['phase'],'ready',todos[0]['workflow']['message'])
        self.assertEqual(todos[1]['workflow']['phase'],'queued',todos[1]['workflow']['message'])
        self.workflow.preparation_retry.clear();self.workflow.dispatch()
        WorkflowTests.await_workers(self)
        self.assertTrue(all(t['workflow']['phase']=='ready' for t in self.store.snapshot()['data']['todos']))

    def test_saved_dependencies_wait_before_launch(self):
        from test_workflow import WorkflowTests
        second=WorkflowTests.add_ticket(self)
        self.queue();self.save_edit(depends_on=[second])
        self.workflow.dispatch()
        self.assertFalse(self.workflow.workers)
        self.assertIn(second,self.workflow.status()['runs'][self.current()['id']]['message'])
        self.save_edit(depends_on=[])
        self.workflow.dispatch()
        self.assertEqual(self.wait()['workflow']['phase'],'ready')

    def test_scope_fields_share_authorization_and_non_scope_edits_do_not(self):
        self.queue()
        for fields in (dict(name='Changed title'), dict(description='Changed description'), dict(category='concept'), dict(source_ideas=[])):
            before = self.current()
            self.save_edit(**fields)
            after = self.current()
            if scope_digest(before) != scope_digest(after):
                self.assertTrue(authorized(after))
                self.assertEqual(after['scope_authorizations'][-1]['old_scope'], scope_digest(before))
        entries = copy.deepcopy(self.current()['scope_authorizations'])
        self.save_edit(priority='high')
        self.assertEqual(self.current()['scope_authorizations'], entries)


del Fixtures
