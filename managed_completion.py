"""Managed implementation handoff, independent of publication and delivery.

Only Workflow's authenticated owner actions issue publication grants. Reports,
ordinary board edits and the worker-readable receipt cannot grant permission.
"""
import copy
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from storage import Conflict, atomic, encode, digest
from versions import FORMAT_VERSION, inspect
from agent_metrics import accepted, checked, repair_needed


def result_path(run):
    return Path(run['worktree']).parent / (run['run_id']+'-result.txt')


def read_report(run):
    text = result_path(run).read_text()
    raw, separator, marker = text.strip().rpartition('\n')
    if marker not in ('UNFERTIG_IMPLEMENTATION_COMPLETE', 'UNFERTIG_NEEDS_ATTENTION'):
        raise Conflict('Retained worker report has no recognized final marker; review required.')
    report = json.loads(raw)
    if not isinstance(report, dict) or report.get('status') not in ('complete', 'needs_attention'):
        raise Conflict('Invalid worker outcome.')
    if (report['status'] == 'complete') != (marker == 'UNFERTIG_IMPLEMENTATION_COMPLETE'):
        raise Conflict('Contradictory worker outcome and marker.')
    if not isinstance(report.get('summary'), str) or not report['summary'].strip() or not all(
            isinstance(report.get(k), list) and all(isinstance(v, str) for v in report[k]) for k in ('tests', 'limitations')):
        raise Conflict('Completion report requires summary, tests and limitations.')
    return report, digest(text)


def validate(run):
    from context_workflow import validate as validate_context
    validate_context(run)
    from relaxed_integration import validate as validate_integration
    validate_integration(run)
    from post_publish import validate as validate_hooks
    if 'post_publish' in run:
        validate_hooks(run['post_publish'])
    for key in ('implementation', 'publication', 'verification', 'approval'):
        if key in run:
            value = run[key]
            if not isinstance(value, dict) or value.get('status') not in ('complete','blocked','pending','confirmed','passed','failed','not_requested'):
                raise ValueError('Invalid managed '+key+' outcome.')
            if 'commit' in value and not re.fullmatch('[a-f0-9]{40,64}', str(value['commit'])):
                raise ValueError('Invalid managed outcome commit.')
    for key in ('publication_authorization', 'resume_review'):
        if key in run and not isinstance(run[key], dict):
            raise ValueError('Invalid managed '+key+'.')
    if 'handoff_history' in run and not isinstance(run['handoff_history'], list):
        raise ValueError('Invalid managed handoff history.')


def authorized(run):
    grant = run.get('publication_authorization', {})
    if grant.get('source') != 'owner_action' or any(grant.get(k) != run.get(k) for k in ('repository','branch','run_id','pr_url')):
        raise Conflict('Publication authorization is absent or differs from repository, branch, run and PR. Use Verify existing result and resume for explicit owner review.')


def receipt(w, run):
    path = Path(run['worktree']).parent / (run['run_id']+'-publication.json')
    atomic(path, encode(dict(format_version=FORMAT_VERSION, run_id=run['run_id'],
                            repository=run['repository'], branch=run['branch'], pr_url=run['pr_url'],
                            publication=run.get('publication', {'status':'pending'}))))


def publish(w, run, commit):
    authorized(run)
    state = w.pr_state(run)
    if state['state'] != 'OPEN':
        raise Conflict('PR is no longer open; review integration separately.')
    ref = 'refs/heads/'+run['branch']
    remote = w.git('ls-remote', '--refs', 'origin', ref, cwd=run['worktree']).split()
    remote = remote[0] if len(remote) == 2 and remote[1] == ref else ''
    if remote != commit:
        if run.get('publication', {}).get('status') == 'blocked':
            raise Conflict('Publication is blocked. Review the failed/uncertain attempt and explicitly resume; no automatic retry.')
        if not remote or remote != state['headRefOid']:
            raise Conflict('Remote branch and PR disagree; inspect publication before retrying.')
        w.git('merge-base', '--is-ancestor', remote, commit, cwd=run['worktree'])
        try:
            w.git('push', 'origin', commit+':'+ref, cwd=run['worktree'])
        except Exception as error:
            run['publication'] = dict(status='blocked', commit=commit, message=str(error)[-2000:])
            receipt(w, run)
            raise
    # GitHub may lag a successful push. Poll read-only; never push again to
    # make its API catch up. Every read rechecks PR identity and branch ownership.
    for delay in (0, .1, .25, .5, 1, 2, 3):
        if delay:
            time.sleep(delay)
        state = w.pr_state(run)
        observed = w.git('ls-remote', '--refs', 'origin', ref, cwd=run['worktree']).split()
        if observed != [commit, ref] or state['state'] != 'OPEN':
            raise Conflict('Remote publication changed while confirming the PR.')
        if state['headRefOid'] == commit:
            break
        w.git('merge-base', '--is-ancestor', state['headRefOid'], commit, cwd=run['worktree'])
    else:
        run['publication'] = dict(status='pending', commit=commit, observed=state['headRefOid'],
                                  message='Git branch is published; GitHub PR confirmation is delayed. Retry to confirm without another push.')
        receipt(w, run)
        raise Conflict(run['publication']['message'])
    run['publication'] = dict(status='confirmed', commit=commit, pr_url=run['pr_url'])
    receipt(w, run)
    return commit


def recovery_view(w, todo, run, activity):
    view = dict(can_verify_existing=False)
    if activity or run.get('foreign') or todo['status'] == 'closed' or run.get('external_completions'):
        return view
    if run['phase'] not in ('interrupted','implementation_failed','handoff_blocked','test_failed'):
        return view
    try:
        report, fingerprint = read_report(run)
        view.update(can_verify_existing=True, retained_commit=report.get('commit'), report_digest=fingerprint,
                    retained_report=report)
    except (ValueError, OSError):
        pass
    return view


def prepare_resume(w, todo, run, body, automatic):
    if automatic or run['phase'] not in ('implementation_failed','handoff_blocked','implementing','testing','test_failed'):
        raise Conflict('Existing result recovery requires explicit review of a retained implementation.')
    if w.retained_activity(todo):
        raise Conflict('Active or uncertain worker prevents recovery.')
    process = w.process_receipt(run)
    if not process.is_file():
        raise Conflict('Missing worker process receipt; absence of an active worker cannot be confirmed.')
    if w.git('rev-parse','--path-format=absolute','--git-common-dir',cwd=run['worktree']) != w.git('rev-parse','--path-format=absolute','--git-common-dir'):
        raise Conflict('Retained worktree belongs to another repository.')
    report, fingerprint = read_report(run)
    if body.get('report_digest') != fingerprint or report.get('commit') != body.get('commit'):
        raise Conflict('Review the exact retained report and implementation commit before resuming.')
    if not all(isinstance(body.get(k), str) and body[k].strip() for k in ('actor','reason')):
        raise Conflict('Recovery requires the reviewing actor and reason.')
    if w.git('branch', '--show-current', cwd=run['worktree']) != run['branch'] or w.git('rev-parse','HEAD',cwd=run['worktree']) != body['commit']:
        raise Conflict('Retained worktree does not contain the reviewed branch and HEAD.')
    if body['commit'] == run.get('kickoff_commit', run['base']):
        raise Conflict('Kickoff is not implementation evidence.')
    state = w.pr_state(run)
    if state['state'] != 'OPEN' or state['headRefOid'] != body['commit']:
        # Explicit recovery publishes only a fast-forward of the reviewed remote head.
        if body.get('remote_commit') != state['headRefOid'] or state['state'] != 'OPEN':
            raise Conflict('Remote PR HEAD differs. Review its exact current head before authorizing publication.')
        w.git('merge-base','--is-ancestor',state['headRefOid'],body['commit'],cwd=run['worktree'])
    run.setdefault('handoff_history', []).append({k:copy.deepcopy(run[k]) for k in (
        'phase','message','implementation','publication','verification','approval','resume_review') if k in run})
    run['resume_review'] = dict(actor=body['actor'], reason=body['reason'], commit=body['commit'],
        report_digest=fingerprint, at=datetime.now(timezone.utc).isoformat(),
        publication_only=body.get('publication_only') is True)
    # This owner action is a new, explicit scoped authorization, not a board label
    # or a retry around an execution review denial. It cannot approve worker tools.
    run['publication_authorization'] = dict(source='owner_action', repository=run['repository'],
        branch=run['branch'], pr_url=run['pr_url'], run_id=run['run_id'], request_id=body.get('request_id',''))
    run['publication'] = dict(status='pending')


def finish(w, todo, run):
    from workflow import scope_digest
    ident = todo['id']
    report, fingerprint = read_report(run)
    commit = w.git('rev-parse','HEAD',cwd=run['worktree'])
    run.setdefault('handoff_history', []).append({k:copy.deepcopy(run[k]) for k in ('phase','message','worker_report','worker_report_digest','implementation','publication','verification','approval') if k in run})
    run['worker_report'] = copy.deepcopy(report)
    run['worker_report_digest'] = fingerprint
    run['completion_summary'] = report['summary'].strip()+'\n\nVerification: '+('; '.join(report['tests']) or 'No worker checks reported')+'\nLimitations: '+('; '.join(report['limitations']) or 'None reported')
    run['verification'] = dict(status='pending')
    run['implementation'] = dict(status='blocked', commit=commit)
    run['approval'] = dict(status='not_requested')
    if report.get('commit') != commit:
        raise Conflict('Worker report does not match local HEAD.')
    current = next(t for t in w.snapshot()['data']['todos'] if t['id'] == ident)
    if current['status'] == 'closed' or current.get('workflow',{}).get('external_completions') or current['workflow']['run_id'] != run['run_id']:
        raise Conflict('Ticket lifecycle changed during implementation.')
    w.guard_process(run)
    if current['workflow']['scope'] != run['scope'] or scope_digest(current) != run['scope']:
        raise Conflict('Task scope changed; review required.')
    if w.git('branch','--show-current',cwd=run['worktree']) != run['branch'] or w.git('status','--porcelain',cwd=run['worktree']):
        raise Conflict('Implementation worktree changed or has uncommitted changes.')
    if commit == run.get('kickoff_commit',run['base']):
        raise Conflict('No implementation changes reported. Coordinator review required.')
    process = w.process_receipt(run)
    if process.exists():
        saved = json.loads(process.read_text())
        if inspect(saved, 'worker receipt')[0] == 'read_only' or (saved.get('purpose', 'worker') == 'worker' and saved.get('returncode') != 0):
            raise Conflict('Worker process did not exit successfully; review required.')
    structured = report.get('implementation')
    blockers = report.get('blockers', [])
    if structured is not None:
        if structured != 'complete' or not isinstance(blockers,list) or any(v not in ('publication','publication_approval','execution_approval') for v in blockers):
            if isinstance(blockers, list) and blockers and set(blockers) <= {'implementation', 'verification'}:
                repair_needed(run)
            raise Conflict('Worker has genuine implementation blockers; publication cannot resolve them.')
        if report['status'] == 'needs_attention' and not blockers:
            raise Conflict('Worker needs attention without a classified blocker; review required.')
    elif report['status'] != 'complete':
        review = run.get('resume_review', {})
        if not (review.get('publication_only') and review.get('report_digest') == fingerprint and review.get('commit') == commit):
            raise Conflict('Legacy needs_attention report requires explicit owner review that publication was the sole blocker.')
    run['implementation'] = dict(status='complete', commit=commit)
    run['commit'] = commit
    review = run.get('resume_review', {})
    if 'publication_approval' in blockers and not (review.get('publication_only') and review.get('report_digest') == fingerprint):
        run['approval'] = dict(status='blocked', message='Publication approval discrepancy: owner must review the exact report and authorize coordinator publication using Verify existing result and resume. Worker tool approvals remain with the execution approval service.')
        w.save(ident,run)
        raise Conflict(run['approval']['message'])
    if 'execution_approval' in blockers or report.get('approval') not in (None, 'not_requested', 'publication'):
        run['approval'] = dict(status='blocked', message='Resolve worker execution approval through its trusted execution service; board recovery cannot grant worker tool permission.')
        raise Conflict(run['approval']['message'])
    w.save(ident,run)
    try:
        publish(w,run,commit)
    except Exception as error:
        run['publication'] = dict(status='blocked', commit=commit, message=str(error))
        raise
    run['approval'] = dict(status='confirmed' if review else 'not_requested')
    w.save(ident,run)
    try:
        checked(w, run, ident)
        if w.git('rev-parse','HEAD',cwd=run['worktree']) != commit or w.git('status','--porcelain',cwd=run['worktree']):
            raise Conflict('Verification changed the reviewed worktree.')
        current = next(t for t in w.snapshot()['data']['todos'] if t['id'] == ident)
        if scope_digest(current) != run['scope'] or current['status'] == 'closed':
            raise Conflict('Task changed during verification.')
        state = w.pr_state(run)
        if state['state'] != 'OPEN' or state['headRefOid'] != commit:
            raise Conflict('PR HEAD changed during verification.')
        run['verification'] = dict(status='passed',commit=commit)
    except Exception as error:
        run['verification'] = dict(status='failed',commit=commit,message=str(error))
        raise
    body = Path(run['worktree']).parent / (run['run_id']+'-pr.md')
    body.write_text(todo['description']+'\n\n'+run['completion_summary']+'\nConfigured implementation checks passed at '+commit+'.\n'+w.publication_context(run))
    w.github('pr','edit',run['pr_url'],'--title','[unfertig] '+ident+': '+todo['name'],'--body-file',str(body))
    if state.get('isDraft'):
        w.github('pr','ready',run['pr_url'])
    accepted(run)
    run.update(phase='ready',message='Implementation and remote PR HEAD verified; configured checks passed. Ready for review. Merge and deployment require separate authorization.')
