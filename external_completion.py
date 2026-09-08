"""Read-only evidence gathering and owner-authorized reconciliation of old attempts."""
import copy
from http.client import HTTPException
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from urllib.request import urlopen

from processing import system_id
from storage import Conflict, digest

SHA = r'[a-f0-9]{40}(?:[a-f0-9]{24})?'
PR = r'https://github\.com/([\w.-]+/[\w.-]+)/pull/[1-9][0-9]*'


def deployment(workflow, commit, integration):
    result = dict(status='unverified', commit=commit, message='No deployment reference supplied.')
    if not commit:
        return result
    result['message'] = 'No read-only deployment verifier is available for this artifact recipe.'
    if not workflow.managed_unfertig():
        return result
    try:
        if not integration:
            raise ValueError('Integration must be verified before deployment can be verified.')
        workflow.git('merge-base', '--is-ancestor', integration, commit)
        workflow.git('merge-base', '--is-ancestor', commit, 'refs/remotes/origin/' + workflow.options['base_branch'])
        root = Path(workflow.processing['working_directory'])
        runtime = root / 'tools/unfertig'
        for path in ('tools/unfertig',):
            if workflow.git('rev-parse', 'HEAD:' + path, cwd=root) != commit:
                raise ValueError('A committed wrapper pin differs from the supplied deployment.')
        if workflow.git('rev-parse', 'HEAD', cwd=runtime) != commit or workflow.git('status', '--porcelain', cwd=runtime):
            raise ValueError('Installed runtime differs from the supplied deployment or is dirty.')
        # Query this owner service: startup-captured revision is distinct from disk HEAD.
        with urlopen(workflow.url + '/api/state', timeout=5) as response:
            snapshot = json.load(response)
        context = snapshot['context']
        if (context.get('runtime_commit') != commit or context.get('app_root') != str(runtime.resolve())
                or context.get('data') != workflow.store.context.get('data')
                or context.get('repository') != str(root.resolve())):
            raise ValueError('Runtime health does not match the deployment and board owner.')
        if snapshot['compatibility']['read_only'] or snapshot['history']['pending'] or not snapshot['history']['enabled']:
            raise ValueError('Runtime board is not writable with completed history.')
        result.update(status='verified', message='Committed runtime pin, installed runtime, running revision and writable owner board verified.')
    except (OSError, ValueError, KeyError, HTTPException, subprocess.SubprocessError) as error:
        result.update(status='unverified', message=str(error))
    return result


def evidence(workflow, body):
    url = body.get('pr_url', '')
    implementation = body.get('implementation_commit', '')
    integration = body.get('integration_commit', '')
    deployed = body.get('deployment_commit', '')
    for value in (implementation, integration, deployed):
        if not isinstance(value, str) or (value and not re.fullmatch(SHA, value)):
            raise ValueError('Supply full commit hashes or leave missing references empty.')
    if not isinstance(url, str) or (url and not re.fullmatch(PR, url)):
        raise ValueError('Supply a GitHub PR URL or leave it empty.')
    result = dict(pr_url=url, implementation_commit=implementation,
                  integration=dict(status='unverified', commit=integration, message='No PR supplied.'))
    verified = ''
    try:
        if url:
            # Bind a replacement PR to the configured origin, not the failed branch.
            repository = json.loads(workflow.github('repo', 'view', '--json', 'nameWithOwner'))['nameWithOwner']
            if re.fullmatch(PR, url)[1].lower() != repository.lower():
                raise ValueError('Replacement PR belongs to a different repository.')
            pr = json.loads(workflow.github('pr', 'view', url, '--json',
                           'url,state,headRefOid,headRefName,baseRefName,mergeCommit'))
            result['pr'] = pr
            if pr.get('url') != url or pr.get('baseRefName') != workflow.options['base_branch']:
                raise ValueError('PR identity or target branch differs from this repository.')
            if pr.get('state') != 'MERGED':
                raise ValueError('PR is not merged: ' + str(pr.get('state')))
            merged = (pr.get('mergeCommit') or {}).get('oid', '')
            if not re.fullmatch(SHA, merged):
                raise ValueError('GitHub did not provide a merge revision.')
            if not isinstance(pr.get('headRefOid'), str) or not re.fullmatch(SHA, pr['headRefOid']):
                raise ValueError('GitHub did not provide an implementation revision.')
            if integration and integration != merged:
                raise ValueError('Supplied integration revision contradicts the GitHub merge revision.')
            if implementation and implementation != pr.get('headRefOid'):
                raise ValueError('Supplied implementation revision contradicts the PR head.')
            workflow.git('fetch', 'origin', workflow.options['base_branch'])
            workflow.git('merge-base', '--is-ancestor', merged, 'refs/remotes/origin/' + workflow.options['base_branch'])
            # Squash/rebase merge evidence is GitHub's merge revision, never branch ancestry.
            verified = merged
            result['implementation_commit'] = pr['headRefOid']
            result['integration'] = dict(status='verified', commit=merged, message='GitHub merge revision verified on origin/' + workflow.options['base_branch'])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result['integration']['message'] = str(error)
    result['deployment'] = deployment(workflow, deployed, verified)
    return result


def complete(workflow, todo, body):
    old = todo.get('workflow')
    if not old or old.get('system') != system_id() or old.get('repository') != workflow.options['repository']:
        raise Conflict('Reconcile this attempt on its original owner and repository.')
    expected = Path(workflow.options['repository']) / '.worktrees/unfertig' / old['run_id']
    if Path(old['worktree']).resolve() != expected.resolve():
        raise Conflict('Retained run paths differ from the owning repository.')
    if old['phase'] not in {'implementation_failed', 'test_failed', 'merge_failed', 'push_failed',
                            'restart_failed', 'resolution_blocked', 'implementing', 'testing', 'merging', 'ready', 'tested'}:
        raise Conflict('This stage cannot be superseded; inspect its recovery evidence.')
    block = workflow.retained_activity(todo)
    if block:
        raise Conflict(block)
    for key in ('actor', 'reason', 'request_id'):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > (4000 if key == 'reason' else 128):
            raise ValueError('External completion requires ' + key + '.')
    observed = evidence(workflow, body)
    # Recheck worker evidence after network I/O. The workflow lock excludes launches;
    # the store revision below excludes concurrent HTTP/filesystem record changes.
    block = workflow.retained_activity(todo)
    if block:
        raise Conflict(block)
    run = copy.deepcopy(old)
    run.setdefault('external_completions', []).append(dict(actor=body['actor'], reason=body['reason'],
        at=datetime.now(timezone.utc).isoformat(), outcome='superseded', evidence=observed))
    run.setdefault('action_requests', {})[body['request_id']] = digest(body)
    workflow.store.mutate(dict(actor=body['actor'], request_id='external-' + digest(body), changes=[dict(
        collection='todos', id=todo['id'], revision=body['revision'], record=dict(todo, workflow=run))]), workflow=True)


def validate(entries):
    if not isinstance(entries, list) or not entries:
        raise ValueError('External completion history must be a nonempty list.')
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('outcome') != 'superseded':
            raise ValueError('External completion must supersede the old attempt.')
        for key in ('actor', 'reason', 'at'):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError('External completion requires ' + key + '.')
        if datetime.fromisoformat(entry['at']).tzinfo is None:
            raise ValueError('External completion timestamp needs a timezone.')
        saved = entry.get('evidence')
        if not isinstance(saved, dict):
            raise ValueError('External completion requires evidence.')
        for key, pattern in (('pr_url', PR), ('implementation_commit', SHA)):
            value = saved.get(key, '')
            if not isinstance(value, str) or (value and not re.fullmatch(pattern, value)):
                raise ValueError('Invalid external completion ' + key + '.')
        for key in ('integration', 'deployment'):
            stage = saved.get(key)
            if not isinstance(stage, dict) or stage.get('status') not in ('verified', 'unverified'):
                raise ValueError('Invalid external completion evidence status.')
            commit = stage.get('commit')
            if not isinstance(commit, str) or (commit and not re.fullmatch(SHA, commit)) or (stage['status'] == 'verified' and not commit):
                raise ValueError('Invalid external completion evidence revision.')
            if not isinstance(stage.get('message'), str):
                raise ValueError('External completion evidence needs an explanation.')
        if saved['deployment']['status'] == 'verified' and saved['integration']['status'] != 'verified':
            raise ValueError('Verified deployment requires verified integration.')
