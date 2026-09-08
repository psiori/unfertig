"""Cooperative host restart protocol, separate from task completion.

The host owns requests and installs files only after this process exits 75.
Session identities prevent an old ready receipt from authorizing a later exit.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading

from storage import atomic, encode, Conflict
from versions import FORMAT_VERSION


class Maintenance:
    def __init__(self, server, environment=None):
        env = os.environ if environment is None else environment
        if environment is None and env.get('UM_TOOL_DIR') and Path(env['UM_TOOL_DIR']).resolve() != Path(__file__).resolve().parent:
            env = {}  # A feature preview is never the host's managed runtime.
        self.server = server
        self.update_path = Path(env['UM_UPDATE_STATUS']) if env.get('UM_UPDATE_STATUS') else None
        self.session = env.get('UM_RESTART_SESSION', '')
        self.request_path = Path(env['UM_RESTART_REQUEST']) if env.get('UM_RESTART_REQUEST') else None
        self.status_path = Path(env['UM_RESTART_STATUS']) if env.get('UM_RESTART_STATUS') else None
        self.guard = threading.RLock()
        self.writers = 0
        self.quiescing = False
        self.pending = False
        self.exit_code = 0
        self.phase = 'idle'
        self.blockers = []
        self.error = ''
        self.hooks = None

    @property
    def supported(self):
        return bool(self.session and self.request_path and self.status_path)

    @contextmanager
    def mutation(self):
        with self.guard:
            accepted = not self.quiescing
            if accepted:
                self.writers += 1
        try:
            yield accepted
        finally:
            if accepted:
                with self.guard:
                    self.writers -= 1

    def view(self):
        hooks = []
        if self.hooks:
            for todo in self.server.store.snapshot()['data']['todos']:
                for event in todo.get('workflow', {}).get('post_publish', []):
                    hooks.append(dict(todo=todo['id'], id=event['id'], hook=event['hook']['id'],
                                      status=event['status'], message=event.get('message', ''), commit=event['commit']))
        update = {}
        if self.update_path and self.update_path.exists():
            update = json.loads(self.update_path.read_text())
            if update.get('schema_version') != 1:
                raise ValueError('Unsupported host update status schema.')
        return dict(update=update, supported=self.supported, phase=self.phase, pending=self.pending,
                    blockers=list(self.blockers), error=self.error or (self.hooks.error if self.hooks else ''),
                    runtime_commit=getattr(self.server.store, 'context', {}).get('runtime_commit', ''), hooks=hooks)

    def announce(self):
        if not self.supported:
            return
        value = dict(format_version=FORMAT_VERSION, protocol_version='1.0.0', session=self.session,
                     phase=self.phase, blockers=self.blockers, error=self.error,
                     runtime_commit=getattr(self.server.store, 'context', {}).get('runtime_commit', ''))
        if not self.status_path.exists() or json.loads(self.status_path.read_text()) != value:
            atomic(self.status_path, encode(value))

    def tick(self):
        from processing import system_id
        from post_publish import Hooks
        workflow, processing = self.server.workflow, self.server.processing
        if workflow and self.hooks is None:
            self.hooks = Hooks(workflow)
        try:
            if self.supported and self.request_path.exists():
                request = json.loads(self.request_path.read_text())
                if request.get('session') == self.session:
                    if request.get('schema_version') != 1 or request.get('protocol_version') != '1.0.0':
                        raise ValueError('Unsupported host restart request. Update the host protocol.')
                    self.pending = True
            if workflow:
                with workflow.lock:
                    workflow.restart_pending = self.pending
            if processing:
                with processing.lock:
                    processing.restart_pending = self.pending
            if self.hooks:
                self.hooks.tick(dispatch=not self.pending)
            if self.pending and not self.quiescing:
                blockers = []
                if workflow:
                    blockers += ['Task '+ident+' is running' for ident in workflow.active_workers()]
                    # Detached workers can survive a coordinator failure. The
                    # retained process identity must also prove they have ended.
                    for todo in workflow.store.snapshot()['data']['todos']:
                        run = todo.get('workflow', {})
                        if run.get('system') != system_id():
                            continue
                        if not run.get('worktree'):
                            continue
                        try:
                            workflow.guard_process(run)
                        except (ValueError, OSError, Conflict) as error:
                            blockers.append(todo['id']+': '+str(error))
                        if run.get('phase') in ('restarting', 'migrating', 'recovering'):
                            blockers.append(todo['id']+': legacy deployment requires recovery')
                if processing and ((processing.worker and processing.worker.is_alive()) or
                                   (processing.child and processing.child.poll() is None)):
                    blockers.append('Idea processing is running')
                if self.hooks and self.hooks.busy():
                    blockers.append('Post-publication command is running')
                with self.guard:
                    if self.writers:
                        blockers.append('Finishing API writes')
                    with self.server.store.lock:
                        snapshot = self.server.store.snapshot()
                        if snapshot['history']['pending']:
                            blockers.append('Local Git history needs retry')
                        if not blockers:
                            self.quiescing = True
                self.blockers = blockers
                self.phase = 'draining' if blockers else 'ready'
            self.error = ''
            self.announce()
            if self.quiescing and not self.exit_code:
                self.exit_code = 75
                # shutdown waits for serve_forever, so invoke it on another thread.
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        except (ValueError, OSError, Conflict, KeyError) as error:
            self.error = str(error)
            # An unreadable receipt or uncertain writer never authorizes exit.
            self.quiescing = False
            self.phase = 'failed'

    def action(self, body):
        if body.get('action') != 'retry_hook' or not self.hooks:
            raise ValueError('Choose a failed post-publication hook to retry.')
        if self.pending:
            raise ValueError('Restart is pending; retry after the instance returns.')
        self.hooks.retry(body.get('todo'), body.get('event'))
        return self.view()
