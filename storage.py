"""Record-scoped board storage, recoverable transactions, and local Git history."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timezone


class Conflict(Exception):
    pass


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.board-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class BoardStore:
    def __init__(self, path, validator, git=True):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.validator = validator
        self.git = git
        self.lock = threading.RLock()
        self.journal = self.root / '.transaction.json'
        self.pending = self.root / '.history-pending.json'
        self.receipts = self.root / '.receipts'
        self.lease = None
        self.history_error = ''
        self.context = {}

    def acquire(self):
        """Held for the server/offline command lifetime, including migration."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.lease = (self.root / '.server.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.lease.seek(0)
                if not self.lease.read(1):
                    self.lease.write(b'0'); self.lease.flush()
                self.lease.seek(0)
                msvcrt.locking(self.lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.close()
            raise ValueError('Another board server or offline writer owns this data directory.') from None

    def close(self):
        if self.lease:
            self.lease.close()
            self.lease = None

    def create_starter(self):
        with self.lock:
            if self.path.exists() or self.journal.exists():
                return
            now = datetime.now(timezone.utc).isoformat()
            todo = dict(id='T0001', source_ideas=[], author='unfertig', date_entered=now,
                        created_by='unfertig', updated_at=now, priority='normal', group='',
                        name='Add your first idea', description='Capture an idea in the scratchpad, then turn it into an actionable todo. Close this onboarding task when you are ready.',
                        tags=[], status='open', closed_by='', date_closed='', pr_url='', commit_url='', commit_hash='')
            self.transaction({self.path.name: {'schema_version': 2, 'ideas': []},
                              'todos/T0001.json': todo}, 'Initialize board')

    def initialize(self):
        with self.lock:
            self.recover()
            raw = self.path.read_bytes()
            data = json.loads(raw)
            if data.get('schema_version') == 1:
                self.validator(data)
                backup = self.root / 'data.v1-backup.json'
                if backup.exists() and backup.read_bytes() != raw:
                    raise ValueError('Migration backup differs; preserve and reconcile it before migration.')
                atomic(backup, raw)
                files = {}
                expected = {t['id'] + '.json' for t in data['todos']}
                if any(p.name not in expected for p in (self.root / 'todos').glob('*.json')):
                    raise Conflict('Unexpected todo files exist before migration; reconcile them first.')
                for todo in data['todos']:
                    relative = f"todos/{todo['id']}.json"
                    target = self.root / relative
                    if target.exists() and json.loads(target.read_bytes()) != todo:
                        raise Conflict(f'Migration would overwrite {relative}. Reconcile first.')
                    files[relative] = todo
                header = copy.deepcopy(data)
                header.pop('todos')
                header['schema_version'] = 2
                files[self.path.name] = header
                self.transaction(files, 'Migrate board to per-todo files', extra_paths=['data.v1-backup.json'])
            self.read()
            if self.pending.exists():
                self.commit_pending()

    def read(self):
        with self.lock:
            if self.journal.exists():
                self.recover()
            header = json.loads(self.path.read_bytes())
            if header.get('schema_version') != 2 or 'todos' in header:
                raise ValueError('Expected migrated schema 2 ideas file. Restart the new server to migrate.')
            data = copy.deepcopy(header)
            data['schema_version'] = 1  # Reuse the record schema; storage layout is version 2.
            data['todos'] = []
            for path in sorted((self.root / 'todos').glob('*.json'), key=lambda p: (len(p.stem), p.stem)):
                todo = json.loads(path.read_bytes())
                if path.stem != todo.get('id'):
                    raise ValueError(f'ID and filename disagree: {path.name}')
                data['todos'].append(todo)
            self.validator(data)
            if len(encode(data)) > 5_000_000:
                raise ValueError('Board exceeds 5 MB.')
            return data, digest(data)

    def snapshot(self):
        with self.lock:
            data, revision = self.read()
            return dict(api_version=2, data=data, revision=revision, context=self.context,
                        revisions={kind: {r['id']: digest(r) for r in data[kind]} for kind in ('ideas', 'todos')},
                        history={'pending': self.pending.exists(), 'error': self.history_error, 'enabled': self.git})

    def transaction(self, files, message, receipt=None, extra_paths=()):
        if self.pending.exists() and not self.commit_pending():
            raise Conflict('Previous edit is saved but its Git commit is pending. Retry history before another edit.')
        journal = dict(files=files, message=message, receipt=receipt, paths=list(files) + list(extra_paths))
        atomic(self.journal, encode(journal))
        self.recover()
        self.commit_pending()

    def recover(self):
        if not self.journal.exists():
            return
        journal = json.loads(self.journal.read_bytes())
        # Only server-generated, local transaction files are accepted.
        for relative, value in journal['files'].items():
            if relative != self.path.name and not re.fullmatch(r'todos/T\d{4,}\.json', relative):
                raise ValueError('Invalid transaction path.')
            atomic(self.root / relative, encode(value))
        if self.git:
            atomic(self.pending, encode({'paths': journal['paths'], 'message': journal['message']}))
        if journal['receipt']:
            receipt = journal['receipt']
            atomic(self.receipts / (receipt['request_id'] + '.json'), encode(receipt))
        self.journal.unlink()

    def commit_pending(self):
        if not self.pending.exists():
            return True
        try:
            entry = json.loads(self.pending.read_bytes())
            def git(*args):
                return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                                      capture_output=True, text=True, timeout=30).stdout.strip()
            root = Path(git('rev-parse', '--show-toplevel')).resolve()
            prefix = self.root.relative_to(root)
            paths = [str(root / prefix / p) for p in entry['paths']]
            # Never take unrelated staged changes into this commit. Git's index lock
            # coordinates with other Git commands; failures stay pending and visible.
            git('add', '-f', '--', *paths)
            has_head = subprocess.run(['git', '-C', str(self.root), 'rev-parse', '--verify', 'HEAD'], capture_output=True).returncode == 0
            changed = git('diff', *(['HEAD'] if has_head else ['--cached']), '--name-only', '--', *paths)
            if changed:
                git('commit', '--only', '-m', 'todo: ' + entry['message'], '--', *paths)
            self.pending.unlink()
            self.history_error = ''
            return True
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            self.history_error = (getattr(error, 'stderr', '') or str(error)).strip()
            return False

    def mutate(self, body):
        with self.lock:
            request_id = body.get('request_id', '')
            if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', request_id):
                raise ValueError('Supply a stable unique request_id (16–100 letters/digits/_/-); reuse it on uncertain retries.')
            self.recover()
            receipt_path = self.receipts / (request_id + '.json')
            fingerprint = digest(body)
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_bytes())
                if receipt.get('retired'):
                    raise Conflict('This request belongs to the pre-split board. Inspect the migration manifest and current record before issuing a new request.')
                if receipt['fingerprint'] != fingerprint:
                    raise Conflict('request_id was already used for a different edit.')
                return dict(self.snapshot(), assigned=receipt['assigned'])
            previous, _ = self.read()
            data = copy.deepcopy(previous)
            changes = body.get('changes')
            if not isinstance(changes, list) or not changes or len(changes) > 100:
                raise ValueError('Supply 1–100 record changes.')
            assigned, seen, changed_ids = [], set(), []
            for change in changes:
                if not isinstance(change, dict):
                    raise ValueError('Each change must be an object.')
                kind = change.get('collection')
                if kind not in ('ideas', 'todos'):
                    raise ValueError('Unknown collection.')
                record = copy.deepcopy(change.get('record'))
                if not isinstance(record, dict):
                    raise ValueError('record must be an object.')
                ident = change.get('id')
                if ident is None:
                    prefix = 'I' if kind == 'ideas' else 'T'
                    ident = prefix + str(max((int(r['id'][1:]) for r in data[kind]), default=0) + 1).zfill(4)
                    record['id'] = ident
                    if kind == 'todos':
                        linked = [t['id'] for t in data['todos'] if set(t['source_ideas']) & set(record.get('source_ideas', []))]
                        if linked and not change.get('allow_shared_sources', False):
                            raise Conflict('Source idea already processed by ' + ', '.join(linked) + '. Review overlap; explicitly allow shared sources for intentional splits.')
                    data[kind].append(record)
                    assigned.append({'collection': kind, 'id': ident})
                else:
                    old = next((r for r in data[kind] if r['id'] == ident), None)
                    if old is None or change.get('revision') != digest(old):
                        raise Conflict(f'{ident} changed. Keep your draft, reload this record, compare, and reconcile only intended fields.')
                    if record.get('id') != ident:
                        raise ValueError('Record ID cannot change.')
                    # A Save that only refreshes bookkeeping is not a meaningful edit.
                    meaningful = lambda r: {k: v for k, v in r.items() if k != 'updated_at'}
                    if meaningful(record) == meaningful(old):
                        record = copy.deepcopy(old)
                    elif kind == 'todos':
                        record['updated_at'] = datetime.now(timezone.utc).isoformat()
                    data[kind][data[kind].index(old)] = record
                if (kind, ident) in seen:
                    raise ValueError('Duplicate change to the same record.')
                seen.add((kind, ident))
                changed_ids.append(ident)
            self.validator(data, previous)
            if len(encode(data)) > 5_000_000:
                raise ValueError('Board exceeds 5 MB.')
            files = {}
            if data['ideas'] != previous['ideas']:
                header = {k: v for k, v in data.items() if k != 'todos'}
                header['schema_version'] = 2
                files[self.path.name] = header
            old_todos = {t['id']: t for t in previous['todos']}
            for todo in data['todos']:
                if old_todos.get(todo['id']) != todo:
                    files[f"todos/{todo['id']}.json"] = todo
            receipt = dict(request_id=request_id, fingerprint=fingerprint, assigned=assigned)
            actor = body.get('actor', 'editor')
            if not isinstance(actor, str) or len(actor) > 200:
                raise ValueError('Invalid actor.')
            message = 'Save ' + ', '.join(changed_ids) + ' by ' + ' '.join(actor.split())
            if files:
                self.transaction(files, message, receipt)
            else:
                atomic(receipt_path, encode(receipt))
            return dict(self.snapshot(), assigned=assigned)
