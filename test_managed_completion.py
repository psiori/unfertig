"""Reproduce T0018 using real local commits/remotes and retained worker reports."""
import copy
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_workflow
from workflow import Workflow
from storage import Conflict, digest, atomic, encode
from versions import FORMAT_VERSION, migrate, MIGRATIONS, inspect
from managed_completion import read_report, result_path, finish, publish


class ManagedCompletionTests(unittest.TestCase):
    setUp = test_workflow.WorkflowTests.setUp
    git = test_workflow.WorkflowTests.git
    fake_github = test_workflow.WorkflowTests.fake_github
    run_stage = test_workflow.WorkflowTests.run_stage

    def retained(self, structured=True, blockers=None):
        todo = self.store.snapshot()['data']['todos'][0]
        if 'workflow' not in todo:
            todo = self.run_stage('implement')
        else:
            run = dict(todo['workflow'], phase='ready')
            self.workflow.save(todo['id'], run)
            todo = self.store.snapshot()['data']['todos'][0]
        self.assertEqual(todo['workflow']['phase'],'ready',todo['workflow']['message'])
        run = todo['workflow']
        report, _ = read_report(run)
        report.update(status='needs_attention', limitations=['Worker publication approval rejected; coordinator published the commit.'])
        if structured:
            report.update(implementation='complete', blockers=blockers or ['publication_approval'], approval='publication')
        result_path(run).write_text(json.dumps(report)+'\nUNFERTIG_NEEDS_ATTENTION\n')
        run.update(phase='implementation_failed', message='Historical incomplete report', publication=dict(status='blocked', message='Approval rejected'))
        self.workflow.save(todo['id'],run)
        return self.store.snapshot()['data']['todos'][0]

    def request(self,todo,**extra):
        run=todo['workflow']; report, fingerprint=read_report(run)
        return dict(id=todo['id'],action='verify_existing',revision=digest(todo),request_id=uuid.uuid4().hex,
                    actor='SL',reason='Reviewed exact report: publication was sole blocker',commit=report['commit'],
                    report_digest=fingerprint,publication_only=True,**extra)

    def resume(self,todo,body=None):
        body=body or self.request(todo)
        self.workflow.start(body)
        self.workflow.workers[todo['id']].join(20)
        self.assertFalse(self.workflow.workers[todo['id']].is_alive())
        return self.store.snapshot()['data']['todos'][0]

    def test_t0018_legacy_report_reproduces_old_gate_and_resumes_without_worker(self):
        todo=self.retained(structured=False)
        original=result_path(todo['workflow']).read_text()
        # The old completion gate rejects even after the coordinator published HEAD.
        self.assertFalse(original.rstrip().endswith('UNFERTIG_IMPLEMENTATION_COMPLETE'))
        self.assertEqual(self.git('ls-remote','origin',todo['workflow']['branch']).split()[0],todo['workflow']['commit'])
        command=self.workflow.command
        calls=[]
        def verify_only(argv,*args,**kwargs):
            calls.append(argv)
            self.assertEqual(argv,self.options['test'])
            return command(argv,*args,**kwargs)
        body=self.request(todo)
        with patch.object(self.workflow,'command',side_effect=verify_only), patch.object(self.workflow,'ensure_pr',side_effect=AssertionError('duplicate PR')):
            current=self.resume(todo,body)
        run=current['workflow']
        self.assertEqual(run['phase'],'ready',run['message'])
        self.assertEqual(len(calls),1)
        self.assertEqual(run['verification'],dict(status='passed',commit=run['commit']))
        self.assertEqual(result_path(run).read_text(),original)
        self.assertEqual(len(self.prs),1)
        self.assertTrue(any(h.get('message')=='Historical incomplete report' for h in run['handoff_history']))
        restarted=Workflow(self.store,self.workflow.url,self.options,self.processing)
        restarted.start(body)
        self.assertEqual(self.store.snapshot()['data']['todos'][0],current)
        with self.assertRaises(Conflict): restarted.start(dict(body,reason='different'))

    def test_structured_publication_only_proceeds_after_confirmed_remote(self):
        todo=self.retained(blockers=['publication'])
        run=copy.deepcopy(todo['workflow'])
        with patch.object(self.workflow,'command') as checks:
            finish(self.workflow,todo,run)
        self.assertEqual(run['phase'],'ready')
        checks.assert_called_once()
        self.assertEqual(run['implementation']['status'],'complete')
        self.assertEqual(run['publication']['status'],'confirmed')

    def test_unresolved_approval_stays_separate_and_does_not_publish(self):
        todo=self.retained(); run=copy.deepcopy(todo['workflow'])
        with patch('managed_completion.publish') as publication, patch.object(self.workflow,'command') as checks:
            with self.assertRaisesRegex(Conflict,'approval discrepancy'): finish(self.workflow,todo,run)
        publication.assert_not_called(); checks.assert_not_called()
        self.assertEqual(run['implementation']['status'],'complete')
        self.assertEqual(run['approval']['status'],'blocked')

    def test_genuine_blocker_cannot_be_waived_by_publication_review(self):
        for blocker in ('implementation','verification','execution_approval'):
            todo=self.retained(blockers=[blocker])
            current=self.resume(todo)
            self.assertNotEqual(current['workflow']['phase'],'ready')
            self.assertEqual(current['workflow']['implementation']['status'],'complete' if blocker == 'execution_approval' else 'blocked')

    def test_failed_verification_preserves_implementation_and_report(self):
        todo=self.retained()
        with patch.object(self.workflow,'command',side_effect=ValueError('regression failed')):
            current=self.resume(todo)
        run=current['workflow']
        self.assertEqual(run['phase'],'handoff_blocked')
        self.assertEqual(run['implementation']['status'],'complete')
        self.assertEqual(run['verification']['status'],'failed')
        self.assertIn('regression failed',run['message'])
        self.assertTrue(self.workflow.status()['runs'][todo['id']]['can_verify_existing'])

    def test_stale_scope_revision_report_and_commit_are_rejected(self):
        todo=self.retained(); body=self.request(todo)
        for key,value in [('revision','stale'),('report_digest','changed'),('commit','b'*40)]:
            with self.assertRaises(Conflict): self.workflow.start(dict(body,**{key:value}))
        old=todo
        self.store.mutate(dict(actor='SL',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=todo['id'],revision=digest(todo),record=dict(todo,description='changed'))]))
        todo=self.store.snapshot()['data']['todos'][0]
        with self.assertRaisesRegex(Conflict,'scope'): self.workflow.start(self.request(todo))
        self.assertEqual(result_path(old['workflow']).read_text(),result_path(todo['workflow']).read_text())

    def test_active_uncertain_dirty_and_wrong_worktree_block(self):
        todo=self.retained(); run=todo['workflow']; path=self.workflow.process_receipt(run)
        previous=path.read_bytes()
        for state in ('launching','running'):
            atomic(path,encode(dict(format_version=FORMAT_VERSION,run_id=run['run_id'],state=state,pid=os.getpid(),identity=self.workflow.process_identity(os.getpid()))))
            self.assertFalse(self.workflow.status()['runs'][todo['id']]['can_verify_existing'])
            with self.assertRaises(Conflict): self.workflow.start(self.request(todo))
        path.write_bytes(previous)
        (Path(run['worktree'])/'dirty').write_text('retain')
        with self.assertRaisesRegex(ValueError,'uncommitted'): self.workflow.start(self.request(todo))

    def test_remote_mismatch_during_verification_does_not_mark_ready(self):
        todo=self.retained(); real=self.workflow.pr_state; count=0
        def changed(run):
            nonlocal count
            count+=1
            state=real(run)
            if count>=3: state['headRefOid']='a'*40
            return state
        with patch.object(self.workflow,'pr_state',side_effect=changed): current=self.resume(todo)
        self.assertEqual(current['workflow']['phase'],'handoff_blocked')
        self.assertEqual(current['workflow']['verification']['status'],'failed')

    def test_failed_push_requires_owner_action_not_automatic_retry(self):
        todo=self.retained(); run=copy.deepcopy(todo['workflow'])
        run['publication']=dict(status='pending')
        remote=self.workflow.pr_state(run)
        remote['headRefOid']=run['kickoff_commit']
        real=self.workflow.git; calls=[]
        def failed(*args,**kwargs):
            if args[0]=='push': calls.append(args); raise ValueError('approval rejected')
            return real(*args,**kwargs)
        with patch.object(self.workflow,'pr_state',return_value=remote),patch.object(self.workflow,'git',side_effect=failed):
            with self.assertRaisesRegex(ValueError,'approval rejected'): publish(self.workflow,run,run['commit'])
            with self.assertRaisesRegex(Conflict,'blocked'): publish(self.workflow,run,run['commit'])
        self.assertEqual(len(calls),1)
        self.assertEqual(run['publication']['status'],'blocked')

    def test_unknown_authorization_cannot_publish_even_matching_remote(self):
        todo=self.retained();run=copy.deepcopy(todo['workflow']);run.pop('publication_authorization')
        with patch.object(self.workflow,'git') as git:
            with self.assertRaisesRegex(Conflict,'authorization'): publish(self.workflow,run,run['commit'])
        git.assert_not_called()

    def test_migration_preserves_evidence_no_invented_permission(self):
        old=dict(format_version='1.15.0',workflow=dict(phase='implementation_failed',message='Retained'),extension={'x':[1]})
        expected=dict(old,format_version=FORMAT_VERSION)
        self.assertEqual(MIGRATIONS['1.15.0'](copy.deepcopy(old),'todo'),dict(old,format_version='1.16.0'))
        self.assertEqual(migrate(old,'todo'),expected)
        self.assertEqual(migrate(expected,'todo'),expected)
        self.assertEqual(inspect(expected,supported='1.15.0')[0],'read_only')

    def test_recovery_claim_survives_interrupted_transaction_without_duplicate_worker(self):
        todo=self.retained();body=self.request(todo)
        real=__import__('storage').atomic
        def interrupted(path,content):
            if path.name==todo['id']+'.json': raise OSError('interrupted')
            return real(path,content)
        with patch('storage.atomic',side_effect=interrupted):
            with self.assertRaises(OSError): self.workflow.start(body)
        self.store.recover(); self.store.commit_pending()
        restarted=Workflow(self.store,self.workflow.url,self.options,self.processing)
        with patch.object(restarted,'launch') as launch:
            restarted.start(body); launch.assert_not_called()
            restarted.dispatch(); launch.assert_called_once()
            self.assertEqual(launch.call_args.args[2],'verify_existing')

    def test_lost_push_response_reconciles_confirmed_head_without_second_push(self):
        todo=self.retained(); run=copy.deepcopy(todo['workflow'])
        run['publication']=dict(status='pending')
        before=self.workflow.pr_state(run); before['headRefOid']=run['kickoff_commit']
        after=dict(before,headRefOid=run['commit'])
        real=self.workflow.git; pushes=[]
        def lost(*args,**kwargs):
            if args[0]=='push':
                pushes.append(args)
                real(*args,**kwargs)
                raise ValueError('lost response')
            return real(*args,**kwargs)
        with patch.object(self.workflow,'pr_state',side_effect=[before,after]),patch.object(self.workflow,'git',side_effect=lost):
            with self.assertRaisesRegex(ValueError,'lost response'): publish(self.workflow,run,run['commit'])
            self.assertEqual(publish(self.workflow,run,run['commit']),run['commit'])
        self.assertEqual(len(pushes),1)
        self.assertEqual(run['publication']['status'],'confirmed')

    def test_worker_receipt_missing_and_foreign_owner_block_recovery(self):
        todo=self.retained();run=todo['workflow']
        path=self.workflow.process_receipt(run); content=path.read_bytes(); path.unlink()
        with self.assertRaisesRegex(Conflict,'Missing worker'): self.workflow.start(self.request(todo))
        path.write_bytes(content)
        self.workflow.save(todo['id'],dict(run,system='f'*64))
        todo=self.store.snapshot()['data']['todos'][0]
        with self.assertRaisesRegex(ValueError,'another system'): self.workflow.start(self.request(todo))

    def test_failed_verification_can_resume_on_restart(self):
        todo=self.retained()
        self.options['test']=[__import__('sys').executable,'-c','raise SystemExit(1)']
        current=self.resume(todo)
        self.assertEqual(current['workflow']['verification']['status'],'failed')
        self.options['test']=[__import__('sys').executable,'-c','print("passed")']
        self.workflow=Workflow(self.store,self.workflow.url,self.options,self.processing)
        self.addCleanup(self.workflow.close)
        current=self.resume(current)
        self.assertEqual(current['workflow']['phase'],'ready',current['workflow']['message'])
