"""Saved editor authorization and retained-attempt continuation.

Only the owner editor adapter grants authorization. Ordinary record transports
preserve this evidence, and never infer authorization from task/actor text.
"""
import copy
from datetime import datetime, timezone

from storage import Conflict, digest

FIELDS = ('name', 'description', 'source_ideas', 'source_refs', 'depends_on', 'category')
MESSAGE = 'Saved task scope changed. Work is retained; use Retry implementation to continue with the saved task.'


def scope(todo):
    fields = {k: todo.get(k) for k in FIELDS[:4]}
    fields.update({k: todo[k] for k in FIELDS[4:] if todo.get(k)})
    return fields


def authorized(todo):
    entries = todo.get('scope_authorizations', [])
    return bool(entries and entries[-1]['new_scope'] == digest(scope(todo)))


def record_save(old, record, body):
    if scope(old) == scope(record) and (not old.get('workflow') or old['workflow']['scope'] == digest(scope(record)) or authorized(old)):
        return
    record.setdefault('scope_authorizations', []).append(dict(
        source='owner_editor_save', request_id=body['request_id'], actor=body.get('actor', 'editor'),
        at=datetime.now(timezone.utc).isoformat(), revision=digest(old),
        old_scope=digest(scope(old)), new_scope=digest(scope(record)),
        old=scope(old), new=scope(record)))


def validate(todo):
    entries = todo.get('scope_authorizations', [])
    if not isinstance(entries, list):
        raise ValueError('scope_authorizations must be a list.')
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('source') != 'owner_editor_save':
            raise ValueError('Invalid saved scope authorization.')
        for key in ('request_id', 'actor', 'at', 'revision'):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise ValueError('Invalid scope authorization '+key)
        for key in ('old', 'new'):
            if not isinstance(entry.get(key), dict) or digest(entry[key]) != entry.get(key+'_scope'):
                raise ValueError('Scope authorization does not match its requirements.')


def adopt(todo, run):
    """Called only at an authorized continuation point, after process guards."""
    current = digest(scope(todo))
    if current == run['scope']:
        return False
    if not authorized(todo):
        raise Conflict('No recorded owner-editor authorization for this task scope. Save the intended requirements in the task editor before Retry.')
    # Capture all evidence once; do not recursively copy earlier attempts.
    previous = copy.deepcopy({k: v for k, v in run.items() if k != 'scope_attempts'})
    run.setdefault('scope_attempts', []).append(previous)
    run['scope_save_request'] = todo['scope_authorizations'][-1]['request_id']
    for item in [run, *run.get('repositories', [])]:
        item['scope'] = current
        # Branches, PRs and process receipts stay in place. Published outcomes
        # and candidate evidence survive in the archive, never as completion
        # evidence for the new scope (including partially published sets).
        for key in ('commit', 'tested_commit', 'verification', 'accepted_result',
                    'implementation', 'worker_report', 'worker_report_digest',
                    'repository_heads', 'completion_summary', 'published_commit',
                    'merge_commit', 'integration_commit', 'integration_tested_commit',
                    'github_merge_commit', 'deployment_commit', 'post_publish',
                    'completion_boundary', 'integration_attempt', 'integration_worktree',
                    'integration_branch', 'integration_evidence', 'integration_publication',
                    'integration_repairs', 'integration_retries', 'checkout_sync',
                    'checkout_inspection', 'intervening_changes', 'pin_update',
                    'pin_update_history'):
            item.pop(key, None)
    run.update(phase='implementation_failed', message=MESSAGE)
    return True
