"""Filesystem adapter for the same BoardStore contract used by HTTP.

No source service launch, implicit discovery, migration, or second data store.
"""
import hashlib
import os
from pathlib import Path

from configuration import resolve, board_context, git_root
from storage import BoardStore
from versions import parse, FORMAT_VERSION


def preflight_context(context):
    expected = {k: context[k] for k in ('data', 'repository', 'process', 'project_id')}
    process = Path(context['process'])
    if process.name != 'PROCESS.md' or not process.is_file() or git_root(context['data']) != Path(context['repository']).resolve():
        raise ValueError('Destination PROCESS.md or owning repository is invalid.')
    expected['process_sha256'] = hashlib.sha256(process.read_bytes()).hexdigest()
    return expected


def filesystem(source, validator, body=None, expected=None):
    if os.name == 'nt':
        raise ValueError('Filesystem aggregation is blocked on Windows: cooperative leases require POSIX flock.')
    for field in ('data', 'config', 'app_root'):
        if not source.get(field) or not Path(source[field]).exists():
            raise ValueError(f'Filesystem source {field} is missing or inaccessible.')
    if not Path(source['app_root']).is_dir() or not all(Path(source[k]).is_file() for k in ('data', 'config')):
        raise ValueError('Filesystem sources require data/config files and an app_root directory.')
    configuration = resolve(source['app_root'], config=source['config'])
    if configuration['path'] != Path(source['data']).resolve() or configuration['project_id'] != source['project_id']:
        raise ValueError('Filesystem source config does not match configured data and project identity.')
    if configuration['mode'] == 'aggregation':
        raise ValueError('Nested aggregators are not supported.')
    store = BoardStore(configuration['path'], validator, config=configuration['config'])
    store.acquire(cooperative=True)
    try:
        with store.lock:
            # Re-resolve after locking: a stopped configuration/migration command
            # cannot race the shared lifetime lease. External editors are unsupported.
            current = resolve(source['app_root'], config=source['config'])
            if current != configuration:
                raise ValueError('Source configuration changed during preflight; retry.')
            store.context = board_context(configuration, source['app_root'])
            preflight = preflight_context(store.context)
            if body is not None and preflight != expected:
                raise ValueError('Destination preflight changed; retain claim and review context manually.')
            store.preflight()
            paths = [store.path, store.config, *sorted((store.root / 'todos').glob('*.json')),
                     *sorted(store.receipts.glob('*.json'))]
            paths += [p for p in (store.journal, store.pending) if p.exists()]
            for path in paths:
                document = store.document(path)
                documents = [document]
                if path == store.journal:
                    documents += list(document['files'].values())
                    if document.get('receipt'):
                        documents.append(document['receipt'])
                if any(parse(d.get('format_version', '0.0.0')) < parse(FORMAT_VERSION) for d in documents):
                    raise ValueError('Source requires an explicit stopped migration; discovery does not migrate.')
            if store.document(store.path).get('schema_version') != 2:
                raise ValueError('Source requires an explicit stopped layout migration.')
            # Recovery is roll-forward of an accepted transaction, never migration
            # or restoration of an earlier snapshot. BoardStore validates it first.
            if body is None:
                return store.snapshot()
            return store.mutate(body)
    finally:
        store.close()
