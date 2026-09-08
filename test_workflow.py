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
        self.store.context={'process':str(self.repo/'PROCESS.md'), 'todos':str(board/'todos'), 'data':str(board/'data.json'), 'repository':str(self.repo)}
        self.store.acquire();self.addCleanup(self.store.close);self.store.initialize()
        executable=self.root/'codex'
        executable.write_text('#!'+sys.executable+'\nimport pathlib,subprocess,sys\nsys.stdin.read()\npathlib.Path("result").write_text("implemented")\nsubprocess.run(["git","add","result"],check=True)\nsubprocess.run(["git","commit","-qm","Implement test task"],check=True)\nimport json\nhead=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()\npathlib.Path(sys.argv[sys.argv.index("-o")+1]).write_text(json.dumps(dict(status="complete",commit=head,summary="Done",tests=["fake agent"],limitations=[]))+"\\nUNFERTIG_IMPLEMENTATION_COMPLETE")\nprint("Implementation complete",flush=True)\n')
        executable.chmod(0o755)
        self.processing=processing_settings(dict(executable=str(executable)),self.root,self.repo,self.repo)
        self.options=settings(dict(enabled=True,repository=str(self.repo), test=[sys.executable,'-c','print("checks passed")'],
            preview=[sys.executable,'-m','http.server','{port}','--bind','127.0.0.1'],preview_url='http://127.0.0.1:{port}',
            restart=[sys.executable,'-c','print("restarted")']),self.root,self.processing,'embedded')
        self.prs = {}
        github_patch = patch.object(Workflow, 'github', lambda workflow, *args, **kwargs: self.fake_github(workflow, *args, **kwargs))
        github_patch.start(); self.addCleanup(github_patch.stop)
        self.workflow=Workflow(self.store,'http://127.0.0.1:1',self.options,self.processing)
        self.addCleanup(self.workflow.close)

    def fake_github(self, workflow, *args, **kwargs):
        if args[:2] == ('pr', 'list'):
            branch = args[args.index('--head')+1]
            return json.dumps([dict(url=v['url']) for v in self.prs.values() if v['headRefName'] == branch])
        if args[:2] == ('pr', 'create'):
            branch = args[args.index('--head')+1]
            url = 'https://github.com/test/code/pull/'+str(len(self.prs)+1)
            self.prs[url] = dict(url=url, state='OPEN', isDraft=True, headRefName=branch, baseRefName='main', mergeCommit=None)
            return url
        if args[:2] == ('pr', 'edit'):
            return ''
        if args[:2] == ('pr', 'ready'):
            self.prs[args[2]]['isDraft'] = False
            return ''
        if args[:2] == ('pr', 'view'):
            state = dict(self.prs[args[2]])
            state['headRefOid'] = self.git('rev-parse', state['headRefName'])
            return json.dumps(state)
        raise AssertionError(args)

    def git(self,*args):
        result=subprocess.run(['git','-C',str(self.repo),*args],capture_output=True,text=True,check=True)
        return result.stdout.strip()

    def run_stage(self,action, wait=True):
        snap=self.store.snapshot();todo=snap['data']['todos'][0]
        result=self.workflow.start(dict(id=todo['id'],action=action,revision=snap['revisions']['todos'][todo['id']],commit=todo.get('workflow',{}).get('commit')))
        if wait:self.workflow.workers[todo['id']].join(20);self.assertFalse(self.workflow.workers[todo['id']].is_alive())
        return self.store.snapshot()['data']['todos'][0]

    def test_launch_passes_latest_effort_as_actual_config_and_freezes_running_job(self):
        from efforts import EFFORTS, launch_arguments
        from storage import digest
        for value in EFFORTS:
            self.assertEqual(launch_arguments({'effort': value}), ['-c', 'model_reasoning_effort="'+value+'"'])
        with self.assertRaisesRegex(ValueError, 'Unsupported effort'):
            launch_arguments({'effort': 'unsupported'})
        real_pr = self.workflow.ensure_pr
        def edit_effort(value):
            todo = self.store.snapshot()['data']['todos'][0]
            self.store.mutate(dict(actor='SL', request_id=uuid.uuid4().hex, changes=[dict(
                collection='todos', id=todo['id'], revision=digest(todo), record=dict(todo, effort=value))]))
        def prepare(todo, run):
            real_pr(todo, run)
            edit_effort('xhigh')  # Saved after scheduling, before actual execution.
        actual = self.workflow.command
        captured = []
        def command(argv, cwd, ident, prompt=None, **kwargs):
            if 'exec' in argv:
                captured.append((list(argv), prompt))
                edit_effort('low')  # A running job keeps its captured setting.
            return actual(argv, cwd, ident, prompt, **kwargs)
        with patch.object(self.workflow, 'ensure_pr', side_effect=prepare), patch.object(self.workflow, 'command', side_effect=command):
            todo = self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'], 'ready', todo)
        self.assertEqual(todo['effort'], 'low')
        self.assertEqual(len(captured), 1)
        argv, prompt = captured[0]
        self.assertEqual(argv[argv.index('-c')+1], 'model_reasoning_effort="xhigh"')
        self.assertIn('Agent effort: xhigh', prompt)

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
        self.assertIn('Done', todo['completion_summary'])
        self.assertIn('fake agent', todo['completion_summary'])
        self.assertIn('Deployment verification:', todo['completion_summary'])
        self.assertEqual(self.git('rev-parse','HEAD'),todo['workflow']['tested_commit'])
        self.assertEqual(self.git('ls-remote','origin','refs/heads/main').split()[0],todo['commit_hash'])
        self.assertEqual((self.repo/'result').read_text(),'implemented')

    def test_no_change_report_retains_findings_without_empty_implementation_commit(self):
        executable = Path(self.processing['executable'])
        executable.write_text(executable.read_text().replace('pathlib.Path("result").write_text("implemented")', 'pass').replace('subprocess.run(["git","add","result"],check=True)', 'pass').replace('subprocess.run(["git","commit","-qm","Implement test task"],check=True)', 'pass'))
        todo = self.run_stage('implement')
        run = todo['workflow']
        self.assertEqual(run['phase'], 'implementation_failed')
        self.assertIn('No implementation changes', run['message'])
        self.assertIn('Done', run['completion_summary'])
        self.assertEqual(self.git('rev-parse', run['branch']), run['kickoff_commit'])
        self.assertEqual(todo['commit_hash'], '')
        self.assertNotEqual(todo['status'], 'closed')

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
        self.assertEqual(todo['workflow']['phase'], 'resolution_blocked', todo)
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

    def test_changed_main_is_combined_and_retested(self):
        self.run_stage('implement')
        (self.repo/'other').write_text('concurrent');self.git('add','.');self.git('commit','-qm','Other work')
        todo=self.run_stage('merge');self.assertEqual(todo['workflow']['phase'],'restarting',todo)
        self.assertTrue((self.repo/'result').exists())
        self.assertEqual(todo['workflow']['integration_commit'], todo['workflow']['integration_tested_commit'])

    def test_dirty_target_blocks_direct_merge(self):
        self.run_stage('implement')
        (self.repo/'unsaved').write_text('Preserve this work')
        todo = self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'], 'merge_failed')
        self.assertFalse((self.repo/'result').exists())
        self.assertIn(str(self.repo), todo['workflow']['message'])
        self.assertIn('Implementation is committed', todo['workflow']['message'])
        self.assertIn('retry Merge & restart', todo['workflow']['message'])
        self.assertNotIn('tested_commit', todo['workflow'])

    def test_interrupted_claim_survives_restart_without_automatic_retry(self):
        with patch.object(self.workflow,'run'):
            self.run_stage('implement')
        resumed=Workflow(self.store,self.workflow.url,dict(self.options,automatic=True),self.processing)
        self.assertEqual(resumed.status()['runs']['T0001']['phase'],'interrupted')
        with patch.object(resumed,'start') as start:resumed.tick();start.assert_not_called()

    def check_live_implementation(self, outcome):
        # Hold a real disposable agent process so polling has observable activity.
        executable = self.root/'codex'
        entered, release = self.root/'entered', self.root/'release'
        pause = (f'import time\npathlib.Path({str(entered)!r}).touch()\n'
                 f'while not pathlib.Path({str(release)!r}).exists(): time.sleep(.02)\n')
        if outcome == 'failure':
            pause += 'sys.exit(1)\n'
        executable.write_text(executable.read_text().replace('sys.stdin.read()\n', 'sys.stdin.read()\n'+pause))
        self.run_stage('implement', wait=False)
        deadline = time.monotonic()+10
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(entered.exists(), 'Disposable worker did not start')
        for _ in range(2):
            status = self.workflow.status()
            self.assertTrue(status['busy'])
            self.assertEqual(status['runs']['T0001']['phase'], 'implementing')
            self.assertNotIn('foreign', status['runs']['T0001'])
        if outcome == 'interruption':
            self.workflow.close()
        else:
            release.touch()
        self.workflow.workers['T0001'].join(10)
        status = self.workflow.status()
        self.assertFalse(status['busy'])
        self.assertNotEqual(status['runs']['T0001']['phase'], 'implementing')
        self.assertEqual(status['runs']['T0001']['phase'], 'ready' if outcome == 'completion' else 'implementation_failed')

    def test_live_implementation_completion(self):
        self.check_live_implementation('completion')

    def test_live_implementation_failure(self):
        self.check_live_implementation('failure')

    def test_live_implementation_interruption(self):
        self.check_live_implementation('interruption')

    def test_board_owned_context_can_merge_its_own_workflow_history(self):
        # Simulate Vesoma: the code target and board share their Git owner.
        self.workflow.close(); self.store.close()
        import shutil
        board = self.repo/'board'
        board.mkdir()
        (board/'data.json').write_text(json.dumps(fixture()))
        (self.repo/'.gitignore').write_text('.server.lock\n.service.lock\n.operation.lock\n.receipts/\n.history-pending.json\n.transaction.json\n')
        self.git('add','.');self.git('commit','-qm','Embedded board');self.git('push','-q','origin','main')
        self.store=BoardStore(board/'data.json',validate);self.store.context={'process':str(self.repo/'PROCESS.md'), 'todos':str(board/'todos'), 'data':str(board/'data.json'), 'repository':str(self.repo)}
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
        self.assertEqual(migrated['workflow'],{'enabled':False,'automatic':True,'extension':42,'max_workers':2,'automatic_merge':False,'automatic_publish':False,'automatic_deploy':False})
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

    def add_ticket(self, **fields):
        template = copy.deepcopy(self.store.snapshot()['data']['todos'][0])
        for key in ('workflow', 'id'):
            template.pop(key, None)
        template.update(name='Another ticket', source_ideas=[], status='open', closed_by='', date_closed='', **fields)
        result = self.store.mutate(dict(actor='Test', request_id=uuid.uuid4().hex,
            changes=[dict(collection='todos', id=None, record=template)]))
        return result['data']['todos'][-1]['id']

    def start_ticket(self, ident, action='implement', **extra):
        snap = self.store.snapshot()
        todo = next(t for t in snap['data']['todos'] if t['id'] == ident)
        body = dict(id=ident, action=action, revision=snap['revisions']['todos'][ident],
                    commit=todo.get('workflow', {}).get('commit'), request_id=uuid.uuid4().hex, **extra)
        return body, self.workflow.start(body)

    def await_workers(self):
        for worker in list(self.workflow.workers.values()):
            worker.join(15)
            self.assertFalse(worker.is_alive())

    def test_two_workers_capacity_queue_and_idempotent_restart(self):
        import threading
        gate = threading.Event()
        self.addCleanup(gate.set)
        second, third = self.add_ticket(), self.add_ticket()
        with patch.object(self.workflow, 'run', side_effect=lambda *args: gate.wait(10)):
            body, _ = self.start_ticket('T0001')
            self.start_ticket(second)
            self.start_ticket(third)
            self.assertEqual(self.workflow.status()['active_count'], 2)
            self.assertEqual(self.workflow.status()['runs'][third]['phase'], 'queued')
            self.workflow.start(body)
            self.assertEqual(len(self.workflow.workers), 2)
            with self.assertRaises(Conflict):
                self.workflow.start(dict(body, action='test'))
            gate.set(); self.await_workers()
        resumed = Workflow(self.store, self.workflow.url, self.options, self.processing)
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.status()['runs']['T0001']['phase'], 'interrupted')
        resumed.dispatch()
        resumed.workers[third].join(15)
        self.assertEqual(resumed.status()['runs'][third]['phase'], 'ready')
        self.assertEqual(resumed.status()['runs']['T0001']['phase'], 'interrupted')

    def test_pr_failure_prevents_worker_and_retry_recovers_one_pr(self):
        executable = Path(self.processing['executable'])
        marker = self.root/'agent-entered'
        executable.write_text(executable.read_text().replace('sys.stdin.read()', f'pathlib.Path({str(marker)!r}).touch();sys.stdin.read()'))
        github = self.workflow.github
        def fail_create(*args, **kwargs):
            if args[:2] == ('pr', 'create'):
                # Simulate an accepted request whose response was lost.
                github(*args, **kwargs)
                raise ValueError('Lost GitHub response')
            return github(*args, **kwargs)
        with patch.object(self.workflow, 'github', side_effect=fail_create):
            todo = self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'], 'implementation_failed')
        self.assertFalse(marker.exists())
        self.assertEqual(len(self.prs), 1)
        todo = self.run_stage('retry')
        self.assertEqual(todo['workflow']['phase'], 'ready', todo)
        self.assertTrue(marker.exists())
        self.assertEqual(len(self.prs), 1)
        remote = self.git('ls-remote', 'origin', 'refs/heads/'+todo['workflow']['branch']).split()[0]
        self.assertEqual(remote, todo['workflow']['commit'])

    def test_merge_queue_drains_other_worker(self):
        import threading
        self.run_stage('implement')
        second = self.add_ticket()
        gate = threading.Event(); self.addCleanup(gate.set)
        with patch.object(self.workflow, 'run', side_effect=lambda *args: gate.wait(10)):
            self.start_ticket(second)
            self.start_ticket('T0001', 'merge')
            state = self.workflow.status()
            self.assertTrue(state['draining'])
            self.assertEqual(state['runs']['T0001']['phase'], 'merge_queued')
            self.assertEqual(state['active_count'], 1)
            gate.set(); self.await_workers()
        self.workflow.dispatch(); self.await_workers()
        self.assertEqual(self.workflow.status()['runs']['T0001']['phase'], 'restarting')

    def test_github_squash_merge_is_not_applied_twice(self):
        todo = self.run_stage('implement'); run = todo['workflow']
        self.git('merge', '--squash', run['commit']); self.git('commit', '-qm', 'Squashed on GitHub')
        merged = self.git('rev-parse', 'HEAD'); self.git('push', 'origin', 'main')
        # Follow-up main change would conflict if the original branch were reapplied.
        (self.repo/'result').write_text('follow-up on main')
        self.git('add', 'result'); self.git('commit', '-qm', 'Follow-up'); self.git('push', 'origin', 'main')
        self.prs[run['pr_url']].update(state='MERGED', mergeCommit=dict(oid=merged))
        todo = self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'], 'restarting', todo)
        self.assertEqual((self.repo/'result').read_text(), 'follow-up on main')
        self.assertEqual(todo['workflow']['github_merge_commit'], merged)

    def test_closed_pr_blocks_integration_without_mutation(self):
        todo = self.run_stage('implement')
        head = self.git('rev-parse', 'HEAD')
        self.prs[todo['pr_url']]['state'] = 'CLOSED'
        todo = self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'], 'merge_failed')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_integration_checks_combined_code_and_preserves_failure(self):
        self.run_stage('implement')
        (self.repo/'other').write_text('main advanced');self.git('add','other');self.git('commit','-qm','Other change')
        head = self.git('rev-parse', 'HEAD')
        self.options['test'] = [sys.executable, '-c', 'from pathlib import Path; assert not (Path("other").exists() and Path("result").exists())']
        todo = self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'], 'resolution_blocked')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertTrue(Path(todo['workflow']['integration_worktree']).is_dir())

    def test_dependency_waits_and_unknown_cycle_rejected(self):
        second = self.add_ticket(depends_on=['T0001'])
        self.start_ticket(second)
        self.assertEqual(self.workflow.status()['runs'][second]['phase'], 'queued')
        self.assertIn('T0001', self.workflow.status()['runs'][second]['message'])
        snap = self.store.snapshot(); first = snap['data']['todos'][0]
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.store.mutate(dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(collection='todos', id=first['id'], revision=snap['revisions']['todos'][first['id']], record=dict(first, depends_on=[second]))]))
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            self.add_ticket(depends_on=['T9999'])
        self.store.mutate(dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(collection='todos', id=first['id'], revision=snap['revisions']['todos'][first['id']], record=dict(first, completion_summary='Verified prerequisite on origin/main; no further work required.', status='closed', commit_hash=self.git('rev-parse','origin/main'), closed_by='Test', date_closed=datetime.now(timezone.utc).isoformat()))]))
        self.workflow.dispatch(); self.await_workers()
        self.assertEqual(self.workflow.status()['runs'][second]['phase'], 'ready')

    def test_scope_change_while_queued_blocks_launch(self):
        second = self.add_ticket(depends_on=['T0001'])
        self.start_ticket(second)
        snap = self.store.snapshot(); todo = snap['data']['todos'][-1]
        self.store.mutate(dict(actor='Test', request_id=uuid.uuid4().hex, changes=[dict(collection='todos',id=second,revision=snap['revisions']['todos'][second],record=dict(todo,depends_on=[],description='New scope'))]))
        self.workflow.dispatch()
        self.assertEqual(self.workflow.status()['runs'][second]['phase'], 'implementation_failed')
        self.assertNotIn(second, self.workflow.workers)

    def test_parallel_migration_preserves_claims_extensions_and_defaults(self):
        config = dict(format_version='1.7.0', workflow=dict(enabled=True, automatic=True, max_workers=3, extension=42))
        migrated = migrate(config, 'config')
        self.assertEqual(migrated['workflow']['max_workers'], 3)
        self.assertFalse(migrated['workflow']['automatic_merge'])
        self.assertFalse(migrated['workflow']['automatic_publish'])
        self.assertFalse(migrated['workflow']['automatic_deploy'])
        self.assertEqual(migrated['workflow']['extension'], 42)
        self.assertEqual(migrate(migrated, 'config'), migrated)
        todo = self.run_stage('implement')
        legacy = dict(todo, format_version='1.7.0', extension={'preserve': True})
        result = migrate(legacy, 'todo')
        self.assertEqual(result['workflow'], legacy['workflow'])
        self.assertEqual(result['extension'], legacy['extension'])
        self.assertNotIn('depends_on', result)


    def test_checkpoint_is_pushed_while_agent_still_runs(self):
        executable=Path(self.processing['executable'])
        release=self.root/'release'; entered=self.root/'checkpoint'
        code=executable.read_text()
        code += f'\nimport time\npathlib.Path({str(entered)!r}).touch()\nwhile not pathlib.Path({str(release)!r}).exists(): time.sleep(.02)\n'
        executable.write_text(code)
        self.start_ticket('T0001')
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                run=self.workflow.status()['runs']['T0001']
                if entered.exists():
                    head=self.git('rev-parse',run['branch'])
                    remote=self.git('ls-remote','origin','refs/heads/'+run['branch']).split()[0]
                    if head==remote:break
                time.sleep(.1)
            else:self.fail('Checkpoint was not published while the worker ran')
            self.assertTrue(self.workflow.workers['T0001'].is_alive())
        finally:
            release.touch();self.await_workers()
        self.assertEqual(self.workflow.status()['runs']['T0001']['phase'],'ready')

    def test_external_main_advance_during_checks_invalidates_candidate(self):
        self.run_stage('implement')
        original = self.workflow.command
        baseline = self.git('rev-parse','HEAD')
        def changed(argv,cwd,ident,*args,**kwargs):
            result=original(argv,cwd,ident,*args,**kwargs)
            if '-integration-' in str(cwd):
                self.git('commit','--allow-empty','-qm','Concurrent main update')
            return result
        with patch.object(self.workflow,'command',side_effect=changed):
            todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'merge_failed')
        self.assertFalse((self.repo/'result').exists())
        self.assertNotEqual(self.git('rev-parse','HEAD'),baseline)
        self.assertIn('advanced',todo['workflow']['message'])

    def test_automatic_delivery_requires_all_three_grants(self):
        self.run_stage('implement')
        self.options.update(automatic_merge=True,automatic_publish=True,automatic_deploy=False)
        with patch.object(self.workflow,'start') as start:
            self.workflow.tick();start.assert_not_called()
        self.options['automatic_deploy']=True
        with patch.object(self.workflow,'start') as start:
            self.workflow.tick();self.assertEqual(start.call_args.args[0]['action'],'merge')


    def test_live_retained_process_blocks_duplicate_after_service_restart(self):
        from storage import atomic, encode
        todo=self.run_stage('implement');run=todo['workflow']
        receipt=self.workflow.process_receipt(run)
        atomic(receipt,encode(dict(format_version=FORMAT_VERSION,run_id=run['run_id'],state='running',pid=os.getpid(),identity=self.workflow.process_identity(os.getpid()))))
        resumed=Workflow(self.store,self.workflow.url,self.options,self.processing)
        self.addCleanup(resumed.close)
        snapshot=self.store.snapshot()
        with self.assertRaisesRegex(Conflict,'still running'):
            resumed.start(dict(id=todo['id'],action='test',revision=snapshot['revisions']['todos'][todo['id']],commit=run['commit']))
        atomic(receipt,encode(dict(format_version=FORMAT_VERSION,run_id=run['run_id'],state='launching')))
        with self.assertRaisesRegex(Conflict,'unknown'):
            resumed.guard_process(run)
        atomic(receipt,encode(dict(format_version=FORMAT_VERSION,run_id=run['run_id'],state='exited')))
        resumed.guard_process(run)



    def queue_ready_tickets(self):
        self.run_stage('implement')
        ids = ['T0001', self.add_ticket(), self.add_ticket()]
        for ident in ids[1:]:
            self.start_ticket(ident); self.await_workers()
        requests = []
        with patch.object(self.workflow, 'dispatch'):
            for ident in ids:
                requests.append(self.start_ticket(ident, 'merge')[0])
        return ids, requests

    def await_receipt(self, ident):
        run = self.workflow.status()['runs'][ident]
        path = Path(run['worktree']).parent/(run['run_id']+'-deployment.json')
        deadline = time.monotonic()+10
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(path.exists())
        return path

    def restart_coordinator(self):
        self.workflow.close()
        self.workflow = Workflow(self.store, 'http://127.0.0.1:1', self.options, self.processing)
        self.addCleanup(self.workflow.close)

    def test_failed_delivery_pauses_queue_across_restart_and_recovery(self):
        ids, requests = self.queue_ready_tickets()
        before = copy.deepcopy(self.workflow.status()['runs'])
        self.options['restart'] = ['/usr/bin/false']
        self.workflow.dispatch(); self.await_workers(); self.await_receipt(ids[0])
        self.restart_coordinator()
        self.workflow.tick()
        state = self.workflow.status()
        self.assertEqual(state['queue_blocked_by'], ids[0])
        self.assertEqual(state['runs'][ids[0]]['phase'], 'restart_failed')
        for ident, request in zip(ids[1:], requests[1:]):
            self.assertEqual(state['runs'][ident]['phase'], 'merge_queued')
            self.assertEqual(state['runs'][ident]['waiting_for'], ids[0])
            self.assertEqual(state['runs'][ident]['queued_at'], before[ident]['queued_at'])
            self.workflow.start(request)  # Original request remains idempotent.
        self.options['restart'] = [sys.executable, '-c', 'print("recovered")']
        self.start_ticket(ids[0], 'recover'); self.await_workers(); self.await_receipt(ids[0])
        self.workflow.reconcile()
        for ident in ids[1:]:
            self.workflow.dispatch(); self.await_workers(); self.await_receipt(ident)
            self.workflow.reconcile()
            self.assertEqual(self.workflow.status()['runs'][ident]['phase'], 'done')

    def test_successful_supervisor_receipt_retains_subsequent_queue_after_restart(self):
        ids, _ = self.queue_ready_tickets()
        self.workflow.dispatch(); self.await_workers(); self.await_receipt(ids[0])
        self.restart_coordinator()
        self.workflow.tick(); self.await_workers()
        self.assertEqual(self.workflow.status()['runs'][ids[0]]['phase'], 'done')
        self.assertEqual(self.workflow.status()['runs'][ids[1]]['phase'], 'restarting')
        self.assertEqual(self.workflow.status()['runs'][ids[2]]['phase'], 'merge_queued')
        self.await_receipt(ids[1]); self.workflow.reconcile()

    def test_explicit_skip_preserves_failure_and_request_identity(self):
        ids, _ = self.queue_ready_tickets()
        run = self.workflow.status()['runs'][ids[0]]
        self.workflow.save(ids[0], dict(run, phase='merge_failed', message='Retained failure'))
        with patch.object(self.workflow, 'dispatch'):
            request, _ = self.start_ticket(ids[0], 'skip')
            self.workflow.start(request)
        current = self.workflow.status()['runs'][ids[0]]
        self.assertEqual(current['phase'], 'merge_failed')
        self.assertEqual(current['message'], 'Retained failure')
        self.assertIn('queue_skip', current)
        self.assertIsNone(self.workflow.status()['queue_blocked_by'])
        with self.assertRaises(Conflict):
            self.workflow.start(dict(request, action='merge'))

    def conflict_fixture(self):
        agent = Path(self.processing['executable'])
        agent.write_text(agent.read_text().replace('sys.stdin.read()', 'sys.stdin.read();pathlib.Path("readme").write_text("ticket")').replace('["git","add","result"]', '["git","add","result","readme"]'))
        todo = self.run_stage('implement')
        (self.repo/'readme').write_text('main')
        self.git('add', 'readme'); self.git('commit', '-qm', 'Advanced main')
        self.git('push', 'origin', 'main')
        agent.write_text('#!'+sys.executable+'''\nimport pathlib,subprocess,sys,json
prompt=sys.stdin.read()
assert 'All merge conflicts' in prompt
assert 'Do not push' in prompt
pathlib.Path('readme').write_text('main + ticket')
subprocess.run(['git','add','readme'],check=True)
subprocess.run(['git','commit','-qm','Preserve both intended changes'],check=True)
head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(dict(status='complete',commit=head,summary='Both changes preserved',tests=['combined'],attempts=['Inspect both parents and combine'],blocker='')))
''')
        self.options['test'] = [sys.executable, '-c', 'from pathlib import Path; assert Path("readme").read_text() == "main + ticket"']
        return todo['workflow']

    def test_agent_resolves_conflict_and_continues_existing_authorization(self):
        original = self.conflict_fixture()
        todo = self.run_stage('merge'); run = todo['workflow']
        self.assertEqual(run['phase'], 'restarting', run)
        self.assertEqual((self.repo/'readme').read_text(), 'main + ticket')
        self.assertEqual(self.git('rev-parse', original['branch']), original['commit'])
        self.assertEqual(run['conflicted_paths'], ['readme'])
        self.assertIn('CONFLICT', run['git_diagnostics']['stdout'])
        self.assertEqual(run['integration_tested_commit'], run['published_commit'])
        self.assertTrue(Path(run['resolution_reports'][0]).is_file())
        self.await_receipt(todo['id'])

    def test_restart_during_resolution_reuses_candidate_and_waiting_entries(self):
        self.conflict_fixture()
        resolve = self.workflow.resolve_candidate
        def interrupt(ident, run, candidate, cause):
            run.update(phase='resolving_conflict', message='Agent resolving merge conflict', conflicted_paths=['readme'])
            self.workflow.save(ident, run)
            raise SystemExit('Simulated coordinator exit')
        with patch.object(self.workflow, 'resolve_candidate', side_effect=interrupt):
            self.run_stage('merge')
        before = self.workflow.status()['runs']['T0001']
        self.restart_coordinator()
        self.workflow.dispatch(); self.await_workers()
        after = self.workflow.status()['runs']['T0001']
        self.assertEqual(after['phase'], 'restarting', after)
        self.assertEqual(after['integration_worktree'], before['integration_worktree'])
        self.await_receipt('T0001')

    def test_git_diagnostics_preserve_both_streams_and_command(self):
        from integration import GitFailure
        result = subprocess.CompletedProcess([], 1, 'CONFLICT in app.js', 'Recorded preimage for app.js')
        with patch('workflow.subprocess.run', return_value=result):
            with self.assertRaises(GitFailure) as caught:
                self.workflow.git('merge', 'candidate')
        self.assertIn('CONFLICT', str(caught.exception))
        self.assertIn('Recorded preimage', str(caught.exception))
        self.assertEqual(caught.exception.evidence['command'], ['merge','candidate'])

    def test_genuine_resolution_blocker_retains_attempts_and_queue(self):
        self.conflict_fixture()
        agent = Path(self.processing['executable'])
        agent.write_text('#!'+sys.executable+'''\nimport pathlib,sys,json
sys.stdin.read()
pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(dict(status='needs_attention',attempts=['Inspected both contracts', 'Searched saved requirements'],blocker='Required external interface specification is unavailable')))
''')
        todo = self.run_stage('merge'); run = todo['workflow']
        self.assertEqual(run['phase'], 'resolution_blocked')
        self.assertIn('Inspected both contracts', run['message'])
        self.assertIn('specification', run['message'])
        self.assertEqual(self.workflow.status()['queue_blocked_by'], todo['id'])
        self.assertTrue(Path(run['integration_worktree']).exists())
        self.assertEqual((self.repo/'readme').read_text(), 'main')

    def test_unchanged_failing_candidate_stops_for_lack_of_progress(self):
        self.run_stage('implement')
        agent = Path(self.processing['executable'])
        agent.write_text('#!'+sys.executable+'''\nimport pathlib,subprocess,sys,json
sys.stdin.read()
head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(dict(status='complete',commit=head,summary='No changed candidate',tests=[],attempts=['Inspected failure'],blocker='')))
''')
        self.options['test'] = [sys.executable, '-c', 'raise SystemExit(1)']
        run = self.run_stage('merge')['workflow']
        self.assertEqual(run['phase'], 'resolution_blocked')
        self.assertIn('without progress', run['message'])
        self.assertEqual(len(run['resolution_reports']), 1)
        self.assertEqual(len(run['resolution_failures']), 1)
        retried = self.run_stage('merge')['workflow']
        self.assertEqual(retried['phase'], 'resolution_blocked')
        self.assertEqual(len(retried['resolution_reports']), 2)
        self.assertNotEqual(run['resolution_epoch'], retried['resolution_epoch'])

    def test_incomplete_or_newer_receipts_keep_delivery_pending(self):
        self.run_stage('implement'); todo = self.run_stage('merge')
        path = self.await_receipt(todo['id'])
        valid = json.loads(path.read_text())
        for receipt, reason in [('interrupted JSON', 'pending'),
                                (json.dumps(dict(valid, format_version='1.14.0')), 'too old'),
                                (json.dumps(dict(valid, commit='0'*40)), 'different candidate'),
                                (json.dumps(dict(valid, ok='true')), 'incomplete')]:
            path.write_text(receipt)
            self.workflow.reconcile()
            run = self.workflow.status()['runs'][todo['id']]
            self.assertEqual(run['phase'], 'restarting')
            self.assertIn(reason, run['message'])
        path.write_text(json.dumps(valid)); self.workflow.reconcile()
        self.assertEqual(self.workflow.status()['runs'][todo['id']]['phase'], 'done')

    def test_agent_reconciles_competing_migration_successors(self):
        baseline = "def initial(v): return v\nMIGRATIONS = {'0.0.0': initial}\n"
        (self.repo/'versions.py').write_text(baseline)
        self.git('add', 'versions.py'); self.git('commit', '-qm', 'Initial registry'); self.git('push','origin','main')
        agent = Path(self.processing['executable'])
        proposed = "def initial(v): return v\ndef effort(v): return dict(v, effort='medium')\nMIGRATIONS = {'0.0.0': initial, '1.0.0': effort}\n"
        agent.write_text(agent.read_text().replace('sys.stdin.read()', 'sys.stdin.read();pathlib.Path("versions.py").write_text('+repr(proposed)+')').replace('["git","add","result"]', '["git","add","result","versions.py"]'))
        original = self.run_stage('implement')['workflow']
        main = "def initial(v): return v\ndef discovery(v): return dict(v, search_paths=[])\nMIGRATIONS = {'0.0.0': initial, '1.0.0': discovery}\n"
        (self.repo/'versions.py').write_text(main)
        self.git('add','versions.py'); self.git('commit','-qm','Published discovery migration'); self.git('push','origin','main')
        combined = "def initial(v): return v\ndef discovery(v): return dict(v, search_paths=[])\ndef effort(v): return dict(v, effort='medium')\nMIGRATIONS = {'0.0.0': initial, '1.0.0': discovery, '1.1.0': effort}\n"
        agent.write_text('#!'+sys.executable+'\nimport pathlib,subprocess,sys,json\nprompt=sys.stdin.read()\nassert "sequential registry" in prompt\npathlib.Path("versions.py").write_text('+repr(combined)+')\nsubprocess.run(["git","add","versions.py"],check=True)\nsubprocess.run(["git","commit","-qm","Serialize both migrations"],check=True)\nhead=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()\npathlib.Path(sys.argv[sys.argv.index("-o")+1]).write_text(json.dumps(dict(status="complete",commit=head,summary="Both migrations retained",tests=["supported upgrades"],attempts=["Preserved published assignment; appended effort"],blocker="")))\n')
        self.options['test'] = [sys.executable, '-c', "import versions; v={'original':'retained','extension':[42]};\nfor f in versions.MIGRATIONS.values(): v=f(v)\nassert v == {'original':'retained','extension':[42],'search_paths':[],'effort':'medium'}"]
        todo = self.run_stage('merge'); run = todo['workflow']
        self.assertEqual(run['phase'], 'restarting', run)
        self.assertEqual((self.repo/'versions.py').read_text(), combined)
        self.assertEqual(self.git('rev-parse', original['branch']), original['commit'])
        self.await_receipt(todo['id'])

    def migration_review(self):
        self.run_stage('implement')
        def review(candidate):
            return dict(schema_version=1,review_id='a'*32,state='migration_required',
                        candidate_commit=self.workflow.git('rev-parse','HEAD',cwd=candidate),
                        current_formats=['1.8.0'],target_format='1.9.0',message='Reviewed storage upgrade')
        with patch.object(self.workflow,'managed_unfertig',return_value=True),patch.object(self.workflow,'deployment_review',side_effect=review):
            return self.run_stage('merge')

    def test_migration_pauses_before_main_publication_and_requires_exact_review(self):
        head=self.git('rev-parse','HEAD')
        todo=self.migration_review();run=todo['workflow']
        self.assertEqual(run['phase'],'migration_required',todo)
        self.assertEqual(self.git('rev-parse','HEAD'),head)
        self.assertEqual(self.git('rev-parse','origin/main'),head)
        self.assertFalse((Path(run['worktree']).parent/(run['run_id']+'-deployment.json')).exists())
        snap=self.store.snapshot()
        body=dict(id=todo['id'],action='migrate',revision=snap['revisions']['todos'][todo['id']],commit=run['commit'])
        with self.assertRaisesRegex(Conflict,'review_id'):self.workflow.start(body)
        body['review_id']=run['deployment_review']['review_id']
        with patch.object(self.workflow,'host_deployment',return_value={}) as check,patch.object(self.workflow,'launch_deployment') as launch:
            self.workflow.start(body);self.workflow.workers[todo['id']].join(10)
            check.assert_called_once_with('check','--review','a'*32)
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[-1],'deploy')
        self.assertEqual(self.git('rev-parse','origin/main'),run['integration_tested_commit'])

    def test_changed_main_invalidates_migration_approval_before_publish(self):
        todo=self.migration_review();run=todo['workflow']
        (self.repo/'other').write_text('unrelated');self.git('add','other');self.git('commit','-qm','Main advanced')
        head=self.git('rev-parse','HEAD');snap=self.store.snapshot()
        with patch.object(self.workflow,'launch_deployment') as launch:
            self.workflow.start(dict(id=todo['id'],action='migrate',revision=snap['revisions']['todos'][todo['id']],commit=run['commit'],review_id='a'*32))
            self.workflow.workers[todo['id']].join(10);launch.assert_not_called()
        current=self.store.snapshot()['data']['todos'][0]['workflow']
        self.assertEqual(current['phase'],'merge_failed');self.assertIn('since migration review',current['message'])
        self.assertEqual(self.git('rev-parse','HEAD'),head)

    def test_automatic_delivery_never_approves_migration(self):
        self.migration_review()
        self.options.update(automatic_merge=True,automatic_publish=True,automatic_deploy=True)
        with patch.object(self.workflow,'start') as start:self.workflow.tick();start.assert_not_called()

    def test_public_recovery_verifies_publication_without_new_integration(self):
        self.run_stage('implement');todo=self.run_stage('merge');run=todo['workflow']
        # Join the detached receipt writer before simulating failed deployment.
        receipt=Path(run['worktree']).parent/(run['run_id']+'-deployment.json')
        for _ in range(100):
            if receipt.exists():break
            time.sleep(.02)
        self.workflow.save(todo['id'],dict(run,phase='restart_failed'))
        before=self.git('rev-parse','HEAD')
        with patch.object(self.workflow,'integrate_and_deploy') as integrate,patch.object(self.workflow,'launch_deployment') as launch:
            self.run_stage('recover');integrate.assert_not_called();launch.assert_called_once()
        self.assertEqual(self.git('rev-parse','HEAD'),before)


    def test_migrating_phase_drains_until_verified_receipt(self):
        todo=self.migration_review();run=todo['workflow'];snapshot=self.store.snapshot()
        with patch.object(self.workflow,'host_deployment',return_value={}),patch.object(self.workflow,'host_argv',return_value=[sys.executable,'-c','print("verified host deployment")']):
            self.workflow.start(dict(id=todo['id'],action='migrate',revision=snapshot['revisions']['todos'][todo['id']],commit=run['commit'],review_id='a'*32))
            self.workflow.workers[todo['id']].join(10)
        self.assertEqual(self.workflow.status()['runs'][todo['id']]['phase'],'migrating')
        self.assertTrue(self.workflow.draining())
        for _ in range(100):
            self.workflow.reconcile()
            current=self.store.snapshot()['data']['todos'][0]
            if current['status']=='closed':break
            time.sleep(.02)
        self.assertEqual(current['workflow']['phase'],'done',current)
        self.assertFalse(self.workflow.draining())


if __name__=='__main__':unittest.main()
