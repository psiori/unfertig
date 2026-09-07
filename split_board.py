"""Offline, restartable relocation of a board. Recovery state stays in the host repo."""
import argparse
import copy
import hashlib
import json
import shutil
from pathlib import Path

from storage import BoardStore, atomic, encode
from server import validate
from versions import FORMAT_VERSION, inspect, migrate


def split(source, app, project, recovery, assignments):
    source, app, project, recovery = map(lambda p: Path(p).resolve(), (source, app, project, recovery))
    if len({source.parent, app, project, recovery}) != 4:
        raise ValueError('Source, destinations and recovery directories must differ.')
    for target in (app, project, recovery):
        if target.is_relative_to(source.parent) and target != app:
            raise ValueError('Recovery and project data must be outside the extracted app.')
    manifest_path = recovery / 'manifest.json'
    store = BoardStore(source, validate)
    store.acquire()
    try:
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_bytes())
            state, _ = inspect(manifest, 'relocation manifest')
            if state == 'read_only':
                raise ValueError('Newer relocation manifest: update Unfertig before resuming.')
            if manifest['source'] != str(source) or manifest['destinations'] != {'app': str(app), 'project': str(project)} or manifest['assignments'] != assignments:
                raise ValueError('Migration parameters differ from recovery manifest.')
            if manifest['complete']:
                # Never overwrite later edits on a repeat invocation.
                return manifest
            manifest = migrate(manifest, 'manifest', 'relocation manifest')
            original = json.loads((recovery / 'snapshot.json').read_bytes())
            if any(inspect(value, 'relocation snapshot')[0] == 'read_only'
                   for value in [original, *original.get('todos', [])]):
                raise ValueError('Newer relocation snapshot: update Unfertig before resuming.')
            original = migrate(original, 'board', 'relocation snapshot')
            original['todos'] = [migrate(t, 'todo') for t in original['todos']]
        else:
            store.require_writable()
            if store.journal.exists() or store.pending.exists():
                raise ValueError('Finish pending source transactions/Git commits before splitting.')
            original, _ = store.read()
            if set(assignments['todos']) != {t['id'] for t in original['todos']} or set(assignments['ideas']) != {i['id'] for i in original['ideas']}:
                raise ValueError('Every original idea and todo needs an explicit destination.')
            if any(v not in ('app', 'project') for group in assignments.values() for v in group.values()):
                raise ValueError('Destinations must be app or project.')
            if app.exists() or project.exists():
                raise ValueError('Destination already exists; do not overwrite a board.')
            recovery.mkdir(parents=True, exist_ok=True)
            backup = recovery / 'original'
            # Copy exact board bytes and runtime recovery state, never into the app repo.
            backup.mkdir(exist_ok=True)
            for name in (source.name, 'todos', 'data.v1-backup.json', '.receipts'):
                path = source.parent / name
                if path.is_dir(): shutil.copytree(path, backup / name, dirs_exist_ok=True)
                elif path.exists(): shutil.copy2(path, backup / name)
            atomic(recovery / 'snapshot.json', encode(original))
            manifest = dict(format_version=FORMAT_VERSION, source=str(source), destinations={'app':str(app), 'project':str(project)}, assignments=assignments,
                            source_digest=hashlib.sha256(encode(original)).hexdigest(), complete=False,
                            records={kind:{r['id']:{'destination':assignments[kind][r['id']], 'sha256':hashlib.sha256(encode(r)).hexdigest()} for r in original[kind]} for kind in ('ideas','todos')})
            atomic(manifest_path, encode(manifest))
        for label, target in (('app',app),('project',project)):
            data = copy.deepcopy(original)
            for kind in ('ideas','todos'):
                data[kind] = [r for r in original[kind] if assignments[kind][r['id']] == label]
            validate(data)  # Shared-source cases require an explicit resolved assignment, never dangling links.
            header = {k:v for k,v in data.items() if k != 'todos'}; header['schema_version']=2
            expected = {'data.json':header, **{f"todos/{t['id']}.json":t for t in data['todos']}}
            for relative, record in expected.items():
                path = target/relative
                if path.exists() and migrate(json.loads(path.read_bytes()), 'todo' if relative.startswith('todos/') else 'board') != record:
                    raise ValueError(f'Destination changed during recovery: {path}')
                atomic(path, encode(record))
            # Retire request IDs without exporting original bodies to another repository.
            for receipt in (recovery/'original'/'.receipts').glob('*.json'):
                atomic(target/'.receipts'/receipt.name, encode({'format_version':FORMAT_VERSION, 'retired':True}))
            atomic(target/'.gitignore', b'.server.lock\n.transaction.json\n.history-pending.json\n.receipts/\n.board-*.tmp\n')
            current,_=BoardStore(target/'data.json', validate, git=False).read()
            if current != data: raise ValueError('Destination validation mismatch.')
        # Retiring the source makes legacy clients fail closed before old files are removed.
        atomic(source, encode({'format_version':FORMAT_VERSION,'schema_version':0,'retired':True,'manifest':str(manifest_path)}))
        manifest['complete']=True
        atomic(manifest_path, encode(manifest))
        return manifest
    finally:
        store.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','app','project','recovery','assignments'): parser.add_argument('--'+name, required=True, type=Path)
    args=parser.parse_args()
    print(json.dumps(split(args.source,args.app,args.project,args.recovery,json.loads(args.assignments.read_text())),indent=2))
