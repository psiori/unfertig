# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Optional argv recipes for configured Unfertig, Kermit, native and context projects.

No discovery: the administrator selects a kind and exact project/context paths.
Preview state is disposable. Production process control is limited to saved PIDs
whose command still contains the exact configured artifact entry point.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import urllib.request


def run(args, cwd):
    subprocess.run(list(map(str,args)), cwd=cwd, check=True)



def start_managed(context, timeout):
    """Wait for the supervisor, whose startup work outlives a client wait."""
    deadline = time.monotonic() + timeout
    env = {k: v for k, v in os.environ.items()
           if k not in ('VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'UV_PROJECT')}
    command = ['sh', str(context/'start_tools.sh'), '--timeout', str(min(5, timeout)), '--json']
    result = subprocess.run(command, cwd=context, env=env, capture_output=True, text=True, timeout=30)
    session = None
    while True:
        try:
            state = json.loads(result.stdout)
        except (ValueError, TypeError):
            state = None
        if state is not None:
            if not isinstance(state, dict):
                raise ValueError('Invalid managed supervisor status.')
            current = state.get('run_id')
            if session and current != session:
                raise ValueError('Managed startup session changed; inspect its status before retrying.')
            session = current or session
            phase = state.get('phase')
            if phase == 'running' and result.returncode == 0:
                if not any(t.get('name') == 'unfertig' and t.get('state') == 'running'
                           for t in state.get('tools', [])):
                    raise ValueError('Managed supervisor is running without the Unfertig service.')
                return
            if phase != 'starting':
                raise ValueError('Managed startup failed: ' + str(state.get('error') or phase))
        elif '--status' in command:
            raise ValueError('Cannot read managed startup status: ' + (result.stderr or result.stdout)[-2000:])
        if time.monotonic() >= deadline:
            raise ValueError('Managed startup is still pending after the wait budget; the supervisor may continue. '
                             'Inspect start_tools.sh --status before retrying.')
        time.sleep(min(2, max(0, deadline - time.monotonic())))
        command = ['sh', str(context/'start_tools.sh'), '--status', '--json']
        result = subprocess.run(command, cwd=context, env=env, capture_output=True, text=True, timeout=30)


def spawn_artifact(argv, repository, marker, url=''):
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.is_file():
        previous=json.loads(marker.read_text())
        process=subprocess.run(['ps','-p',str(previous['pid']),'-o','command='],capture_output=True,text=True)
        if process.returncode == 0:
            # Never kill a reused PID or an unrelated listener.
            if previous['entry'] not in process.stdout:
                raise ValueError('Saved artifact PID changed ownership; inspect it manually.')
            os.killpg(previous['pid'],signal.SIGTERM)
            for _ in range(50):
                if subprocess.run(['ps','-p',str(previous['pid'])],stdout=subprocess.DEVNULL).returncode:
                    break
                time.sleep(.1)
            else:
                raise ValueError('Previous artifact did not stop; inspect it manually.')
    if url:
        try:
            urllib.request.urlopen(url,timeout=1).close()
        except OSError:
            pass
        else:
            raise ValueError('An unmanaged artifact already occupies the production URL. Configure its stop command first.')
    log=(marker.parent/'artifact.log').open('a')
    child=subprocess.Popen(argv,cwd=repository,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    log.close()
    entry=next((arg for arg in argv if str(repository) in arg and (arg.endswith('.py') or arg.endswith('/unendlich'))), '')
    marker.write_text(json.dumps(dict(format_version='1.5.0',pid=child.pid,entry=entry)))
    deadline=time.monotonic()+30
    while True:
        if child.poll() is not None:
            raise ValueError('Artifact exited; inspect '+str(marker.parent/'artifact.log'))
        if not url:
            time.sleep(1)
            if child.poll() is not None:
                raise ValueError('Native artifact exited immediately.')
            break
        try:
            with urllib.request.urlopen(url,timeout=1) as response:
                if response.status==200:break
        except OSError:
            pass
        if time.monotonic()>deadline:raise ValueError('Artifact did not become healthy.')
        time.sleep(.2)
    # This launcher intentionally leaves the verified child running.
    child.returncode = 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=['unfertig','kermit','native','context'])
    parser.add_argument('stage',choices=['test','preview','restart','preflight'])
    parser.add_argument('--repository',type=Path,required=True)
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--data',type=Path)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--startup-timeout',type=int,default=900,
                        help='Seconds to wait for managed validation and startup (default: 900).')
    args=parser.parse_args();repo=args.repository.resolve();context=args.context.resolve()
    if not 0 < args.startup_timeout <= 3600:
        parser.error('--startup-timeout must be between 1 and 3600 seconds.')
    uv=shutil.which('uv') or str(Path.home()/'.local/bin/uv')
    if args.stage == 'preflight':
        if args.kind != 'unfertig':
            raise ValueError('Migration preflight is specific to the managed Unfertig board.')
        from deployment_preflight import assess
        result = assess(repo, context)
        result.pop('storage_digest', None)
        print(json.dumps(result))
        return
    if args.stage=='test':
        run(['git','diff','--check'],repo)
        if args.kind=='unfertig':
            runner = repo / 'suite_runner.py'
            if runner.is_file():
                run([uv,'run','--no-project','--python','3.12','python',str(runner),'--javascript'],repo)
            else:
                run([uv,'run','--no-project','--python','3.12','python','-m','unittest','discover','-q'],repo)
                run(['node','--test',*[str(p) for p in sorted(repo.glob('test_*.cjs'))]],repo)
        elif args.kind=='kermit':
            run([uv,'run','--locked','python','-m','unittest','discover','-s','tests','-q'],repo)
        elif args.kind=='native':
            run(['bash','ci/check.sh'],repo)
        else:
            json.loads((repo/'node.json').read_text())
            if not (repo/'AGENTS.md').is_file():raise ValueError('Missing context instructions.')
        return
    if args.stage=='preview':
        if not args.data:raise ValueError('Preview requires a disposable data directory.')
        data=args.data.resolve();data.mkdir(parents=True,exist_ok=True)
        if args.kind=='unfertig':
            argv=[uv,'run','--no-project','--python','3.12','--script',str(repo/'server.py'),'--data',str(data/'data.json'),'--init','--no-git','--no-browser','--port',str(args.port)]
        elif args.kind=='kermit':
            state=data/'state';state.mkdir(exist_ok=True)
            if not (state/'.git').exists():
                run(['git','init','-q',str(state)],repo)
                (state/'.gitignore').write_text('/local/\n')
                run(['git','-C',str(state),'add','.gitignore'],repo)
                run(['git','-C',str(state),'-c','user.name=Preview','-c','user.email=preview@example.invalid','commit','-qm','Disposable preview state'],repo)
            companies=data/'companies';companies.mkdir(exist_ok=True)
            argv=[uv,'run','--locked','python',str(repo/'scripts/dashboard.py'),'--state-dir',str(state),'--companies-dir',str(companies),'--no-browser','--port',str(args.port)]
        elif args.kind=='native':
            argv=[str(repo/'build/game/app/unendlich'),'--windowed']
        else:
            argv=['open',str(repo)]
        os.chdir(repo);os.execvp(argv[0],argv)
    elif args.kind=='unfertig':
        raise ValueError('Legacy in-transaction restart is disabled. Configure workflow.after_publish to request a cooperative host update; see DEPLOYMENT.md.')
    elif args.kind=='kermit':
        spawn_artifact([uv,'run','--locked','python',str(repo/'scripts/dashboard.py'),'--no-browser'],repo,
            context/'.local/unfertig-workflow/artifact.json','http://127.0.0.1:8766')
    elif args.kind=='native':
        run(['cmake','-B','build','-DCMAKE_BUILD_TYPE=RelWithDebInfo'],repo)
        run(['cmake','--build','build'],repo)
        spawn_artifact([str(repo/'build/game/app/unendlich'),'--windowed'],repo,context/'.local/unfertig-workflow/artifact.json')
    else:
        # A context repository has documents, not a running application.
        run(['open',str(repo)],repo)


if __name__=='__main__':main()
