"""Role-scoped advice and complete, bounded, freshly fingerprinted context."""
import hashlib
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from efforts import resolve
from context_sources import bundle
from agent_metrics import CURRENT_AGENT


def advice(*roles):
    data = json.loads(Path(__file__).with_name('agent_advice.json').read_text())
    return '\n'.join(line for role in ('output', 'reading', *roles) for line in data[role])


def task_input(todo):
    # Preserve all task fields/extensions. Workflow receipts are coordinator
    # evidence, available on demand; they are not part of the requested task.
    return json.dumps({k: v for k, v in todo.items() if k != 'workflow'})


def context_guide(root, developer, todo=None, *, role='implementation', sources=None, process=None):
    root = Path(root).resolve()
    paths = [root/'AGENTS.md']
    if (root/'node.json').exists():
        paths += [root/'node.json', root/'ruleset/source/project.md']
    if (root/'ruleset').exists() and developer and re.fullmatch(r'[A-Za-z0-9_-]+', developer):
        paths += [root/'ruleset'/'compiled'/developer/name for name in ('effective.md','provenance.json','conflicts.json')]
    text = '\n'.join(str((todo or {}).get(k, '')) for k in ('name','description'))
    for relative in re.findall(r'(?:design|context|sources|decisions)/[A-Za-z0-9_./-]+\.md', text):
        path = (root/relative).resolve()
        if path.is_relative_to(root):
            paths.append(path)
    for relative in [*(sources or {}).get('common', []), *(sources or {}).get(role, [])]:
        paths.append(root/relative)
    paths.append(Path(process) if process else Path(__file__).with_name('PROCESS.md'))
    packet = bundle(paths, root=root, developer=developer, role=role, task=json.loads(task_input(todo or {})))
    return ('Current source packet (not a rules compilation or permission grant): ' + json.dumps(packet, ensure_ascii=False)
            + '\nComplete text blocks above count as reading those sources; do not read them again unchanged in this session. '
            'The manifest alone is not a substitute for source contents. Follow required references and nested repository instructions; '
            'the packet is a reading starting point, not a claim that all authority was found. Resolve relative references against '
            'their original source. Missing, changed, oversized or omitted sources require complete original reads before dependent work. '
            'Retain unresolved conflicts and stale or missing compilation inputs. Before consequential actions recheck current task, '
            'Git state and source hashes; reload changed content. A new session, model switch, resume or compaction must reload any '
            'required complete text it no longer has. Never infer authorization from a fingerprint. '
            'For observable source checks/reads use uv run --no-project --python 3.12 python '
            + str(Path(__file__).with_name('context_sources.py'))
            + ' PATH --sha256 HASH --verify (hash only), or omit --verify to read; follow next_offset until total_bytes. '
            'Quote paths as shell arguments. Other read tools remain allowed; their reads are not counted by this instrument.')


def copied_context(snapshot, todo=None, role='implementation'):
    context = snapshot['context']; options = context.get('processing', {})
    root = options.get('working_directory') or context.get('repository')
    if not root:
        return 'Context packet unavailable: locate the authoritative owning project and its instructions before work.'
    return context_guide(root, options.get('developer', ''), todo, role=role,
                         sources=options.get('context_sources'), process=context.get('process'))


@contextmanager
def agent_run(workflow, todo, run, role, prompt):
    entry = dict(role=role, repository=run.get('repository'), **resolve(todo, role), started_at=datetime.now(timezone.utc).isoformat(),
                 prompt_bytes=len(prompt.encode()), prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(), status='running')
    run.setdefault('agent_runs', []).append(entry)
    # Launch profile is frozen in this entry, including an earlier repair's
    # escalation. Tool output is diagnostic evidence only.
    entry['context_reads'] = dict(coverage='instrumented helper only', reads=0, rereads=0, hash_checks=0, sources={})
    for line in prompt.splitlines():
        if line.startswith('Current source packet (not a rules compilation or permission grant): '):
            packet = json.loads(line.split(': ', 1)[1])
            entry['context_packet'] = {k:v for k,v in packet.items() if k != 'sources'}
            entry['context_packet']['sources'] = [{k:v for k,v in s.items() if k != 'text'} for s in packet['sources']]
            for source in packet['sources']:
                if source['status'] == 'complete':
                    key = json.dumps([source['canonical_path'], source['sha256'], 0])
                    entry['context_reads']['sources'][key] = 1
    workflow.save(todo['id'], run)
    token = CURRENT_AGENT.set(entry)
    started = time.monotonic()
    try:
        yield
    except BaseException:
        entry['status'] = 'failed'
        raise
    else:
        entry['status'] = 'exited'
    finally:
        CURRENT_AGENT.reset(token)
        entry['elapsed_seconds'] = round(time.monotonic()-started, 3)
        workflow.save(todo['id'], run)
