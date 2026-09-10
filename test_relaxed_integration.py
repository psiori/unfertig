"""Real Git publication and repair, exclusively in disposable repositories."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import uuid

import test_workflow as fixtures
from relaxed_integration import Changes, DEFAULTS, settings
from storage import BoardStore, Conflict
from server import validate
from workflow import Workflow
from versions import FORMAT_VERSION, MIGRATIONS, migrate, inspect


class PolicyTests(unittest.TestCase):
    def test_defaults_validation_and_every_supported_migration(self):
        self.assertEqual(settings({}), DEFAULTS)
        for version in MIGRATIONS:
            original = dict(format_version=version, workflow={'integration':{'mode':'relaxed','extension':42}}, extra=[1])
            migrated = migrate(original, 'config')
            self.assertEqual(migrated['workflow']['integration'], {**DEFAULTS, 'mode':'relaxed','extension':42})
            self.assertEqual(original['workflow']['integration'], {'mode':'relaxed','extension':42})
            self.assertEqual(migrate(migrated, 'config'), migrated)
            self.assertEqual(migrated['extra'], [1])
        self.assertEqual(inspect({'format_version':FORMAT_VERSION}, supported='1.22.0')[0], 'read_only')
        for value in (None, [], {'mode':'loose'}, {'max_attempts':True}, {'max_attempts':0},
                      {'max_attempts':11}, {'automatic_repair':'true'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings(value)
        for value in (None, False, 'invalid'):
            self.assertEqual(migrate({'format_version':'1.22.0','workflow':{'integration':value}}, 'config')['workflow']['integration'], value)


class RelaxedTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    git = fixtures.WorkflowTests.git
    run_stage = fixtures.WorkflowTests.run_stage
    fake_github = fixtures.WorkflowTests.fake_github

    def relaxed(self, repair=True, attempts=3):
        self.options['integration'] = settings(dict(mode='relaxed', automatic_repair=repair, max_attempts=attempts))

    def embedded(self, submodule=False):
        self.workflow.close()
        self.store.close()
        if submodule:
            source = self.store.root
            self.git('-c','protocol.file.allow=always','submodule','add',str(source),'context')
            board = self.repo/'context'
            for key, value in [('user.name','Test'),('user.email','test@example.invalid')]:
                self.workflow.git('config',key,value,cwd=board)
        else:
            board = self.repo/'board'
            board.mkdir()
            (board/'data.json').write_text(json.dumps(fixtures.fixture()))
            (self.repo/'.gitignore').write_text('.server.lock\n.service.lock\n.operation.lock\n.receipts/\n.history-pending.json\n.transaction.json\n')
        self.git('add','.');self.git('commit','-qm','Embedded board');self.git('push','-q','origin','main')
        self.store = BoardStore(board/'data.json', validate)
        self.store.context = dict(process=str(self.repo/'PROCESS.md'),todos=str(board/'todos'),data=str(board/'data.json'),repository=str(self.repo))
        self.store.acquire();self.addCleanup(self.store.close);self.store.initialize()
        self.workflow = Workflow(self.store,self.workflow.url,self.options,self.processing)
        self.addCleanup(self.workflow.close)
        other=dict(self.store.snapshot()['data']['todos'][0]);other.pop('id');other['name']='Unrelated task';other['source_ideas']=[]
        self.store.mutate(dict(actor='SL',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=None,record=other)]))

    def advance_remote(self, name='guide.md', content='Relevant documentation'):
        path = self.root/('upstream-'+uuid.uuid4().hex)
        self.git('worktree','add','-b','other-'+uuid.uuid4().hex,str(path),'origin/main')
        (path/name).write_text(content)
        self.workflow.git('add',name,cwd=path);self.workflow.git('commit','-m','Concurrent upstream change',cwd=path)
        commit = self.workflow.git('rev-parse','HEAD',cwd=path)
        self.workflow.git('push','origin',commit+':refs/heads/main',cwd=path)
        return commit

    def done(self, todo):
        self.assertEqual(todo['workflow']['phase'], 'done', todo['workflow']['message'])
        run=todo['workflow']
        self.assertEqual(self.git('merge-base',run['published_commit'],'origin/main'),run['published_commit'])
        return run

    def test_clean_relaxed_publication_and_optional_checkout_sync(self):
        self.relaxed(repair=False); self.run_stage('implement')
        old=self.git('rev-parse','HEAD')
        run=self.done(self.run_stage('merge'))
        self.assertEqual(self.git('rev-parse','HEAD'),old)
        self.assertEqual(run['checkout_sync']['status'],'deferred')
        self.assertEqual(run['integration_tested_commit'],run['published_commit'])

    def test_clean_checkout_fast_forwards_when_repair_enabled(self):
        self.relaxed(); self.run_stage('implement')
        run=self.done(self.run_stage('merge'))
        self.assertEqual(self.git('rev-parse','HEAD'),run['published_commit'])
        self.assertEqual(run['checkout_sync']['status'],'synchronized')

    def test_context_gitlink_board_drift_keeps_pin_and_history(self):
        self.embedded(submodule=True); self.relaxed()
        old_pin=self.git('ls-tree','HEAD','context')
        self.run_stage('implement')
        history=self.workflow.git('rev-parse','HEAD',cwd=self.store.root)
        run=self.done(self.run_stage('merge'))
        self.assertEqual(self.git('ls-tree','HEAD','context'),old_pin)
        self.workflow.git('merge-base','--is-ancestor',history,'HEAD',cwd=self.store.root)
        self.assertEqual(run['checkout_sync']['status'],'deferred')
        self.assertTrue(any(e['path'].startswith('context/todos/') for e in run['checkout_inspection']))

    def test_staged_and_unstaged_unrelated_board_edits_survive(self):
        self.embedded(); self.relaxed(); self.run_stage('implement')
        other=dict(self.store.snapshot()['data']['todos'][1])
        file=self.store.root/'todos'/ (other['id']+'.json')
        other['name']='User staged edit';file.write_text(json.dumps(other))
        relative=str(file.relative_to(self.repo));self.git('add',relative)
        index=self.git('show',':'+relative)
        other['description']='User unstaged edit';file.write_text(json.dumps(other))
        before=file.read_bytes()
        run=self.done(self.run_stage('merge'))
        self.assertEqual(file.read_bytes(),before)
        self.assertEqual(self.git('show',':'+relative),index)
        self.assertEqual(run['checkout_sync']['status'],'deferred')

    def test_unknown_documentation_and_runtime_edits_block_without_mutation(self):
        self.relaxed();self.run_stage('implement')
        before=self.git('rev-parse','origin/main')
        (self.repo/'readme').write_text('Runtime instructions changed')
        self.git('add','readme');index=self.git('diff','--cached')
        todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'merge_failed')
        self.assertIn('relevant or unclassified',todo['workflow']['message'])
        self.assertEqual(self.git('diff','--cached'),index)
        self.assertEqual(self.git('rev-parse','origin/main'),before)

    def test_runtime_commit_behind_dirty_submodule_pin_is_relevant(self):
        self.embedded(submodule=True);self.relaxed();self.run_stage('implement')
        nested=self.store.root
        (nested/'runtime.py').write_text('changed = True')
        self.workflow.git('add','runtime.py',cwd=nested);self.workflow.git('commit','-m','Runtime change',cwd=nested)
        todo=self.run_stage('merge')
        self.assertIn('relevant or unclassified',todo['workflow']['message'])
        self.assertTrue(any(e['path']=='context/runtime.py' for e in todo['workflow']['checkout_inspection']))

    def test_committed_runtime_pin_and_semantic_documentation_are_retested(self):
        self.embedded(submodule=True);self.relaxed();self.run_stage('implement')
        nested=self.store.root
        (nested/'contract.md').write_text('Runtime behavior changes')
        self.workflow.git('add','contract.md',cwd=nested);self.workflow.git('commit','-m','Contract',cwd=nested)
        self.git('add','context');self.git('commit','-qm','Record runtime pin')
        import sys
        self.options['test']=[sys.executable,'-c',
            "from pathlib import Path; assert Path('context/contract.md').read_text() == 'Runtime behavior changes'"]
        run=self.done(self.run_stage('merge'))
        self.assertTrue(any(e['path']=='context/contract.md' and e['classification']=='relevant' for e in run['intervening_changes']))
        self.assertEqual(run['integration_tested_commit'],run['published_commit'])

    def test_remote_advance_during_tests_rebuilds_and_retests(self):
        self.relaxed();self.run_stage('implement')
        actual=self.workflow.command;calls=[]
        def command(argv,cwd,*args,**kwargs):
            result=actual(argv,cwd,*args,**kwargs)
            if '-integration-' in str(cwd):
                calls.append(str(cwd))
                if len(calls)==1:self.advance_remote()
            return result
        with patch.object(self.workflow,'command',side_effect=command):run=self.done(self.run_stage('merge'))
        self.assertEqual(len(calls),2)
        self.assertEqual(len(run['integration_retries']),1)
        self.assertEqual(self.git('show','origin/main:guide.md'),'Relevant documentation')

    def test_non_fast_forward_push_rebuilds_and_retests(self):
        self.relaxed();self.run_stage('implement')
        actual=self.workflow.git;pushes=[]
        def git(*args,**kwargs):
            if args[0]=='push' and args[-1].endswith(':refs/heads/main') and not kwargs.get('cwd'):
                pushes.append(args[-1])
                if len(pushes)==1:self.advance_remote()
            return actual(*args,**kwargs)
        with patch.object(self.workflow,'git',side_effect=git):run=self.done(self.run_stage('merge'))
        self.assertEqual(len(pushes),2)
        self.assertEqual(len([c for c in run['check_runs'] if c['stage']=='integration']),2)
        self.assertIn('push_retry',[e['stage'] for e in run['integration_repairs']])

    def test_bounded_remote_churn_and_manual_retry_when_repair_disabled(self):
        self.relaxed(attempts=2);self.run_stage('implement')
        actual=self.workflow.command;calls=[]
        def command(argv,cwd,*args,**kwargs):
            result=actual(argv,cwd,*args,**kwargs)
            if '-integration-' in str(cwd):
                calls.append(str(cwd));self.advance_remote(content='advance '+str(len(calls)))
            return result
        with patch.object(self.workflow,'command',side_effect=command):todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'merge_failed')
        self.assertEqual(len(calls),2)
        self.assertEqual(todo['workflow']['integration_retries'][-1]['outcome'],'blocked')
        self.done(self.run_stage('merge'))

    def test_acknowledgement_loss_confirms_publication_without_second_push(self):
        from integration import GitFailure
        self.relaxed();self.run_stage('implement');actual=self.workflow.git;pushes=[]
        def git(*args,**kwargs):
            result=actual(*args,**kwargs)
            if args[0]=='push' and args[-1].endswith(':refs/heads/main') and not kwargs.get('cwd'):
                pushes.append(args)
                raise GitFailure(args,subprocess.CompletedProcess(args,1,'','Lost push acknowledgement'))
            return result
        with patch.object(self.workflow,'git',side_effect=git):run=self.done(self.run_stage('merge'))
        self.assertEqual(len(pushes),1)
        self.assertEqual(run['integration_publication']['status'],'confirmed')

    def test_restart_after_push_uses_exact_retained_intent(self):
        self.relaxed();self.run_stage('implement')
        with patch('relaxed_integration.finish',side_effect=OSError('Crash after push')):todo=self.run_stage('merge')
        before=todo['workflow'];self.assertEqual(before['phase'],'merge_failed')
        self.workflow.close();self.workflow=Workflow(self.store,self.workflow.url,self.options,self.processing);self.addCleanup(self.workflow.close)
        with patch.object(self.workflow,'command',side_effect=AssertionError('Must not rerun tests after confirmed push')):
            run=self.done(self.run_stage('merge'))
        self.assertEqual(run['published_commit'],before['integration_publication']['commit'])
        self.assertEqual(run['integration_tested_commit'],before['integration_tested_commit'])

    def test_substantive_conflict_retains_candidate_and_shared_edits(self):
        self.relaxed();self.run_stage('implement')
        self.advance_remote(name='result',content='conflicting upstream implementation')
        before=self.git('rev-parse','HEAD')
        todo=self.run_stage('merge');run=todo['workflow']
        self.assertEqual(run['phase'],'merge_failed')
        self.assertIn('Substantive merge conflict',run['message'])
        self.assertEqual(self.git('rev-parse','HEAD'),before)
        self.assertEqual(run['conflicted_paths'],['result'])
        self.assertTrue(Path(run['integration_worktree']).exists())

    def test_metadata_arriving_during_tests_records_distinct_tested_and_final_commits(self):
        self.embedded();self.relaxed();self.options['integration']['reuse_board_metadata']=True;self.run_stage('implement')
        actual=self.workflow.command;calls=[]
        def command(argv,cwd,*args,**kwargs):
            result=actual(argv,cwd,*args,**kwargs)
            if '-integration-' in str(cwd):
                calls.append(str(cwd))
                snap=self.store.snapshot();other=snap['data']['todos'][1]
                self.store.mutate(dict(actor='SL',request_id=uuid.uuid4().hex,changes=[dict(
                    collection='todos',id=other['id'],revision=snap['revisions']['todos'][other['id']],record=dict(other,name='Unrelated board update'))]))
            return result
        with patch.object(self.workflow,'command',side_effect=command):run=self.done(self.run_stage('merge'))
        self.assertEqual(len(calls),1)
        self.assertNotEqual(run['integration_tested_commit'],run['published_commit'])
        self.assertEqual(run['integration_evidence']['final_commit'],run['published_commit'])
        self.assertTrue(run['integration_evidence']['metadata'])

    def test_strict_default_blocks_board_gitlink_drift(self):
        self.embedded(submodule=True);self.run_stage('implement')
        todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'merge_failed')
        self.assertIn('must be clean',todo['workflow']['message'])

    def test_automatic_repair_disabled_requires_explicit_retry(self):
        self.relaxed(repair=False);self.run_stage('implement')
        actual=self.workflow.command;calls=[]
        def command(argv,cwd,*args,**kwargs):
            result=actual(argv,cwd,*args,**kwargs)
            if '-integration-' in str(cwd):
                calls.append(str(cwd));self.advance_remote()
            return result
        with patch.object(self.workflow,'command',side_effect=command):todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'merge_failed')
        self.assertEqual(len(calls),1)
        self.assertEqual(todo['workflow']['integration_retries'][-1]['outcome'],'blocked')
        self.done(self.run_stage('merge'))

    def test_repeated_state_stops_before_attempt_budget(self):
        from integration import StaleCandidate
        self.relaxed(attempts=10);self.run_stage('implement')
        with patch.object(self.workflow,'integrate_and_deploy',side_effect=StaleCandidate('same state')) as call:
            todo=self.run_stage('merge')
        self.assertEqual(call.call_count,2)
        self.assertEqual(todo['workflow']['phase'],'merge_failed')
        self.assertEqual(todo['workflow']['integration_retries'][-1]['outcome'],'blocked')

    def test_task_scope_change_during_tests_blocks_publication(self):
        self.relaxed();self.run_stage('implement')
        before=self.git('rev-parse','origin/main');actual=self.workflow.command
        def command(argv,cwd,*args,**kwargs):
            result=actual(argv,cwd,*args,**kwargs)
            if '-integration-' in str(cwd):
                snap=self.store.snapshot();task=snap['data']['todos'][0]
                self.store.mutate(dict(actor='SL',request_id=uuid.uuid4().hex,changes=[dict(
                    collection='todos',id=task['id'],revision=snap['revisions']['todos'][task['id']],record=dict(task,description='Changed scope'))]))
            return result
        with patch.object(self.workflow,'command',side_effect=command):todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'merge_failed')
        self.assertIn('scope',todo['workflow']['message'])
        self.assertEqual(self.git('rev-parse','origin/main'),before)

    def test_committed_resolution_is_retested_after_explicit_retry(self):
        self.relaxed();self.run_stage('implement');self.advance_remote(name='result',content='conflict')
        failed=self.run_stage('merge')['workflow'];candidate=Path(failed['integration_worktree'])
        (candidate/'result').write_text('Reviewed resolution')
        self.workflow.git('add','result',cwd=candidate);self.workflow.git('commit','-m','Reviewed conflict resolution',cwd=candidate)
        run=self.done(self.run_stage('merge'))
        self.assertEqual(run['integration_worktree'],str(candidate))
        self.assertEqual(self.git('show','origin/main:result'),'Reviewed resolution')
        self.assertEqual(run['integration_tested_commit'],run['published_commit'])

    def test_intermediate_semantic_edit_reverted_in_pin_is_still_inspected(self):
        self.embedded(submodule=True);self.relaxed();run=self.run_stage('implement')['workflow']
        start=self.workflow.git('rev-parse','HEAD',cwd=self.store.root)
        doc=self.store.root/'contract.md';doc.write_text('Changed semantic guidance')
        self.workflow.git('add','contract.md',cwd=self.store.root);self.workflow.git('commit','-m','Contract change',cwd=self.store.root)
        self.workflow.git('revert','--no-edit','HEAD',cwd=self.store.root)
        end=self.workflow.git('rev-parse','HEAD',cwd=self.store.root)
        self.assertEqual(self.workflow.git('diff','--name-only',start,end,cwd=self.store.root),'')
        inspector=Changes(self.workflow,'T0001',run)
        self.assertFalse(inspector.commits(self.store.root,start,end))
        self.assertTrue(any(e['path']=='contract.md' for e in inspector.entries))
