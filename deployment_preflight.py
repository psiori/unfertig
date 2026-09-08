"""Read-only managed deployment assessment; candidate code sees disposable state.

This deliberately does not install code, migrate a live board, or control services.
The updater validates supported automatic startup migrations before promotion.
"""
from contextlib import contextmanager
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from storage import BoardStore
from versions import parse


LOCKS = {'.server.lock', '.service.lock', '.operation.lock'}


def files(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Deployment assessment refuses symbolic links in board/configuration.')
        if path.is_file() and path.name not in LOCKS:
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def command(argv, cwd):
    env = {k: v for k, v in os.environ.items()
           if k not in ('VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'UV_PROJECT', 'PYTHONPATH')
           and not k.startswith(('UNFERTIG_', 'UM_'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['GIT_OPTIONAL_LOCKS'] = '0'
    result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env,
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError('Disposable deployment validation failed: ' + (result.stderr + result.stdout)[-4000:])
    return result.stdout


def revision(repository):
    return command(['git', 'rev-parse', 'HEAD'], repository).strip()


def supported_format(repository):
    tree = ast.parse((repository / 'versions.py').read_text())
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FORMAT_VERSION' for t in statement.targets):
            version = ast.literal_eval(statement.value)
            parse(version)
            return version
    raise ValueError('Runtime does not declare a supported storage format.')


@contextmanager
def capture(context):
    """Use the shared operation protocol without recovery, migration or history writes."""
    config = context / 'state/unfertig/config/config.json'
    config_bytes = config.read_bytes()
    value = json.loads(config_bytes)
    data = (config.parent / value['data']).resolve()
    if (data != context / 'state/unfertig/data/data.json'
            or value.get('mode') != 'embedded'
            or (config.parent / value.get('repository', '.')).resolve() != context):
        raise ValueError('Deployment preflight requires the declared wrapper-owned Unfertig board.')
    if not data.is_file():
        raise ValueError('Deployment preflight requires an existing board.')
    store = BoardStore(data, lambda value: None, git=False)
    store.acquire(cooperative=True)
    try:
        with store.lock:
            if store.journal.exists() or store.pending.exists():
                raise ValueError('Finish pending board transactions/history through supported recovery before deployment.')
            board = files(data.parent)
            configs = files(config.parent)
            if configs.get('config.json') != config_bytes:
                raise ValueError('Configuration changed while acquiring preflight locks; retry explicitly.')
        yield board, configs
        with store.lock:
            if files(data.parent) != board or files(config.parent) != configs:
                raise ValueError('Board or configuration changed during preflight; review a fresh assessment.')
    finally:
        store.close()


def preserve(before, after, label, metadata=True):
    """Every existing value except top-level version metadata must survive."""
    if isinstance(before, dict) and isinstance(after, dict):
        for key, value in before.items():
            if metadata and key == 'format_version':
                continue
            if key not in after:
                raise ValueError('Migration changes original content or extensions: ' + label + ':' + key)
            preserve(value, after[key], label + ':' + key, False)
    elif isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            raise ValueError('Migration changes original content: ' + label)
        for index, (old, new) in enumerate(zip(before, after)):
            preserve(old, new, label + ':' + str(index), False)
    elif before != after:
        raise ValueError('Migration changes original content: ' + label)


def preserve_files(before, after):
    """Preserve original content, including the supported monolithic-to-split step."""
    for name, raw in before.items():
        if name not in after:
            raise ValueError('Migration removed recovery evidence: ' + name)
        if raw == after[name]:
            continue
        old, new = json.loads(raw), json.loads(after[name])
        if name == 'data.json' and old.get('schema_version') == 1 and new.get('schema_version') == 2:
            header = dict(old)
            originals = header.pop('todos')
            header.pop('schema_version')
            preserve(header, new, name)
            for todo in originals:
                path = 'todos/' + todo['id'] + '.json'
                if path not in after:
                    raise ValueError('Migration lost original todo: ' + path)
                preserve(todo, json.loads(after[path]), path)
        else:
            preserve(old, new, name)


def assess(candidate, context):
    candidate, context = Path(candidate).resolve(), Path(context).resolve()
    installed = context / 'tools/unfertig'
    candidate_revision, installed_revision = revision(candidate), revision(installed)
    for app in (candidate, installed):
        if command(['git', 'status', '--porcelain'], app).strip():
            raise ValueError('Deployment preflight requires clean candidate and installed checkouts.')
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('Install uv before deployment preflight.')
    with capture(context) as (board, configs), tempfile.TemporaryDirectory(prefix='unfertig-preflight-') as temporary:
        host = Path(temporary)
        data = host / 'state/unfertig/data'
        config_dir = host / 'state/unfertig/config'
        for root, entries in ((data, board), (config_dir, configs)):
            for name, raw in entries.items():
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
        command(['git', 'init', '-q'], host)
        command(['git', 'config', 'user.name', 'Disposable migration check'], host)
        command(['git', 'config', 'user.email', 'check@example.invalid'], host)
        (host / '.gitignore').write_text('.server.lock\n.service.lock\n.operation.lock\n.receipts/\n.transaction.json\n.history-pending.json\n')
        command(['git', 'add', '.'], host)
        command(['git', 'commit', '-qm', 'Disposable migration fixture'], host)
        config = config_dir / 'config.json'
        # Only explicitly supported portable wrapper layouts are copied. Effective
        # local configuration is inspected too, without modifying local overrides.
        selected = [config]
        effective = config_dir / 'machine.local.json'
        if effective.exists():
            selected.append(effective)
        for selected_config in selected:
            value = json.loads(selected_config.read_bytes())
            if ((selected_config.parent / value['data']).resolve() != data / 'data.json'
                    or (selected_config.parent / value.get('repository', '.')).resolve() != host
                    or value.get('mode') != 'embedded'):
                raise ValueError('Configuration uses nonportable paths; explicit migration review required.')

        def snapshot(app, selected_config):
            return json.loads(command([uv, 'run', '--no-project', '--python', '3.12', '--script',
                                       app / 'server.py', '--config', selected_config, '--snapshot', '--no-git'], host))

        current = {json.loads(raw).get('format_version', '0.0.0')
                   for name, raw in board.items()
                   if name == 'data.json' or name.startswith(('todos/', '.receipts/'))}
        current.update(json.loads(path.read_bytes()).get('format_version', '0.0.0') for path in selected)
        current = sorted(current, key=parse)
        before = files(data), files(config_dir)
        snapshots = [snapshot(candidate, path) for path in selected]
        after = files(data), files(config_dir)
        for old_files, new_files in zip(before, after):
            preserve_files(old_files, new_files)
        for path in selected:
            snapshot(candidate, path)
        if (files(data), files(config_dir)) != after:
            raise ValueError('Candidate migration is not byte-idempotent.')
        if any(s['history']['pending'] or s['compatibility']['read_only'] for s in snapshots):
            raise ValueError('Candidate cannot provide writable storage with completed history.')
        target = json.loads((data / 'data.json').read_bytes())['format_version']
        changed = before != after
        older = snapshot(installed, config)
        if (files(data), files(config_dir)) != after:
            raise ValueError('Installed writer changes candidate storage; explicit compatibility review required.')
        if parse(target)[:2] > parse(supported_format(installed))[:2] and not older['compatibility']['read_only']:
            raise ValueError('Installed writer does not enforce read-only protection for the migrated format.')
        if (revision(candidate) != candidate_revision or revision(installed) != installed_revision
                or any(command(['git', 'status', '--porcelain'], app).strip() for app in (candidate, installed))):
            raise ValueError('Candidate or installed revision changed during preflight.')
        fingerprint = review_digest(board, configs)
        return dict(state='migration_required' if changed else 'unchanged_storage',
                    current_formats=current, target_format=target, candidate_commit=candidate_revision,
                    installed_commit=installed_revision, storage_digest=fingerprint,
                    message=('Migration required: ' + ', '.join(current) + ' → ' + target +
                             '. Validated programmatic migration will run automatically on restart.'
                             if changed else 'Disposable validation passed with unchanged storage.'))


def review_digest(board, configs):
    """Local-only approval binding; coordinator progress is not a scope change.

    Never publish this digest: configuration can contain machine-local values.
    Receipts and all original record content remain covered by preservation checks.
    """
    selected = {}
    for prefix, entries in (('board/', board), ('config/', configs)):
        for name, raw in sorted(entries.items()):
            if prefix == 'board/' and (name.startswith('.receipts/') or name.endswith('.backup.json')):
                continue
            if prefix == 'board/' and name.startswith('todos/') and name.endswith('.json'):
                value = json.loads(raw)
                value.pop('workflow', None)
                value.pop('updated_at', None)
                raw = json.dumps(value, sort_keys=True).encode()
            selected[prefix + name] = raw.hex()
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def require_unchanged(candidate, context):
    """Legacy opt-in strict audit; normal update/restart uses assess instead."""
    result = assess(candidate, context)
    if result['state'] != 'unchanged_storage':
        raise ValueError(result['message'])
    return result
