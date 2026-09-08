"""Publication, durable hooks and draining use disposable boards and processes."""
import copy
import json
import os
import socket
import subprocess
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from instance_maintenance import Maintenance
from managed_completion import publish
from post_publish import Hooks, events, execute, settings
from storage import atomic, encode, Conflict
from versions import FORMAT_VERSION, migrate, inspect
import test_workflow as fixtures


class PublicationMaintenanceTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    git = fixtures.WorkflowTests.git
    fake_github = fixtures.WorkflowTests.fake_github
    run_stage = fixtures.WorkflowTests.run_stage

    def hook(self, command=None):
        return settings([dict(id='deploy', command=command or [sys.executable, '-c', 'pass'])],
                        self.root, str(self.repo), 'main')[0]

    def completed(self, command=None):
        self.options['after_publish'] = [self.hook(command)]
        self.run_stage('implement')
        with patch.object(self.workflow, 'launch_deployment') as deploy:
            todo = self.run_stage('merge')
        deploy.assert_not_called()
        self.assertEqual(todo['status'], 'closed', todo)
        self.assertEqual(todo['workflow']['phase'], 'done', todo)
        self.assertEqual(len(todo['workflow']['post_publish']), 1)
        return todo

    def wait_hooks(self, hooks):
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            hooks.tick()
            todo = self.store.snapshot()['data']['todos'][0]
            if todo['workflow']['post_publish'][0]['status'] != 'pending':
                for child in hooks.children.values():child.wait(timeout=2)
                hooks.tick(dispatch=False)
                return todo
            time.sleep(.02)
        self.fail(hooks.error)

    def test_hook_failure_is_independent_and_explicit_retry_keeps_event_identity(self):
        marker = self.root/'marker'
        todo = self.completed([sys.executable, '-c', f'import pathlib; raise SystemExit(0 if pathlib.Path({str(marker)!r}).exists() else 1)'])
        hooks = Hooks(self.workflow)
        failed = self.wait_hooks(hooks)
        event = failed['workflow']['post_publish'][0]
        self.assertEqual(event['status'], 'failed')
        self.assertEqual(failed['status'], 'closed')
        self.assertIsNone(self.workflow.status()['queue_blocked_by'])
        marker.touch()
        hooks.retry(todo['id'], event['id'])
        result = self.wait_hooks(hooks)['workflow']['post_publish'][0]
        self.assertEqual((result['id'],result['attempt'],result['status']), (event['id'],1,'complete'))

    def test_event_is_atomic_with_closure_and_not_replayed_after_restart(self):
        marker = self.root/'calls'
        todo = self.completed([sys.executable, '-c', f'from pathlib import Path; p=Path({str(marker)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x")'])
        self.assertFalse(marker.exists())
        event = todo['workflow']['post_publish'][0]
        hooks = Hooks(self.workflow)
        self.wait_hooks(hooks)
        Hooks(self.workflow).tick()
        self.assertEqual(marker.read_text(),'x')
        self.assertEqual(len(events(self.workflow, todo['workflow'])), 1)
        # Frozen hook command is not replaced by later configuration changes.
        self.options['after_publish'] = [self.hook(['/usr/bin/false'])]
        self.assertEqual(events(self.workflow,todo['workflow'])[0]['hook'],event['hook'])

    def test_interrupted_hook_is_retained_without_automatic_reexecution(self):
        todo = self.completed()
        event = todo['workflow']['post_publish'][0]
        path = self.root/'interrupted.json'
        atomic(path, encode(dict(format_version=FORMAT_VERSION, id=event['id'], attempt=0, status='running', extension=7)))
        with patch('post_publish.subprocess.Popen') as launch:
            execute(path, event)
            launch.assert_not_called()
        result = json.loads(path.read_text())
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['extension'],7)

    def test_hooks_filter_publication_repository_and_branch(self):
        hook = self.hook()
        self.options['after_publish'] = [hook]
        run = dict(repository=str(self.root/'other'), published_commit='a'*40)
        self.assertEqual(events(self.workflow,run),[])
        run['repository']=str(self.repo);run['recipe']={'base_branch':'other'}
        self.assertEqual(events(self.workflow,run),[])

    def test_restart_coalesces_and_waits_for_workers_api_and_history(self):
        todo = self.run_stage('implement')
        request, status = self.root/'request.json', self.root/'status.json'
        shutdown = Mock()
        server = SimpleNamespace(store=self.store, workflow=self.workflow, processing=None, shutdown=shutdown)
        maintenance = Maintenance(server, dict(UM_RESTART_SESSION='session',UM_RESTART_REQUEST=str(request),UM_RESTART_STATUS=str(status)))
        worker = Mock(); worker.is_alive.return_value = True
        self.workflow.workers['T0001'] = worker
        request.write_text(json.dumps(dict(schema_version=1, protocol_version='1.0.0',session='session')))
        maintenance.tick();maintenance.tick()
        self.assertTrue(self.workflow.restart_pending)
        self.assertEqual(maintenance.phase,'draining')
        shutdown.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'Restart pending'):
            self.workflow.start({})
        worker.is_alive.return_value = False
        with maintenance.mutation() as accepted:
            self.assertTrue(accepted);maintenance.tick();self.assertEqual(maintenance.phase,'draining')
        pending=copy.deepcopy(self.store.snapshot());pending['history']['pending']=True
        with patch.object(self.store,'snapshot',return_value=pending):
            maintenance.tick();self.assertEqual(maintenance.phase,'draining')
            self.assertIn('Local Git history needs retry',maintenance.blockers)
        maintenance.tick()
        self.assertEqual(maintenance.phase,'ready')
        self.assertEqual(maintenance.exit_code,75)
        with maintenance.mutation() as accepted:
            self.assertFalse(accepted)
        maintenance.tick()
        deadline=time.monotonic()+1
        while not shutdown.called and time.monotonic()<deadline:time.sleep(.01)
        shutdown.assert_called_once()
        self.assertEqual(json.loads(status.read_text())['session'],'session')

    def test_stale_restart_session_does_not_pause_work(self):
        request=self.root/'request.json';request.write_text(json.dumps(dict(schema_version=1,protocol_version='1.0.0',session='old')))
        server=SimpleNamespace(store=self.store,workflow=self.workflow,processing=None,shutdown=Mock())
        maintenance=Maintenance(server,dict(UM_RESTART_SESSION='new',UM_RESTART_REQUEST=str(request),UM_RESTART_STATUS=str(self.root/'status.json')))
        maintenance.tick()
        self.assertFalse(maintenance.pending);self.assertFalse(self.workflow.restart_pending)

    def test_migration_preserves_legacy_claims_and_never_invents_events(self):
        value=dict(format_version='1.19.0', workflow=dict(phase='restart_failed',restart=['keep'],extension=7))
        original=copy.deepcopy(value)
        result=migrate(value,'todo')
        self.assertEqual(value,original)
        self.assertEqual(result['workflow'],value['workflow'])
        self.assertEqual(migrate(result,'todo'),result)
        explicit=dict(format_version='1.19.0',workflow=dict(after_publish=[{'extension':'preserve'}],restart=['old'],max_workers=4))
        self.assertEqual(migrate(explicit,'config')['workflow'],explicit['workflow'])
        self.assertEqual(inspect(result,supported='1.19.0')[0],'read_only')

    def test_github_lag_is_confirmed_without_duplicate_push(self):
        todo=self.run_stage('implement');run=copy.deepcopy(todo['workflow'])
        old=run['commit']
        self.workflow.git('commit','--allow-empty','-m','New checkpoint',cwd=run['worktree'])
        commit=self.workflow.git('rev-parse','HEAD',cwd=run['worktree'])
        real=self.workflow.pr_state;count=0
        def lag(run):
            nonlocal count
            state=real(run);count+=1
            if count<4:state['headRefOid']=old
            return state
        with patch.object(self.workflow,'pr_state',side_effect=lag), patch.object(self.workflow,'git',wraps=self.workflow.git) as git:
            publish(self.workflow,run,commit)
            self.assertEqual(sum(c.args[0]=='push' for c in git.call_args_list),1)
        self.assertEqual(run['publication']['commit'],commit)

    def test_github_delayed_confirmation_resumes_without_push(self):
        todo=self.run_stage('implement');run=copy.deepcopy(todo['workflow'])
        old=run['kickoff_commit'];real=self.workflow.pr_state;state=real(run);state['headRefOid']=old
        with patch.object(self.workflow,'pr_state',return_value=state),patch('managed_completion.time.sleep'):
            with self.assertRaisesRegex(Conflict,'delayed'):publish(self.workflow,run,run['commit'])
        with patch.object(self.workflow,'git',wraps=self.workflow.git) as git:
            publish(self.workflow,run,run['commit'])
            self.assertFalse(any(c.args[0]=='push' for c in git.call_args_list))
        self.assertEqual(run['publication']['status'],'confirmed')

    def test_child_commands_do_not_inherit_supervisor_restart_authority(self):
        from processing import child_environment
        with patch.dict(os.environ,dict(UM_RESTART_SESSION='host',UM_RESTART_REQUEST='/tmp/host',UM_RESTART_STATUS='/tmp/status',UM_UPDATE_STATUS='/tmp/update')):
            env=child_environment()
            self.assertFalse(any(k.startswith('UM_RESTART_') or k=='UM_UPDATE_STATUS' for k in env))

    def test_actual_service_exits_after_drain_and_keeps_queued_task(self):
        todo=self.run_stage('implement')
        snap=self.store.snapshot()
        with patch.object(self.workflow,'dispatch'):
            self.workflow.start(dict(id=todo['id'],action='merge',revision=snap['revisions']['todos'][todo['id']],commit=todo['workflow']['commit']))
        before=self.store.snapshot()['data']['todos'][0]
        board=self.store.root
        self.workflow.close();self.store.close()
        config=board/'config.json'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        config.write_text(json.dumps(dict(format_version=FORMAT_VERSION,data='data.json',repository='.',mode='embedded',
            port=port,processing=dict(enabled=False),workflow=self.options)))
        with (board/'.gitignore').open('a') as stream:stream.write('\n.local/\n')
        subprocess.run(['git','-C',str(board),'add','.'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(board),'commit','-qm','Disposable restart configuration'],check=True,capture_output=True)
        request=self.root/'restart.json';status=self.root/'ready.json'
        request.write_text(json.dumps(dict(schema_version=1,protocol_version='1.0.0',session='actual-service')))
        app=Path(__file__).resolve().parent
        env={**os.environ,'UM_RESTART_SESSION':'actual-service','UM_RESTART_REQUEST':str(request),
             'UM_RESTART_STATUS':str(status),'UM_TOOL_DIR':str(app)}
        process=subprocess.run([sys.executable,'-B',str(app/'server.py'),'--config',str(config),'--no-browser'],
            cwd=app,env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(process.returncode,75,process.stdout+process.stderr)
        self.assertEqual(json.loads(status.read_text())['phase'],'ready')
        after=json.loads((board/'todos'/f"{todo['id']}.json").read_text())
        self.assertEqual(after['workflow'],before['workflow'])
        self.assertEqual(after['status'],'started')
