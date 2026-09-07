"""Workflow tests use disposable code, boards, local bare remotes and fake agents."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from workflow import Workflow, settings, local_sources
from processing import settings as processing_settings
from versions import FORMAT_VERSION, migrate
from storage import BoardStore, Conflict
from server import validate
from test_server import fixture


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='unfertig-workflow-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root/'code'; self.repo.mkdir()
        self.git('init', '-b', 'main'); self.git('config','user.name','Test'); self.git('config','user.email','test@example.invalid')
        (self.repo/'readme').write_text('original')
        self.git('add','.'); self.git('commit','-qm','Baseline')
        self.remote = self.root/'remote.git'
        subprocess.run(['git','init','--bare','-q',str(self.remote)],check=True)
        self.git('remote','add','origin',str(self.remote)); self.git('push','-qu','origin','main')
        board=self.root/'board';board.mkdir()
        subprocess.run(['git','init','-q',str(board)],check=True)
        for key,value in [('user.name','Test'),('user.email','test@example.invalid')]:
            subprocess.run(['git','-C',str(board),'config',key,value],check=True)
        (board/'.gitignore').write_text('.server.lock\n.service.lock\n.operation.lock\n.receipts/\n.history-pending.json\n.transaction.json\n')
        (board/'data.json').write_text(json.dumps(fixture()))
        subprocess.run(['git','-C',str(board),'add','.'],check=True)
        subprocess.run(['git','-C',str(board),'commit','-qm','Board baseline'],check=True)
        self.store=BoardStore(board/'data.json',validate)
        self.store.context={'process':str(self.repo/'PROCESS.md')}
        self.store.acquire();self.addCleanup(self.store.close);self.store.initialize()
        executable=self.root/'codex'
        executable.write_text('#!'+sys.executable+'\nimport pathlib,subprocess,sys\nsys.stdin.read()\npathlib.Path(sys.argv[sys.argv.index("-o")+1]).write_text("UNFERTIG_IMPLEMENTATION_COMPLETE")\npathlib.Path("result").write_text("implemented")\nsubprocess.run(["git","add","result"],check=True)\nsubprocess.run(["git","commit","-qm","Implement test task"],check=True)\nprint("Implementation complete",flush=True)\n')
        executable.chmod(0o755)
        self.processing=processing_settings(dict(executable=str(executable)),self.root,self.repo,self.repo)
        self.options=settings(dict(enabled=True,repository=str(self.repo), test=[sys.executable,'-c','print("checks passed")'],
            preview=[sys.executable,'-m','http.server','{port}','--bind','127.0.0.1'],preview_url='http://127.0.0.1:{port}',
            restart=[sys.executable,'-c','print("restarted")']),self.root,self.processing,'embedded')
        self.workflow=Workflow(self.store,'http://127.0.0.1:1',self.options,self.processing)
        self.addCleanup(self.workflow.close)

    def git(self,*args):
        result=subprocess.run(['git','-C',str(self.repo),*args],capture_output=True,text=True,check=True)
        return result.stdout.strip()

    def run_stage(self,action, wait=True):
        snap=self.store.snapshot();todo=snap['data']['todos'][0]
        result=self.workflow.start(dict(id=todo['id'],action=action,revision=snap['revisions']['todos'][todo['id']],commit=todo.get('workflow',{}).get('commit')))
        if wait:self.workflow.worker.join(20);self.assertFalse(self.workflow.worker.is_alive())
        return self.store.snapshot()['data']['todos'][0]

    def test_implement_preview_merge_push_and_restart_receipt(self):
        todo=self.run_stage('implement');self.assertEqual(todo['workflow']['phase'],'ready',todo)
        self.assertEqual(todo['status'],'started');self.assertFalse((self.repo/'result').exists())
        todo=self.run_stage('test');self.assertEqual(todo['workflow']['phase'],'tested',todo)
        self.assertTrue(self.workflow.status()['runs']['T0001']['preview_url'])
        todo=self.run_stage('merge');self.assertEqual(todo['workflow']['phase'],'restarting',todo)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            self.workflow.reconcile();todo=self.store.snapshot()['data']['todos'][0]
            if todo['status']=='closed':break
            time.sleep(.1)
        self.assertEqual(todo['status'],'closed',todo)
        self.assertEqual(self.git('rev-parse','HEAD'),todo['workflow']['tested_commit'])
        self.assertEqual(self.git('ls-remote','origin','refs/heads/main').split()[0],todo['commit_hash'])
        self.assertEqual((self.repo/'result').read_text(),'implemented')

    def test_stale_revision_duplicate_claim_and_forgery_blocked(self):
        with self.assertRaises(Conflict): self.workflow.start(dict(id='T0001',action='implement',revision='stale'))
        self.run_stage('implement')
        with self.assertRaises(ValueError):self.run_stage('implement')
        snap=self.store.snapshot();todo=snap['data']['todos'][0]
        record=copy.deepcopy(todo);record['workflow']['phase']='done'
        with self.assertRaises(ValueError):self.store.mutate(dict(actor='Test',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=todo['id'],revision=snap['revisions']['todos'][todo['id']],record=record)]))

    def test_direct_merge_preserves_absent_test_evidence_after_reload(self):
        todo = self.run_stage('implement')
        self.assertNotIn('tested_commit', todo['workflow'])
        todo = self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'], 'restarting', todo)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.workflow.reconcile()
            todo = self.store.snapshot()['data']['todos'][0]
            if todo['status'] == 'closed':
                break
            time.sleep(.1)
        self.assertEqual(todo['status'], 'closed', todo)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], todo['workflow']['commit'])
        resumed = Workflow(self.store, self.workflow.url, self.options, self.processing)
        self.addCleanup(resumed.close)
        self.assertNotIn('tested_commit', resumed.status()['runs']['T0001'])

    def test_failed_optional_preview_can_merge_without_fabricating_success(self):
        self.run_stage('implement')
        self.workflow.options['test'] = [sys.executable, '-c', 'raise SystemExit(1)']
        todo = self.run_stage('test')
        self.assertEqual(todo['workflow']['phase'], 'test_failed')
        todo = self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'], 'restarting', todo)
        self.assertNotIn('tested_commit', todo['workflow'])

    def test_direct_merge_rejects_stale_commit_dirty_worktree_and_incomplete_run(self):
        todo = self.run_stage('implement')
        run = todo['workflow']
        snap = self.store.snapshot()
        with self.assertRaises(Conflict):
            self.workflow.start(dict(id=todo['id'], action='merge', revision=snap['revisions']['todos'][todo['id']], commit='0'*40))
        dirty = Path(run['worktree'])/'dirty'
        dirty.write_text('unsaved')
        with self.assertRaises(ValueError):
            self.run_stage('merge')
        dirty.unlink()
        self.workflow.save(todo['id'], dict(run, phase='implementation_failed'))
        with self.assertRaises(ValueError):
            self.run_stage('merge')
        self.workflow.save(todo['id'], run)
        subprocess.run(['git', '-C', run['worktree'], 'commit', '--allow-empty', '-qm', 'Changed branch'], check=True)
        with self.assertRaises(Conflict):
            self.run_stage('merge')

    def test_local_main_ahead_of_remote_is_included_in_tested_branch(self):
        (self.repo/'local-context').write_text('Existing local main commit')
        self.git('add','.');self.git('commit','-qm','Local context')
        self.run_stage('implement');self.run_stage('test')
        todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'restarting',todo)
        self.assertEqual(self.git('ls-remote','origin','refs/heads/main').split()[0],todo['workflow']['commit'])

    def test_changed_main_blocks_merge(self):
        self.run_stage('implement')
        (self.repo/'other').write_text('concurrent');self.git('add','.');self.git('commit','-qm','Other work')
        todo=self.run_stage('merge');self.assertEqual(todo['workflow']['phase'],'merge_failed')
        self.assertFalse((self.repo/'result').exists())

    def test_dirty_target_blocks_direct_merge(self):
        self.run_stage('implement')
        (self.repo/'unsaved').write_text('Preserve this work')
        todo = self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'], 'merge_failed')
        self.assertFalse((self.repo/'result').exists())
        self.assertNotIn('tested_commit', todo['workflow'])

    def test_interrupted_claim_survives_restart_without_automatic_retry(self):
        with patch.object(self.workflow,'run'):
            self.run_stage('implement')
        resumed=Workflow(self.store,self.workflow.url,dict(self.options,automatic=True),self.processing)
        self.assertEqual(resumed.status()['runs']['T0001']['phase'],'interrupted')
        with patch.object(resumed,'start') as start:resumed.tick();start.assert_not_called()

    def test_board_owned_context_can_merge_its_own_workflow_history(self):
        # Simulate Vesoma: the code target and board share their Git owner.
        self.workflow.close(); self.store.close()
        import shutil
        board = self.repo/'board'
        board.mkdir()
        (board/'data.json').write_text(json.dumps(fixture()))
        (self.repo/'.gitignore').write_text('.server.lock\n.service.lock\n.operation.lock\n.receipts/\n.history-pending.json\n.transaction.json\n')
        self.git('add','.');self.git('commit','-qm','Embedded board');self.git('push','-q','origin','main')
        self.store=BoardStore(board/'data.json',validate);self.store.context={'process':str(self.repo/'PROCESS.md')}
        self.store.acquire();self.addCleanup(self.store.close);self.store.initialize()
        self.workflow=Workflow(self.store,self.workflow.url,self.options,self.processing);self.addCleanup(self.workflow.close)
        self.run_stage('implement');self.run_stage('test');todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'restarting',todo)
        self.assertTrue((self.repo/'result').exists())
        self.assertIn('merge_commit',todo['workflow'])

    def test_feature_switch_defaults_migration_and_backend_gate(self):
        defaults=settings({},self.root,self.processing,'embedded')
        self.assertFalse(defaults['enabled']);self.assertFalse(defaults['automatic'])
        for value in ('true', 1, None):
            with self.assertRaises(ValueError):settings({'enabled':value},self.root,self.processing,'embedded')
        migrated=migrate({'format_version':'1.5.0','workflow':{'automatic':True,'extension':42}},'config')
        self.assertEqual(migrated['workflow'],{'enabled':False,'automatic':True,'extension':42})
        self.assertEqual(migrate(migrated,'config'),migrated)
        self.assertFalse(settings(migrated['workflow'],self.root,self.processing,'embedded')['automatic'])
        self.assertFalse(settings({'enabled':True},self.root,self.processing,'aggregation')['enabled'])
        self.options.update(enabled=False,automatic=True)
        before=self.store.snapshot()['data']
        for action in ('implement','retry','test','merge'):
            with self.assertRaisesRegex(ValueError,'disabled'):self.workflow.start({'action':action})
        with patch.object(self.workflow,'reconcile') as reconcile, patch.object(self.workflow,'start') as start:
            self.workflow.tick();reconcile.assert_not_called();start.assert_not_called()
        self.assertEqual(self.store.snapshot()['data'],before)
        self.assertFalse(self.workflow.status()['enabled'])

    def test_disabled_http_actions_are_rejected_with_valid_token(self):
        from server import Server
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        import threading
        server=Server(('127.0.0.1',0),self.store);server.workflow=self.workflow
        self.options['enabled']=False
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for action in ('implement','retry','test','merge'):
                request=Request(f'http://127.0.0.1:{server.server_port}/api/workflow/action',
                    data=json.dumps({'action':action}).encode(),method='PUT',
                    headers={'X-Board-Token':server.token,'Content-Type':'application/json'})
                with self.assertRaises(HTTPError) as error:urlopen(request)
                self.assertEqual(error.exception.code,403)
                self.assertIn('disabled',error.exception.read().decode())
        finally:server.shutdown();server.server_close();worker.join()

    def test_defaults_and_original_system_eligibility(self):
        self.assertFalse(settings({},self.root,self.processing,'embedded')['automatic'])
        self.assertFalse(settings(dict(automatic=True),self.root,self.processing,'aggregation')['automatic'])
        self.assertFalse(migrate({'format_version':'1.4.0'},'config')['workflow']['automatic'])
        snap=self.store.snapshot();todo=snap['data']['todos'][0]
        with patch('workflow.system_id',return_value='a'*64):
            self.assertFalse(local_sources(todo,snap))
            snap['data']['ideas'][0]['captured_system']='a'*64
            self.assertTrue(local_sources(todo,snap))
            todo['source_refs']=[dict(idea=dict(captured_system='b'*64))]
            self.assertFalse(local_sources(todo,snap))

    def test_nonzero_agent_preserves_claim_and_no_production_changes(self):
        self.processing['executable']='/usr/bin/false'
        todo=self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'],'implementation_failed')
        self.assertEqual(todo['status'],'started');self.assertFalse((self.repo/'result').exists())

    def test_explicit_retry_reuses_branch_and_rotates_failed_restart_receipt(self):
        executable=self.processing['executable'];self.processing['executable']='/usr/bin/false'
        failed=self.run_stage('implement');branch=failed['workflow']['branch']
        self.processing['executable']=executable
        ready=self.run_stage('retry');self.assertEqual(ready['workflow']['phase'],'ready',ready)
        self.assertEqual(ready['workflow']['branch'],branch)
        self.run_stage('test');self.options['restart']=['/usr/bin/false'];self.run_stage('merge')
        def await_phase(phase):
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                self.workflow.reconcile()
                todo=self.store.snapshot()['data']['todos'][0]
                if todo['workflow']['phase']==phase:return todo
                time.sleep(.1)
            self.fail(str(todo))
        await_phase('restart_failed')
        self.options['restart']=[sys.executable,'-c','print("restart recovered")']
        self.run_stage('merge')
        self.assertEqual(await_phase('done')['status'],'closed')

    def test_preview_config_rejects_nonlocal_url_and_command_strings(self):
        for options in [dict(preview_url='https://evil.example'),dict(test='rm -rf /')]:
            with self.assertRaises(ValueError):settings(options,self.root,self.processing,'embedded')


if __name__=='__main__':unittest.main()
