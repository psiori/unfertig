"""Owner-local batch review, using the ordinary durable integration action receipts.

No batch claim or second queue: each accepted action is independently recoverable.
"""
import copy
from pathlib import Path
import uuid

from processing import system_id
from storage import Conflict, digest
from workflow import scope_digest

FINISHED = {'ready', 'tested', 'test_failed'}


def available(w):
    if not w.options['enabled'] or w.stopping:
        raise ValueError('Implementation workflow is disabled or stopping.')
    return w.snapshot()


def candidate(w, todo, snap):
    run = todo.get('workflow', {})
    if todo['status'] == 'closed' or run.get('external_completions'):
        raise Conflict('Closed or superseded attempt; no new integration is authorized.')
    if run.get('phase') not in FINISHED:
        raise Conflict('Not a finished implementation awaiting integration; use its individual progress/recovery controls.')
    if not system_id() or run.get('system') != system_id():
        raise Conflict('Run belongs to another system; use its owning instance.')
    if run.get('scope') != scope_digest(todo):
        raise Conflict('Task scope changed since implementation.')
    if run.get('repository') != w.options['repository']:
        raise Conflict('Repository configuration changed.')
    if todo['id'] in w.active_workers():
        raise Conflict('A worker is still active.')
    w.guard_process(run)
    expected = Path(w.options['repository']) / '.worktrees' / 'unfertig' / run['run_id']
    if Path(run['worktree']).resolve() != expected or not run['branch'].startswith('codex/'):
        raise Conflict('Run paths do not match this repository.')
    if w.git('rev-parse', run['branch']) != run.get('commit'):
        raise Conflict('Branch changed; review its current commit.')
    if w.git('status', '--porcelain', cwd=run['worktree']):
        raise Conflict('Branch worktree has uncommitted changes.')
    if run.get('repositories'):
        from context_workflow import guard, facade
        guard(w, run)
        repos = [r for r in run['repositories'] if r['available']]
        if run.get('repository_heads') != {r['id']: r.get('commit') for r in repos}:
            raise Conflict('Repository result heads are incomplete.')
    else:
        repos = [dict(run, id='project', changed=True)]
    heads = []
    for repo in repos:
        proxy = facade(w, run, repo, todo['id']) if run.get('repositories') else w
        if (proxy.git('rev-parse', repo['branch']) != repo.get('commit')
                or proxy.git('rev-parse', 'HEAD', cwd=repo['worktree']) != repo.get('commit')
                or proxy.git('status', '--porcelain', cwd=repo['worktree'])):
            raise Conflict('Repository branch changed or has uncommitted changes: '+repo['id'])
        if run.get('repositories') and repo.get('changed') and repo.get('verification') != dict(status='passed', commit=repo.get('commit')):
            # Permit future verification extensions, but never infer passed checks.
            verification = repo.get('verification', {})
            if verification.get('status') != 'passed' or verification.get('commit') != repo.get('commit'):
                raise Conflict('Repository implementation checks are incomplete: '+repo['id'])
        if repo.get('changed'):
            if not repo.get('pr_url'):
                raise Conflict('No verified PR for '+repo['id'])
            pr = proxy.pr_state(repo)
            if pr['state'] != 'OPEN' or pr.get('isDraft') or pr['headRefOid'] != repo.get('commit'):
                raise Conflict('PR is not open and ready at the saved implementation commit: '+repo['id']+'. Review/reconcile it individually.')
        heads.append(dict(id=repo['id'], repository=repo['repository'], branch=repo['branch'],
                          commit=repo['commit'], pr_url=repo.get('pr_url', ''), changed=repo.get('changed', True)))
    if not any(r['changed'] for r in heads):
        raise Conflict('No changed repository PRs to integrate.')
    action = dict(id=todo['id'], action='merge', revision=snap['revisions']['todos'][todo['id']],
                  commit=run['commit'], request_id=uuid.uuid4().hex)
    if run.get('repositories'):
        action['repositories'] = copy.deepcopy(run['repository_heads'])
    return dict(id=todo['id'], name=todo['name'], repositories=heads, action=action)


def review(w):
    with w.lock:
        snap = available(w)
        entries, excluded = [], []
        for todo in snap['data']['todos']:
            if not todo.get('workflow'):
                continue
            try:
                entries.append(candidate(w, todo, snap))
            except (Conflict, ValueError, OSError) as error:
                excluded.append(dict(id=todo['id'], reason=str(error)))
        return dict(repository=w.options['repository'], board=snap['context']['data'], entries=entries, excluded=excluded,
                    queue_blocked_by=w.status()['queue_blocked_by'])


def enqueue(w, body):
    entries = body.get('entries')
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise ValueError('Expected the reviewed merge actions as entries.')
    ids = [e.get('id') for e in entries]
    if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Each todo may occur only once in a batch.')
    if any(e.get('action') != 'merge' or not isinstance(e.get('request_id'), str) or not 1 <= len(e['request_id']) <= 128 for e in entries):
        raise ValueError('Each entry requires an exact merge action and stable request ID.')
    outcomes = []
    with w.lock:
        snapshot = available(w)
        if body.get('repository') != w.options['repository'] or body.get('board') != snapshot['context']['data']:
            raise Conflict('Batch belongs to a different owner repository.')
        try:
            for entry in entries:
                try:
                    snap = w.snapshot()
                    todo = next((t for t in snap['data']['todos'] if t['id'] == entry['id']), None)
                    if todo is None:
                        raise Conflict('Todo no longer exists.')
                    receipt = todo.get('workflow', {}).get('action_requests', {}).get(entry['request_id'])
                    if receipt is None:
                        current = candidate(w, todo, snap)['action']
                        if any(entry.get(k) != current.get(k) for k in ('revision', 'commit', 'repositories')):
                            raise Conflict('Todo or selected commits changed after review. Review a new batch.')
                    elif receipt != digest(entry):
                        raise Conflict('Workflow request ID reused for different input.')
                    # Includes path, scope, compatibility and lifecycle guards;
                    # saved receipts also recover delivery interrupted by restart.
                    w.start(entry, dispatch=False)
                    outcomes.append(dict(id=entry['id'], status='accepted', message='Integration action accepted; see the pipeline for progress.'))
                except (Conflict, ValueError) as error:
                    outcomes.append(dict(id=entry['id'], status='rejected', message=str(error)))
                except OSError as error:
                    outcomes.append(dict(id=entry['id'], status='unknown', message=str(error)+' Retry the identical batch to check its receipt.'))
        finally:
            # Save the entire reachable batch before launching integration.
            # The existing dispatcher alone owns conflict pauses and recovery.
            w.dispatch()
        return dict(outcomes=outcomes, workflow=w.status())
