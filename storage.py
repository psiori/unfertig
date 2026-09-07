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
import time
from functools import wraps
from datetime import datetime, timezone
from versions import FORMAT_VERSION, PROTOCOL_VERSION, inspect, migrate, parse, VersionError


class Conflict(Exception):
    pass


class OperationLock:
    """Reentrant thread/process lock; the lifetime lease excludes older writers."""
    def __init__(self, root):
        self.root = root
        self.thread = threading.RLock()
        self.depth = 0
        self.file = None

    def __enter__(self):
        self.thread.acquire()
        try:
            if self.depth == 0:
                self.root.mkdir(parents=True, exist_ok=True)
                self.file = (self.root / '.operation.lock').open('a+b')
                deadline = time.monotonic() + 3
                while True:
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            self.file.seek(0)
                            if not self.file.read(1):
                                self.file.write(b'0'); self.file.flush()
                            self.file.seek(0)
                            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise Conflict('Board operation busy; retain the request/draft and retry.')
                        time.sleep(.02)
            self.depth += 1
            return self
        except BaseException:
            if self.file:
                self.file.close(); self.file = None
            self.thread.release()
            raise

    def __exit__(self, *_):
        self.depth -= 1
        if self.depth == 0:
            self.file.close(); self.file = None
        self.thread.release()


def coordinated(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


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
    def __init__(self, path, validator, git=True, config=None):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.validator = validator
        self.git = git
        self.lock = OperationLock(self.root)
        self.journal = self.root / '.transaction.json'
        self.pending = self.root / '.history-pending.json'
        self.receipts = self.root / '.receipts'
        self.lease = None
        self.service_lease = None
        self.history_error = ''
        self.context = {}
        self.config = Path(config).resolve() if config else None
        if self.git and self.config:
            from configuration import git_root
            if git_root(self.config) != git_root(self.root):
                raise ValueError('Configuration and board must have the same Git owner.')
        self.read_only = False
        self.version_warnings = []

    def document(self, path):
        raw = path.read_bytes()
        if len(raw) > 5_000_000:
            raise ValueError(f'Data file exceeds 5 MB: {path}')
        value = json.loads(raw)
        inspect(value, str(path))
        return value

    def target(self, relative):
        if relative == '@config' and self.config:
            return self.config
        if relative == self.path.name or relative == 'data.v1-backup.json' or re.fullmatch(r'todos/(?:[A-Z][A-Z0-9]{0,11}_)?T\d{4,}\.json|\.receipts/[a-zA-Z0-9_-]{16,100}\.json', relative):
            path = self.root / relative
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError('Data paths cannot be symbolic links.')
            return path
        raise ValueError('Invalid transaction path.')

    def preflight(self):
        """Inspect every active format before any migration, recovery or Git write."""
        self.read_only = False
        self.version_warnings = []
        paths = [self.path, *sorted((self.root / 'todos').glob('*.json')),
                 self.journal, self.pending, *sorted(self.receipts.glob('*.json'))]
        if self.config:
            paths.append(self.config)
        for path in paths:
            if not path.exists():
                continue
            if path.is_symlink():
                raise ValueError(f'Data paths cannot be symbolic links: {path}')
            value = self.document(path)
            values = [(value, str(path))]
            if path == self.path and isinstance(value.get('todos'), list):
                values.extend((todo, f"{path}: {todo.get('id', 'todo')}") for todo in value['todos'])
            if path == self.journal:
                for relative, record in value['files'].items():
                    self.target(relative)
                    values.append((record, relative))
                if value.get('receipt'):
                    self.target('.receipts/' + value['receipt']['request_id'] + '.json')
                    values.append((value['receipt'], 'transaction receipt'))
            if path in (self.journal, self.pending):
                for relative in value['paths']:
                    self.target(relative)
            for record, label in values:
                state, warning = inspect(record, label)
                self.read_only |= state == 'read_only'
                if warning:
                    self.version_warnings.append(warning)
        return not self.read_only

    def validate_recovery(self, journal):
        files = journal['files']
        header = files.get(self.path.name)
        if header is None:
            header = self.document(self.path)
        if header.get('schema_version') != 2 or 'todos' in header:
            raise ValueError('Recovery needs a complete schema-2 board header.')
        data = migrate(header, 'board')
        data['schema_version'] = 1
        records = {f'todos/{p.name}': self.document(p) for p in (self.root / 'todos').glob('*.json')}
        records.update({k: v for k, v in files.items() if k.startswith('todos/')})
        data['todos'] = []
        for key, record in records.items():
            if Path(key).stem != record.get('id'):
                raise ValueError('Recovery todo ID and filename disagree.')
            data['todos'].append(migrate(record, 'todo'))
        self.validator(data)
        if len(encode(data)) > 5_000_000:
            raise ValueError('Recovered board exceeds 5 MB.')

    def require_writable(self):
        self.preflight()
        if self.read_only:
            raise Conflict('Newer minor data format: read-only until Unfertig is updated. ' + ' '.join(self.version_warnings))

    def migrate_active(self):
        files = {}
        for path, kind, key in [(self.path, 'board', self.path.name),
                                *[(p, 'todo', f'todos/{p.name}') for p in sorted((self.root / 'todos').glob('*.json'))],
                                *[(p, 'receipt', f'.receipts/{p.name}') for p in sorted(self.receipts.glob('*.json'))],
                                *([(self.config, 'config', '@config')] if self.config else [])]:
            if not path.exists():
                continue
            old = self.document(path)
            new = migrate(old, kind, str(path))
            if old != new:
                files[key] = new
        if files:
            # Validate the fully normalized board before journaling any write.
            self.read()
            self.transaction(files, f'Migrate active JSON formats to {FORMAT_VERSION}')

    def acquire(self, *, cooperative=False, service=False):
        """Compatibility lease; shared clients additionally serialize operations."""
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
                fcntl.flock(self.lease, (fcntl.LOCK_SH if cooperative else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                if service:
                    self.service_lease = (self.root / '.service.lock').open('a+b')
                    fcntl.flock(self.service_lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.close()
            raise ValueError('Another board server or offline writer owns this data directory.') from None

    def close(self):
        if self.service_lease:
            self.service_lease.close()
            self.service_lease = None
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
            files = {self.path.name: {'schema_version': 2, 'ideas': []}}
            if self.context.get('mode') != 'aggregation':
                files['todos/T0001.json'] = todo
            self.transaction(files, 'Initialize board')

    def initialize(self):
        with self.lock:
            self.preflight()
            if self.read_only:
                if self.journal.exists() or self.pending.exists():
                    raise VersionError('Newer minor format with pending recovery/history. Update Unfertig before recovery; files were not changed.')
                self.read()
                return
            self.recover()
            if self.pending.exists() and not self.commit_pending():
                raise Conflict('Finish pending Git history before data-format migration.')
            raw = self.path.read_bytes()
            data = json.loads(raw)
            if data.get('schema_version') == 1:
                normalized = copy.deepcopy(data)
                normalized['todos'] = [migrate(t, 'todo') for t in data['todos']]
                self.validator(normalized)
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
            self.migrate_active()
            self.read()
            if self.pending.exists():
                self.commit_pending()

    def read(self):
        with self.lock:
            self.preflight()
            if self.journal.exists():
                self.recover()
            header = migrate(self.document(self.path), 'board', str(self.path))
            if header.get('schema_version') != 2 or 'todos' in header:
                raise ValueError('Expected migrated schema 2 ideas file. Restart the new server to migrate.')
            data = copy.deepcopy(header)
            data['schema_version'] = 1  # Reuse the record schema; storage layout is version 2.
            data['todos'] = []
            for path in sorted((self.root / 'todos').glob('*.json'), key=lambda p: (len(p.stem), p.stem)):
                todo = migrate(self.document(path), 'todo', str(path))
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
                        protocol_version=PROTOCOL_VERSION,
                        compatibility={'format_version': FORMAT_VERSION, 'read_only': self.read_only,
                                       'warnings': self.version_warnings},
                        revisions={kind: {r['id']: digest(r) for r in data[kind]} for kind in ('ideas', 'todos')},
                        history={'pending': self.pending.exists(), 'error': self.history_error, 'enabled': self.git})

    @coordinated
    def transaction(self, files, message, receipt=None, extra_paths=()):
        self.require_writable()
        if self.pending.exists() and not self.commit_pending():
            raise Conflict('Previous edit is saved but its Git commit is pending. Retry history before another edit.')
        files = {key: migrate(value, 'config' if key == '@config' else 'todo' if key.startswith('todos/') else 'receipt' if key.startswith('.receipts/') else 'board', key) for key, value in files.items()}
        for key, value in files.items():
            self.target(key)
            if inspect(value, key)[0] == 'read_only':
                raise VersionError('Cannot write a newer minor data format.')
        receipt = migrate(receipt, 'receipt') if receipt else None
        journal = dict(format_version=FORMAT_VERSION, files=files, message=message, receipt=receipt,
                       paths=[key for key in files if not key.startswith('.receipts/')] + list(extra_paths))
        atomic(self.journal, encode(journal))
        self.recover()
        self.commit_pending()

    @coordinated
    def recover(self):
        if not self.journal.exists():
            return
        self.require_writable()
        journal = migrate(self.document(self.journal), 'journal')
        self.validate_recovery(journal)
        # Only server-generated, local transaction files are accepted.
        for relative, value in journal['files'].items():
            target = self.target(relative)
            atomic(target, encode(value))
        if self.git and journal['paths']:
            atomic(self.pending, encode(dict(format_version=FORMAT_VERSION, paths=journal['paths'], message=journal['message'])))
        if journal['receipt']:
            receipt = journal['receipt']
            atomic(self.target('.receipts/' + receipt['request_id'] + '.json'), encode(migrate(receipt, 'receipt')))
        self.journal.unlink()

    @coordinated
    def commit_pending(self):
        if not self.pending.exists():
            return True
        self.require_writable()
        try:
            entry = migrate(self.document(self.pending), 'history')
            def git(*args):
                return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                                      capture_output=True, text=True, timeout=30).stdout.strip()
            root = Path(git('rev-parse', '--show-toplevel')).resolve()
            prefix = self.root.relative_to(root)
            paths = [str(self.target(p)) for p in entry['paths']]
            if any(not Path(p).is_relative_to(root) for p in paths):
                raise ValueError('Configuration and board must have the same Git owner for automatic migration.')
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

    def mutate(self, body, *, routing=False):
        with self.lock:
            self.require_writable()
            if 'protocol_version' in body:
                if parse(body['protocol_version'])[0] != parse(PROTOCOL_VERSION)[0]:
                    raise VersionError('Unsupported request protocol major. Update the client and Unfertig.')
                state, _ = inspect(body, 'request', PROTOCOL_VERSION, 'protocol_version')
                if state == 'read_only':
                    raise VersionError('Newer request protocol; update Unfertig before writing.')
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
                if kind == 'todos' and self.context.get('mode') == 'aggregation':
                    raise ValueError('The aggregator owns no todos; use /api/routes.')
                record = copy.deepcopy(change.get('record'))
                if not isinstance(record, dict):
                    raise ValueError('record must be an object.')
                ident = change.get('id')
                if not routing and ('routing' in record or (ident and any(r['id'] == ident and 'routing' in r for r in data[kind]))):
                    old_route = next((r.get('routing') for r in data[kind] if r['id'] == ident), None)
                    if record.get('routing', old_route) != old_route:
                        raise ValueError('Routing state is managed by /api/routes.')
                if ident is None:
                    if kind == 'todos':
                        record = migrate(record, 'todo')
                    prefix = 'I' if kind == 'ideas' else 'T'
                    initials = body.get('initials', '')
                    if not isinstance(initials, str) or (initials and not re.fullmatch(r'[A-Z][A-Z0-9]{0,11}', initials)):
                        raise ValueError('Initials must be empty or 1–12 uppercase letters/digits, starting with a letter.')
                    prefix = (initials + '_' if initials else '') + prefix
                    ident = prefix + str(max((int(re.search(r'\d+$', r['id']).group()) for r in data[kind]), default=0) + 1).zfill(4)
                    record['id'] = ident
                    if kind == 'ideas':
                        from processing import system_id
                        record.pop('captured_system', None)
                        if system_id():
                            record['captured_system'] = system_id()
                    if self.context.get('project_id'):
                        record['project_id'] = self.context['project_id']
                    if kind == 'ideas' and record.get('selected_project') and record['selected_project'] not in {s['project_id'] for s in self.context.get('sources', [])}:
                        raise ValueError('Unknown selected project.')
                    if kind == 'todos':
                        foreign = record.get('source_refs', [])
                        if not isinstance(foreign, list):
                            raise ValueError('source_refs must be a list.')
                        for ref in foreign:
                            if not isinstance(ref, dict) or not isinstance(ref.get('project_id'), str) or not isinstance(ref.get('idea'), dict):
                                raise ValueError('Foreign source needs project_id and immutable original idea.')
                            self.validator(dict(schema_version=1, ideas=[ref['idea']], todos=[]))
                            if any(any(r['project_id'] == ref['project_id'] and r['idea']['id'] == ref['idea']['id'] for r in t.get('source_refs', [])) for t in data['todos']):
                                raise Conflict('Foreign source already routed. Recover the original stable request.')
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
                    # Older clients may omit extensions; omission never deletes them.
                    record = {**copy.deepcopy(old), **record}
                    for field in ('project_id', 'source_refs'):
                        if field in old and record.get(field) != old[field]:
                            raise ValueError(f'Original {field} must be preserved.')
                    if kind == 'ideas' and record.get('selected_project', '') != old.get('selected_project', ''):
                        if old.get('routing', {}).get('request'):
                            raise Conflict('Routing already claimed; project changes cannot move a todo. Recover the route first.')
                        if record.get('selected_project') and record['selected_project'] not in {s['project_id'] for s in self.context.get('sources', [])}:
                            raise ValueError('Unknown selected project.')
                        record.pop('routing', None)
                    if kind == 'todos':
                        if parse(record.get('format_version', FORMAT_VERSION)) < parse(old.get('format_version', FORMAT_VERSION)):
                            raise VersionError('Cannot downgrade a record format_version.')
                        record = migrate(record, 'todo')
                        if inspect(record)[0] == 'read_only':
                            raise VersionError('Cannot write a newer minor data format.')
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
            receipt = dict(format_version=FORMAT_VERSION, request_id=request_id, fingerprint=fingerprint, assigned=assigned)
            actor = body.get('actor', 'editor')
            if not isinstance(actor, str) or len(actor) > 200:
                raise ValueError('Invalid actor.')
            message = 'Save ' + ', '.join(changed_ids) + ' by ' + ' '.join(actor.split())
            if files:
                self.transaction(files, message, receipt)
            else:
                atomic(receipt_path, encode(receipt))
            return dict(self.snapshot(), assigned=assigned)
