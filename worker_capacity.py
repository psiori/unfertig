"""Owner-local worker settings; no aggregate or filesystem execution endpoint."""
import copy
import json
import subprocess

from storage import Conflict, atomic, digest, encode
from versions import inspect


def validate_limit(value):
    if type(value) is not int or not 1 <= value <= 8:
        raise ValueError('workflow.max_workers must be 1–8.')
    return value


class WorkerSettings:
    def __init__(self, workflow):
        self.workflow = workflow
        self.store = workflow.store

    def source(self):
        path = self.store.config
        if not path or not path.is_file():
            raise ValueError('An explicit instance configuration is required to edit workers.')
        generated = path.name == 'machine.local.json'
        root = path.parents[3] if len(path.parents) > 3 else None
        local = generated or (root is not None and path == root / 'state/unfertig/config/config.json')
        if local:
            # Only the documented UM host layout is supported. Never write the
            # generated effective config or its baseline.
            if root is None:
                raise ValueError('Unsupported generated configuration location.')
            if path not in (root / 'state/unfertig/config/machine.local.json', root / 'state/unfertig/config/config.json'):
                raise ValueError('Unsupported generated configuration location.')
            baseline = path.with_name('machine-baseline.local.json')
            if generated and (not baseline.is_file() or baseline.read_bytes() != path.read_bytes()):
                raise ValueError('Effective configuration changed; reconcile it with the host before editing.')
            path = root / 'state/local/unfertig/config/config.json'
            from configuration import git_root
            if git_root(self.store.root) != root or git_root(path) != root:
                raise ValueError('Worker preferences must belong to the board owner.')
            relative = str(path.relative_to(root))
            ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', '--', relative])
            tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '--', relative], capture_output=True, text=True, check=True)
            if ignored.returncode or tracked.stdout:
                raise ValueError('Local worker preferences must be ignored and untracked.')
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Configuration paths cannot contain symlinks.')
        value = json.loads(path.read_bytes()) if path.exists() else {}
        if inspect(value, 'worker configuration')[0] == 'read_only':
            raise ValueError('Worker configuration is newer; update before editing.')
        if not isinstance(value.get('workflow', {}), dict):
            raise ValueError('workflow must be an object.')
        return path, value, local

    def view(self):
        try:
            _, value, local = self.source()
            return dict(editable=True, revision=digest(value), layer='local' if local else 'instance')
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return dict(editable=False, error=str(error))

    def save(self, body):
        if set(body) != {'max_workers', 'revision'}:
            raise ValueError('Expected max_workers and configuration revision only.')
        limit = validate_limit(body['max_workers'])
        with self.workflow.lock, self.store.lock:
            self.workflow.snapshot()  # Same compatibility/history guard as dispatch.
            path, value, local = self.source()
            if digest(value) != body['revision']:
                raise Conflict('Configuration changed. Reload settings and review your draft before saving again.')
            updated = copy.deepcopy(value)
            updated.setdefault('workflow', {})['max_workers'] = limit
            if local:
                # One atomic durable override; a crash before the in-memory update
                # is recovered by normal supervisor startup. Preserve all fields.
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic(path, encode(updated))
                path.chmod(0o600)
            else:
                self.store.transaction({'@config': updated}, 'Update worker capacity')
            self.workflow.options['max_workers'] = limit
            return self.workflow.status()
