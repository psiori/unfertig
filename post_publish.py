"""Durable post-publication jobs; publication success never depends on a hook.

Commands are trusted instance configuration. Retries receive the same event ID
and must be idempotent. The detached executor retains a lock through its child.
"""
import copy
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import uuid

from storage import atomic, encode, Conflict
from versions import FORMAT_VERSION, inspect
from processing import child_environment


def settings(values, base, repository, branch):
    if not isinstance(values, list):
        raise ValueError('workflow.after_publish must be a list.')
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('Invalid after_publish hook configuration.')
        value = copy.deepcopy(value)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(value.get('id', ''))) or any(h['id'] == value['id'] for h in result):
            raise ValueError('Hooks require unique stable IDs.')
        argv = value.get('command')
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x or '\0' in x for x in argv):
            raise ValueError('Hook command must be an argument array without a shell.')
        for field, default in [('repository', repository), ('cwd', repository)]:
            raw = value.get(field, default)
            if not isinstance(raw, str) or not raw:
                raise ValueError('Hook '+field+' must name a directory.')
            value[field] = str((Path(base) / raw).resolve())
        value.setdefault('branch', branch)
        if not isinstance(value['branch'], str) or not value['branch'] or value['branch'].startswith('-'):
            raise ValueError('Hook branch must be explicit.')
        value.setdefault('timeout_seconds', 60)
        if type(value['timeout_seconds']) is not int or not 1 <= value['timeout_seconds'] <= 3600:
            raise ValueError('Hook timeout must be 1–3600 seconds.')
        result.append(value)
    return result


def events(workflow, run):
    result = copy.deepcopy(run.get('post_publish', []))
    repositories = run.get('repositories', [run])
    for item in repositories:
        commit = item.get('published_commit')
        if not commit:
            continue
        branch = item.get('recipe', {}).get('base_branch', workflow.options['base_branch'])
        for hook in workflow.options.get('after_publish', []):
            if hook['repository'] != item['repository'] or hook['branch'] != branch:
                continue
            if any(e['hook']['id'] == hook['id'] and e['commit'] == commit for e in result):
                continue
            result.append(dict(id=uuid.uuid4().hex, hook=copy.deepcopy(hook), repository=item['repository'],
                               branch=branch, commit=commit, status='pending', attempt=0, message='Waiting to run.'))
    return result


def validate(events):
    if not isinstance(events, list):
        raise ValueError('post_publish must be a list.')
    ids = set()
    for event in events:
        if not isinstance(event, dict) or not re.fullmatch('[a-f0-9]{32}', str(event.get('id', ''))) or event['id'] in ids:
            raise ValueError('Invalid publication event identity.')
        ids.add(event['id'])
        if event.get('status') not in ('pending', 'complete', 'failed') or type(event.get('attempt')) is not int or event['attempt'] < 0:
            raise ValueError('Invalid publication event state.')
        if not re.fullmatch('[a-f0-9]{40,64}', str(event.get('commit', ''))):
            raise ValueError('Invalid published commit.')
        if not isinstance(event.get('hook'), dict) or not isinstance(event.get('repository'), str) or not isinstance(event.get('branch'), str):
            raise ValueError('Invalid publication hook identity.')
        hook = settings([event['hook']], Path('/'), event['repository'], event['branch'])[0]
        if hook['repository'] != event['repository'] or hook['branch'] != event['branch']:
            raise ValueError('Publication hook target differs from its event.')


def locked(path):
    import fcntl
    stream = path.open('a+')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        return None
    return stream


class Hooks:
    def __init__(self, workflow):
        self.workflow = workflow
        self.root = workflow.store.root / '.local' / 'post-publish'
        self.root.mkdir(parents=True, exist_ok=True)
        self.children = {}
        self.error = ''

    def tick(self, dispatch=True):
        with self.workflow.store.lock:
            self.reconcile(dispatch)

    def reconcile(self, dispatch=True):
        from processing import system_id
        for ident, child in list(self.children.items()):
            if child.poll() is not None:
                child.wait(); del self.children[ident]
        try:
            snap = self.workflow.store.snapshot()
            if not any(e.get('status') == 'pending' for t in snap['data']['todos'] for e in t.get('workflow', {}).get('post_publish', [])):
                self.error = ''
                return
            snap = self.workflow.snapshot()
            for todo in snap['data']['todos']:
                run = todo.get('workflow', {})
                if run.get('system') != system_id():
                    continue
                for event in run.get('post_publish', []):
                    if event['status'] != 'pending':
                        continue
                    name = event['id']+'-'+str(event['attempt'])
                    path = self.root / (name+'.json')
                    if path.exists():
                        receipt = json.loads(path.read_text())
                        if inspect(receipt, 'hook receipt')[0] == 'read_only':
                            raise ValueError('Hook receipt requires a newer application.')
                        if receipt.get('id') != event['id'] or receipt.get('attempt') != event['attempt']:
                            raise ValueError('Hook receipt identity mismatch.')
                        if receipt['status'] in ('complete', 'failed'):
                            updated = copy.deepcopy(run)
                            saved = next(e for e in updated['post_publish'] if e['id'] == event['id'])
                            saved.update(status=receipt['status'], message=receipt['message'])
                            self.workflow.save(todo['id'], updated)
                            return
                    if not dispatch or name in self.children:
                        continue
                    if os.name != 'posix':
                        raise ValueError('Durable hook execution currently requires POSIX coordination.')
                    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(path), json.dumps(event)],
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             env=child_environment(), start_new_session=True)
                    self.children[name] = child
            self.error = ''
        except (ValueError, OSError, Conflict, KeyError) as error:
            self.error = str(error)

    def busy(self):
        if any(c.poll() is None for c in self.children.values()):
            return True
        if os.name == 'posix':
            for path in self.root.glob('*.lock'):
                lock = locked(path)
                if lock is None:
                    return True
                lock.close()
        return False

    def retry(self, todo_id, event_id):
        with self.workflow.store.lock:
            self.retry_locked(todo_id, event_id)

    def retry_locked(self, todo_id, event_id):
        if self.busy():
            raise ValueError('A hook is still running.')
        todo = next((t for t in self.workflow.snapshot()['data']['todos'] if t['id'] == todo_id), None)
        if todo is None:
            raise ValueError('Unknown task for hook retry.')
        run = copy.deepcopy(todo['workflow'])
        from processing import system_id
        if run.get('system') != system_id():
            raise ValueError('Retry this hook on its owning instance.')
        event = next((e for e in run.get('post_publish', []) if e['id'] == event_id), None)
        if event is None:
            raise ValueError('Unknown publication hook event.')
        if event['status'] != 'failed':
            raise ValueError('Only failed hooks can be retried.')
        event.update(status='pending', attempt=event['attempt']+1, message='Explicit retry queued.')
        self.workflow.save(todo_id, run)


def execute(path, event):
    lock = locked(path.with_suffix('.lock'))
    if lock is None:
        return
    with lock:
        previous = json.loads(path.read_text()) if path.exists() else None
        if previous:
            if inspect(previous, 'hook receipt')[0] == 'read_only':
                return
            if previous['status'] in ('complete', 'failed'):
                return
            # The command may have completed before a crash. Require an explicit
            # idempotent retry instead of silently repeating an unknown side effect.
            previous.update(status='failed', message='Hook was interrupted; inspect its result and retry with the same event ID.')
            atomic(path, encode(previous)); return
        receipt = dict(format_version=FORMAT_VERSION, id=event['id'], attempt=event['attempt'], status='running', message='Running.')
        atomic(path, encode(receipt))
        env = {**child_environment(), 'UNFERTIG_EVENT_ID':event['id'], 'UNFERTIG_PUBLISHED_COMMIT':event['commit'],
               'UNFERTIG_PUBLISHED_REPOSITORY':event['repository'], 'UNFERTIG_PUBLISHED_BRANCH':event['branch']}
        try:
            with path.with_suffix('.log').open('w') as log:
                child = subprocess.Popen(event['hook']['command'], cwd=event['hook']['cwd'], env=env,
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         start_new_session=True, pass_fds=(lock.fileno(),))
                try:
                    code = child.wait(timeout=event['hook']['timeout_seconds'])
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL); child.wait()
                    raise ValueError('Hook timed out; inspect its log before retrying.')
                if code:
                    raise ValueError('Hook exited '+str(code)+'; see its retained log.')
            receipt.update(status='complete', message='Hook completed.')
        except (OSError, ValueError) as error:
            receipt.update(status='failed', message=str(error))
        atomic(path, encode(receipt))


if __name__ == '__main__':
    execute(Path(sys.argv[1]), json.loads(sys.argv[2]))
