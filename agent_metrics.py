"""Coordinator observations for a small profile pilot; never execution authority."""
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

CURRENT_AGENT = ContextVar('unfertig_agent_observation', default=None)


def now():
    return datetime.now(timezone.utc).isoformat()


def observe(line):
    """Count only instrumented source reads visible in completed Codex tool events."""
    entry = CURRENT_AGENT.get()
    if entry is None:
        return
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return
    if not isinstance(event, dict):
        return
    item = event.get('item', {})
    if not isinstance(item, dict):
        return
    if event.get('type') != 'item.completed' or item.get('type') != 'command_execution':
        return
    output_text = item.get('aggregated_output', '')
    if not isinstance(output_text, str):
        return
    for output in output_text.splitlines():
        if not output.startswith('UNFERTIG_CONTEXT_READ '):
            continue
        try:
            read = json.loads(output.split(' ', 1)[1])
            if (read['operation'] not in ('read', 'verify') or not isinstance(read['path'], str)
                    or len(read['path'])>4096 or not isinstance(read['sha256'], str) or len(read['sha256'])>64):
                continue
            key = json.dumps([read['path'], read['sha256'], read.get('offset', 0)])
            metric = entry.setdefault('context_reads', dict(coverage='instrumented helper only', reads=0, rereads=0, hash_checks=0, sources={}))
            if read['operation'] == 'verify':
                metric['hash_checks'] += 1
            else:
                metric['reads'] += 1
                metric['rereads'] += int(key in metric['sources'])
                if key in metric['sources'] or len(metric['sources'])<256:
                    metric['sources'][key] = metric['sources'].get(key, 0)+1
                else:
                    metric['coverage'] = 'instrumented helper only; distinct-source tracking limit reached'
        except (ValueError, KeyError, TypeError):
            continue


def display_line(line):
    """Keep provider events useful in the live tail without copying huge JSON."""
    try:
        event=json.loads(line)
        item=event.get('item', {})
        if item.get('type')=='command_execution':
            return (str(item.get('command',''))[:300]+'\n'+str(item.get('aggregated_output',''))[-2000:]).strip()
        return str(item.get('text') or event.get('message') or event.get('type') or '')[-2000:]
    except (ValueError, TypeError, AttributeError):
        return line.rstrip()


def repair_needed(run, role='implementation'):
    """Called only for a verified failed check or a classified incomplete result."""
    for entry in reversed(run.get('agent_runs', [])):
        if entry['role'] == role:
            entry['outcome'] = 'repair_needed'
            return


def checked(workflow, run, ident, *, cwd=None, stage='implementation'):
    owner = getattr(workflow, 'metrics_run', run)
    cwd = str(cwd or run['worktree'])
    argv = workflow.argv('test', dict(run, worktree=cwd))
    entry = dict(stage=stage, repository=run.get('repository'), started_at=now(), status='running',
                 command_sha256=hashlib.sha256(json.dumps(argv).encode()).hexdigest())
    owner.setdefault('check_runs', []).append(entry)
    started = time.monotonic()
    try:
        workflow.command(argv, cwd, ident, purpose='verification')
    except BaseException as error:
        entry['status'] = 'failed'
        # A coordinator test failure is repair evidence. It does not launch an
        # agent, waive approval, or change an explicit manual selection.
        infrastructure = ('unavailable', 'not found', 'permission', 'approval', 'authentication', 'rate limit', 'timed out', 'stopping')
        if isinstance(error, ValueError) and not any(word in str(error).lower() for word in infrastructure):
            repair_needed(run if stage == 'integration' else owner, 'integration' if stage == 'integration' else 'implementation')
        raise
    else:
        entry['status'] = 'passed'
    finally:
        entry['elapsed_seconds'] = round(time.monotonic()-started, 3)


def accepted(run):
    """Only called after report, exact HEAD, configured checks and PR validation."""
    if run.get('accepted_result'):
        return
    entries = run.get('agent_runs', [])
    if not entries:
        return  # Do not invent timing for historical uninstrumented runs.
    stamp = now()
    seconds = (datetime.fromisoformat(stamp)-datetime.fromisoformat(entries[0]['started_at'])).total_seconds()
    run['accepted_result'] = dict(at=stamp, elapsed_seconds=round(max(0, seconds), 3),
                                  commit=run.get('commit'), repository_heads=run.get('repository_heads', {}))
    for entry in reversed(entries):
        if entry['role'] == 'implementation':
            entry['outcome'] = 'accepted'
            break


def summary(todo):
    run = todo.get('workflow', {})
    entries = run.get('agent_runs', [])
    # Mapped integrations run through child facades and retain per-repo evidence.
    entries = [*entries, *(entry for repo in run.get('repositories', []) for entry in repo.get('agent_runs', []))]
    checks = run.get('check_runs', [])
    observed = [e['context_reads'] for e in entries if 'context_reads' in e]
    groups = {(e.get('repository'), e['role']) for e in entries}
    return dict(todo_id=todo.get('id'), accepted_seconds=run.get('accepted_result', {}).get('elapsed_seconds'),
                profiles=[dict(profile=e['profile'], role=e['role'], model=e['model'], reasoning_effort=e['reasoning_effort'],
                               elapsed_seconds=e.get('elapsed_seconds'), outcome=e.get('outcome', e['status'])) for e in entries],
                retries=sum(max(0, sum((e.get('repository'), e['role'])==group for e in entries)-1) for group in groups),
                test_seconds=round(sum(c.get('elapsed_seconds', 0) for c in checks), 3) if checks else None,
                failed_checks=sum(c['status']=='failed' for c in checks),
                instrumented_reads=sum(m['reads'] for m in observed) if observed else None,
                instrumented_rereads=sum(m['rereads'] for m in observed) if observed else None,
                read_coverage='instrumented helper only; other tool reads are unobserved')


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Summarize a saved --snapshot export or individual todo files without modifying board state.')
    parser.add_argument('files', nargs='+', type=Path)
    args=parser.parse_args()
    for path in args.files:
        value=json.loads(path.read_text())
        todos=value.get('data', value).get('todos', [value])
        for todo in todos:
            print(json.dumps(summary(todo)))
