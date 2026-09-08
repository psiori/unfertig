"""Optional host conformance: real supervisor/service restarts, disposable PRs.

Set UNFERTIG_TEST_HOST_CONTEXT to a wrapper containing scripts/tools.py.
Only its reusable scripts are copied; no host configuration or live state is used.
"""
import json
from http.client import IncompleteRead
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import unittest
import urllib.request

import test_workflow as fixtures


HOST = Path(os.environ['UNFERTIG_TEST_HOST_CONTEXT']) if os.environ.get('UNFERTIG_TEST_HOST_CONTEXT') else None


@unittest.skipUnless(HOST and (HOST/'scripts/tools.py').is_file(), 'Select the host supervisor contract explicitly')
class SupervisedQueueTests(unittest.TestCase):
    def exercise(self, fail_first):
        fixture = fixtures.WorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        ids, _ = fixture.queue_ready_tickets()
        board = fixture.store.root
        initial = fixture.store.snapshot()
        fixture.workflow.close(); fixture.store.close()
        shutil.copytree(HOST/'scripts', board/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        runtime = board/'tools/unfertig'; runtime.mkdir(parents=True)
        for source in Path(__file__).parent.iterdir():
            if source.is_file() and source.suffix in ('.py','.json','.js','.html','.css'):
                shutil.copy2(source, runtime/source.name)
        (runtime/'start.sh').write_text('exec '+shlex.quote(sys.executable)+' -B '+shlex.quote(str(runtime/'server.py'))+' "$@"\n')
        import socket
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        config = board/'state/unfertig/config/config.json'; config.parent.mkdir(parents=True)
        tools = board/'scripts/tools.py'
        failure = board/'.local/fail-deployment'
        failure.parent.mkdir(exist_ok=True)
        if fail_first:
            failure.touch()
        restart = board/'restart-fixture.py'
        restart.write_text('import subprocess,sys,pathlib\n'+
                          f'command=[{sys.executable!r},{str(tools)!r}]\n'+
                          'subprocess.run(command+["stop","--timeout","20","--json"],check=True)\n'+
                          'subprocess.run(command+["start","--timeout","20","--json"],check=True)\n'+
                          f'raise SystemExit(1 if pathlib.Path({str(failure)!r}).exists() else 0)\n')
        settings = dict(fixture.options, restart=[sys.executable, str(restart)])
        config.write_text(json.dumps(dict(format_version=fixtures.FORMAT_VERSION, mode='embedded',
            data='../../../data.json', repository='../../..', port=port,
            processing=dict(enabled=False, executable=fixture.processing['executable']), workflow=settings)))
        # Fake only GitHub's remote API. All merges, testing, process receipts,
        # board transactions and the wrapper supervisor execute normally.
        binary = board/'.local/bin'; binary.mkdir()
        github = binary/'gh'
        github.write_text('#!'+sys.executable+'\nimport json,subprocess,sys\n'+
            'prs='+repr(fixture.prs)+'\n'+
            'assert sys.argv[1:3]==["pr","view"],sys.argv\n'+
            'state=prs[sys.argv[3]]\n'+
            f'state["headRefOid"]=subprocess.check_output(["git","-C",{str(fixture.repo)!r},"rev-parse",state["headRefName"]],text=True).strip()\n'+
            'print(json.dumps(state))\n')
        github.chmod(0o755)
        config.with_name('launch.json').write_text(json.dumps(dict(args=['--config','{config}','--no-browser'],
            ready_pattern=url, startup_timeout=20, env={'PATH':str(binary)+os.pathsep+os.environ['PATH']})))
        with (board/'.gitignore').open('a') as output:
            output.write('\n.local/\nstate/local/\n__pycache__/\n')
        subprocess.run(['git','-C',str(board),'add','.'], check=True, capture_output=True)
        subprocess.run(['git','-C',str(board),'commit','-qm','Disposable supervisor configuration'], check=True)
        def control(action):
            return subprocess.run([sys.executable, str(tools), action, '--timeout','20','--json'],
                                  cwd=board, capture_output=True, text=True, timeout=30)
        self.addCleanup(lambda: control('stop'))
        self.assertEqual(control('start').returncode, 0)
        def snapshot():
            with urllib.request.urlopen(url+'/api/state', timeout=2) as response:
                return json.load(response)
        def wait_for(phase):
            deadline = time.monotonic()+60
            latest = None
            while time.monotonic() < deadline:
                try:
                    latest = snapshot()
                    if latest['data']['todos'][0]['workflow']['phase'] == phase:
                        return latest
                except (OSError, IncompleteRead):
                    pass  # The actual supervisor is stopping/starting the service.
                time.sleep(.1)
            self.fail(f'Timed out waiting for {phase}: {latest}')
        if fail_first:
            failed = wait_for('restart_failed')
            for before, after in zip(initial['data']['todos'][1:], failed['data']['todos'][1:]):
                self.assertEqual(after['workflow'], before['workflow'])
            with urllib.request.urlopen(url+'/api/workflow') as response:
                status = json.load(response)
            self.assertEqual(status['queue_blocked_by'], ids[0])
            self.assertEqual(status['runs'][ids[1]]['waiting_for'], ids[0])
            failure.unlink()
            first = failed['data']['todos'][0]
            request = urllib.request.Request(url+'/api/workflow/action', method='PUT',
                headers={'Content-Type':'application/json','X-Board-Token':failed['token']},
                data=json.dumps(dict(id=ids[0], action='recover', commit=first['workflow']['commit'],
                    revision=failed['revisions']['todos'][ids[0]], request_id='supervised-recovery')).encode())
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
        wait_for('done')
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            try:
                result = snapshot()
                if all(t['workflow']['phase'] == 'done' for t in result['data']['todos']):
                    break
            except (OSError, IncompleteRead):
                pass
            time.sleep(.1)
        else:
            self.fail('Subsequent PRs did not complete across supervisor restarts')
        for before, after in zip(initial['data']['todos'], result['data']['todos']):
            self.assertEqual(before['workflow']['queued_at'], after['workflow']['queued_at'])
            self.assertEqual(before['workflow']['run_id'], after['workflow']['run_id'])
            for key, value in before['workflow']['action_requests'].items():
                self.assertEqual(after['workflow']['action_requests'][key], value)

    def test_successful_delivery_drains_queue_across_actual_supervisor_restarts(self):
        self.exercise(False)

    def test_failed_delivery_pauses_after_actual_restart_then_public_recovery_resumes(self):
        self.exercise(True)
