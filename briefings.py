"""Role-scoped shared advice and an uncached index of current context sources."""
import hashlib
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from efforts import resolve


def advice(*roles):
    data = json.loads(Path(__file__).with_name('agent_advice.json').read_text())
    return '\n'.join(line for role in ('output', 'reading', *roles) for line in data[role])


def task_input(todo):
    # Preserve all task fields/extensions. Workflow receipts are coordinator
    # evidence, available on demand; they are not part of the requested task.
    return json.dumps({k: v for k, v in todo.items() if k != 'workflow'})


def context_guide(root, developer, todo=None):
    root = Path(root).resolve()
    paths = [root/'AGENTS.md', root/'node.json']
    if developer and re.fullmatch(r'[A-Za-z0-9_-]+', developer):
        paths += [root/'ruleset'/'compiled'/developer/name for name in ('effective.md','provenance.json','conflicts.json')]
    text = '\n'.join(str((todo or {}).get(k, '')) for k in ('name','description'))
    for relative in re.findall(r'(?:design|context|sources|decisions)/[A-Za-z0-9_./-]+\.md', text):
        path = (root/relative).resolve()
        if path.is_relative_to(root):
            paths.append(path)
    entries = []
    for path in dict.fromkeys(paths):
        try:
            before = path.resolve(); raw = path.read_bytes()
            if path.resolve() != before or path.read_bytes() != raw:
                entries.append(dict(path=str(path), status='changed; re-read'))
            else:
                entries.append(dict(path=str(path), status='present', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        except OSError:
            entries.append(dict(path=str(path), status='missing; resolve if required'))
    return ('Current source index (not a rules compilation or permission grant): ' + json.dumps(entries)
            + '\nRead applicable AGENTS.md instructions, metadata-declared policies and the selected current rules. '
            'Resolve relative policy references against this original context. Follow required sources and task-linked design; '
            'report stale compilations, missing authority and unresolved conflicts honestly. '
            'The index is uncached and not a substitute for source contents. Recheck hashes or read fresh when inputs change. '
            'Conversation history is not supplied.')


@contextmanager
def agent_run(workflow, todo, run, role, prompt):
    entry = dict(role=role, **resolve(todo, role), started_at=datetime.now(timezone.utc).isoformat(),
                 prompt_bytes=len(prompt.encode()), prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(), status='running')
    run.setdefault('agent_runs', []).append(entry)
    workflow.save(todo['id'], run)
    started = time.monotonic()
    try:
        yield
    except BaseException:
        entry['status'] = 'failed'
        raise
    else:
        entry['status'] = 'exited'
    finally:
        entry['elapsed_seconds'] = round(time.monotonic()-started, 3)
        workflow.save(todo['id'], run)
