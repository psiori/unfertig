"""Opt-in isolated publication; shared checkout repair never stages user work."""
import copy
import json
import re
from pathlib import Path
import uuid

from agent_metrics import checked
from integration import GitFailure, StaleCandidate
from storage import Conflict, digest

DEFAULTS = dict(mode='strict', automatic_repair=False, max_attempts=3, reuse_board_metadata=False)


def settings(value):
    if not isinstance(value, dict):
        raise ValueError('workflow.integration must be an object.')
    result = {**DEFAULTS, **value}
    if result['mode'] not in ('strict', 'relaxed'):
        raise ValueError('workflow.integration.mode must be strict or relaxed.')
    for key in ('automatic_repair', 'reuse_board_metadata'):
        if type(result[key]) is not bool:
            raise ValueError('workflow.integration.'+key+' must be boolean.')
    if type(result['max_attempts']) is not int or not 1 <= result['max_attempts'] <= 10:
        raise ValueError('workflow.integration.max_attempts must be 1–10.')
    return result


def validate(run):
    """Common protected-record validation for HTTP and filesystem adapters."""
    for value in [run, *run.get('repositories', [])]:
        for key in ('integration_repairs', 'integration_retries', 'checkout_inspection', 'intervening_changes'):
            if key in value and (not isinstance(value[key], list) or any(not isinstance(e, dict) for e in value[key])):
                raise ValueError('Invalid workflow '+key)
        for key, states in (('checkout_sync', ('deferred', 'synchronized')),
                            ('integration_publication', ('pending', 'confirmed'))):
            if key in value:
                entry = value[key]
                if (not isinstance(entry, dict) or entry.get('status') not in states or
                        not re.fullmatch('[a-f0-9]{40,64}', str(entry.get('commit', '')))):
                    raise ValueError('Invalid workflow '+key)
        if 'pin_update' in value:
            entry = value['pin_update']
            if (not isinstance(entry, dict) or entry.get('status') not in ('pending', 'complete') or
                    any(not re.fullmatch('[a-f0-9]{40,64}', str(entry.get(k, ''))) for k in ('base', 'tree')) or
                    not isinstance(entry.get('pins'), dict) or not isinstance(entry.get('index'), dict) or
                    set(entry['pins']) != set(entry['index']) or
                    any(not re.fullmatch('[a-f0-9]{40,64}', str(v)) for v in entry['pins'].values())):
                raise ValueError('Invalid workflow pin_update')
        if 'integration_evidence' in value:
            entry = value['integration_evidence']
            if (not isinstance(entry, dict) or any(not re.fullmatch('[a-f0-9]{40,64}', str(entry.get(k, '')))
                    for k in ('tested_commit', 'final_commit', 'implementation')) or
                    any(not re.fullmatch('[a-f0-9]{64}', str(entry.get(k, ''))) for k in ('command', 'scope')) or
                    not isinstance(entry.get('metadata'), list)):
                raise ValueError('Invalid workflow integration_evidence')


def ancestor(w, old, new, cwd=None):
    try:
        w.git('merge-base', '--is-ancestor', old, new, cwd=cwd)
        return True
    except GitFailure as error:
        if error.evidence['returncode'] != 1:
            raise
        return False


class Changes:
    """Fail closed on unknown content. File extensions are never independence proof.

    Only the owning board's records can be metadata. The active task, transitive
    prerequisites and linked originals retain their semantic content. All commit
    edges are inspected, including intermediate edits later reverted and gitlinks.
    """
    def __init__(self, w, ident, run):
        self.w, self.run, self.ident = w, run, ident
        self.repo = Path(run['repository']).resolve()
        snapshot = w.snapshot()['data']
        self.ideas = snapshot['ideas']
        todos = {t['id']: t for t in snapshot['todos']}
        self.protected = set()
        pending = [ident]
        while pending:
            key = pending.pop()
            if key not in self.protected:
                self.protected.add(key)
                pending.extend(todos.get(key, {}).get('depends_on', []))
        self.sources = {i for key in self.protected for i in todos.get(key, {}).get('source_ideas', [])}
        self.entries = []
        self.checking_checkout = False
        self.remaining = 512
        # Worker changes cannot turn their own inputs into reusable metadata.
        self.touched = set(w.git('diff', '--name-only', '-z', run['base'], run['commit']).split('\0')) - {''}

    def board(self, path, old, new):
        try:
            a, b = json.loads(old), json.loads(new)
            if not all(v is None or isinstance(v, dict) for v in (a, b)):
                return False
            if a is not None and b is not None and a.get('format_version') != b.get('format_version'):
                return False
            if path == self.w.store.path.resolve():
                if not a or not b or a.get('schema_version') != 2 or b.get('schema_version') != 2:
                    return False
                self.w.store.validator(dict(schema_version=1, ideas=a['ideas'], todos=[]))
                self.w.store.validator(dict(schema_version=1, ideas=b['ideas'], todos=[]))
                a = dict(a, ideas=[i for i in a['ideas'] if i['id'] in self.sources])
                b = dict(b, ideas=[i for i in b['ideas'] if i['id'] in self.sources])
                return a == b
            if path.parent == (self.w.store.root / 'todos').resolve() and path.suffix == '.json':
                if any(v is not None and v.get('id') != path.stem for v in (a, b)):
                    return False
                for value in (a, b):
                    if value is not None:
                        self.w.store.validator(dict(schema_version=1, ideas=self.ideas, todos=[value]))
                if path.stem not in self.protected:
                    return True
                if path.stem != self.ident:
                    return a == b
                if a is None or b is None:
                    return False
                # Only our own coordinator observations can change without
                # invalidating the task/dependency snapshot used by checks.
                observations = {'workflow', 'updated_at', 'status', 'pr_url', 'commit_url',
                                'commit_hash', 'completion_summary', 'date_closed', 'closed_by'}
                return {k:v for k,v in a.items() if k not in observations} == {
                    k:v for k,v in b.items() if k not in observations}
        except (ValueError, KeyError, TypeError):
            pass
        return False

    def published_pin(self, path, label, target):
        if not self.checking_checkout or self.run.get('role') != 'context':
            return False
        owner = getattr(self.w, 'metrics_run', {})
        for child in owner.get('repositories', []):
            if (child.get('role') != 'project' or child.get('declared_path') != label or
                    child.get('published_commit') != target or Path(child['repository']).resolve() != path.resolve()):
                continue
            from managed_completion import authorized
            authorized(child)
            expected = self.w.git('ls-tree', self.run['commit'], '--', label)
            if not expected.startswith('160000 ') or expected.split()[2] != target:
                return False
            branch = child.get('recipe', {}).get('base_branch', 'main')
            self.w.git('fetch', 'origin', branch, cwd=path)
            return ancestor(self.w, target, 'origin/'+branch, cwd=path)
        return False

    def diff(self, repo, old, new=None, *, cached=False, depth=0, prefix=''):
        args = ['diff', '--raw', '-z', '--no-abbrev', '--no-renames', '--ignore-submodules=none']
        if cached:
            args += ['--cached']
        args += [old] + ([new] if new else []) + ['--']
        raw = self.w.git(*args, cwd=repo).split('\0')
        neutral = True
        for index in range(0, len(raw)-1, 2):
            modes, name = raw[index].split(), raw[index+1]
            before, after, old_oid, new_oid = modes[:4]
            before = before.lstrip(':')
            path = Path(repo) / name
            label = prefix + name
            safe = False
            published = False
            self.remaining -= 1
            if self.remaining < 0 or depth > 4:
                raise Conflict('Change inspection limit reached; inspect retained revisions manually.')
            # Touching a parent gitlink in the implementation makes every
            # downstream difference relevant, even board metadata.
            overlaps = any(label == p or label.startswith(p+'/') or p.startswith(label+'/') for p in self.touched)
            if before == after == '160000':
                try:
                    if path.resolve() != path or self.w.git('rev-parse', '--show-toplevel', cwd=path) != str(path.resolve()):
                        raise ValueError('Submodule is unavailable or has a different root.')
                    target = new_oid if set(new_oid) != {'0'} else self.w.git('rev-parse', 'HEAD', cwd=path)
                    safe = self.commits(path, old_oid, target, depth=depth+1, prefix=label+'/')
                    published = self.published_pin(path, label, target)
                    safe = (safe and not overlaps) or published
                    if not new and not cached:
                        safe = self.diff(path, 'HEAD', cached=True, depth=depth+1, prefix=label+'/') and safe
                        safe = self.diff(path, 'HEAD', depth=depth+1, prefix=label+'/') and safe
                        if self.w.git('ls-files', '--others', '--exclude-standard', cwd=path):
                            safe = False
                except (OSError, ValueError) as error:
                    self.entries.append(dict(path=label, reason=str(error)[-500:], classification='relevant'))
            elif (not overlaps and {before, after} <= {'100644', '000000'} and
                  (path.resolve() == self.w.store.path.resolve() or
                   path.resolve().parent == (self.w.store.root / 'todos').resolve())):
                a = self.w.git('cat-file', 'blob', old_oid, cwd=repo) if before != '000000' else 'null'
                if after == '000000':
                    b = 'null'
                elif set(new_oid) == {'0'}:
                    b = path.read_text() if path.is_file() and not path.is_symlink() else ''
                else:
                    b = self.w.git('cat-file', 'blob', new_oid, cwd=repo)
                safe = self.board(path.resolve(), a, b)
            self.entries.append(dict(path=label, old=old_oid, new=new_oid,
                                     classification=('published_dependency' if published else 'board_metadata') if safe else 'relevant'))
            neutral = safe and neutral
        return neutral

    def commits(self, repo, old, new, *, depth=0, prefix=''):
        if old == new:
            return True
        if not ancestor(self.w, old, new, cwd=repo):
            self.entries.append(dict(path=prefix, old=old, new=new, classification='relevant', reason='Non-forward history'))
            return False
        rows = self.w.git('rev-list', '--parents', '--max-count=257', old+'..'+new, cwd=repo).splitlines()
        if len(rows) > 256:
            raise Conflict('Intervening history exceeds 256 commits; manual review required.')
        neutral = True
        for row in rows:
            commit, *parents = row.split()
            for parent in parents:
                neutral = self.diff(repo, parent, commit, depth=depth, prefix=prefix) and neutral
        return neutral

    def checkout(self):
        self.checking_checkout = True
        safe = self.diff(self.repo, 'HEAD', cached=True)
        safe = self.diff(self.repo, 'HEAD') and safe
        untracked = self.w.git('ls-files', '--others', '--exclude-standard', '-z').split('\0')
        for name in filter(None, untracked):
            self.entries.append(dict(path=name, classification='relevant', reason='Untracked content'))
            safe = False
        return safe


def materialize_pins(w, repository, candidate, depth=0):
    """Check out exact dependencies from known local objects, never clone/fetch.

    Separate detached worktrees preserve installed runtime checkouts and remain
    beside the candidate for recovery. Missing objects require owner action.
    """
    if depth > 4:
        raise Conflict('Submodule dependency depth exceeds four; inspect manually.')
    for entry in w.git('ls-tree', '-rz', 'HEAD', cwd=candidate).split('\0'):
        if not entry:
            continue
        meta, name = entry.split('\t', 1)
        mode, kind, commit = meta.split()
        if mode != '160000':
            continue
        source, target = repository / name, candidate / name
        if (source.resolve() != source or not source.is_dir() or
                w.git('rev-parse', '--show-toplevel', cwd=source) != str(source.resolve())):
            raise Conflict('Initialize the declared dependency in its owner before integration: '+name)
        if (target / '.git').exists():
            if w.git('status', '--porcelain', cwd=target):
                raise Conflict('Retained candidate dependency changed: '+str(target))
            if w.git('rev-parse', 'HEAD', cwd=target) != commit:
                w.git('checkout', '--detach', commit, cwd=target)
        else:
            try:
                w.git('worktree', 'add', '--detach', str(target), commit, cwd=source)
            except GitFailure as error:
                raise Conflict('Cannot prepare exact dependency '+name+' at '+commit+'. Make its objects available in the owning checkout and retry.') from error
        materialize_pins(w, source, target, depth+1)


def record(w, ident, run, **entry):
    run.setdefault('integration_repairs', []).append(entry)
    run['message'] = entry.get('message', run.get('message', ''))
    with w.lock:
        if ident in w.live:
            w.live[ident]['message'] = run['message']
    w.save(ident, run)


def inspect_checkout(w, ident, run):
    changes = Changes(w, ident, run)
    safe = changes.checkout()
    run['checkout_inspection'] = changes.entries
    if not safe:
        paths = sorted({e['path'] for e in changes.entries if e['classification'] == 'relevant'})
        raise Conflict('Shared checkout has relevant or unclassified edits: '+', '.join(paths)+
                       '. Commit or move them in their owner, then retry; no edits were changed.')
    return changes.entries


def guard(w, ident, run, pr):
    from workflow import scope_digest
    todo = next(t for t in w.snapshot()['data']['todos'] if t['id'] == ident)
    owner = getattr(w, 'metrics_run', run)
    if todo['status'] == 'closed' or scope_digest(todo) != run['scope']:
        raise Conflict('Task scope or status changed during integration; review required.')
    if owner.get('repositories'):
        from context_workflow import guard as context_guard
        context_guard(getattr(w, 'context_owner', w), owner)
    if w.dependency_block(todo, w.snapshot()):
        raise Conflict('Task dependencies changed; '+w.dependency_block(todo, w.snapshot()))
    if w.git('rev-parse', run['branch']) != run['commit'] or w.git('status', '--porcelain', cwd=run['worktree']):
        raise Conflict('Implementation changed during integration; review required.')
    latest = w.pr_state(run)
    if latest['headRefOid'] != run['commit'] or latest['state'] != pr['state']:
        raise Conflict('PR changed during integration; review required.')
    from managed_completion import authorized
    authorized(run)


def synchronize(w, run, commit, policy):
    """Only a clean fast-forward. Git itself guards concurrent index/worktree edits."""
    branch = w.options['base_branch']
    outcome = dict(status='deferred', commit=commit, message='Automatic shared-checkout repair is disabled.')
    if policy['automatic_repair']:
        try:
            if w.git('branch', '--show-current') != branch or w.git('status', '--porcelain', '--ignore-submodules=none'):
                outcome['message'] = 'Published; shared checkout retained because it has edits or is on another branch.'
            elif not ancestor(w, w.git('rev-parse', 'HEAD'), commit):
                outcome['message'] = 'Published; shared checkout has newer/divergent history. Review before synchronization.'
            else:
                w.git('-c', 'submodule.recurse=false', 'merge', '--ff-only', commit)
                outcome.update(status='synchronized', message='Published commit fast-forwarded into the clean shared checkout.')
        except (ValueError, OSError) as error:
            outcome['message'] = 'Published; checkout synchronization failed: '+str(error)[-1000:]
    run['checkout_sync'] = outcome


def integrate(w, ident, run):
    """Called inside the coordinator's existing repository lock and action grant."""
    policy = settings(w.options.get('integration', {}))
    branch = w.options['base_branch']
    if not run.get('pr_url'):
        w.ensure_pr(next(t for t in w.snapshot()['data']['todos'] if t['id'] == ident), run)
    pr = w.pr_state(run)
    guard(w, ident, run, pr)
    inspect_checkout(w, ident, run)
    w.git('fetch', 'origin', branch)
    remote = w.git('rev-parse', 'refs/remotes/origin/'+branch)
    # Recover an uncertain/acknowledged push by identity, never by a guessed PR state.
    intent = run.get('integration_publication', {})
    if intent.get('status') in ('pending', 'confirmed') and intent.get('implementation') == run['commit'] and intent.get('scope') == run['scope']:
        final = intent['commit']
        if ancestor(w, final, remote) and evidence_valid(w, ident, run, final):
            finish(w, ident, run, final, policy)
            return
    local = w.git('rev-parse', 'refs/heads/'+branch)
    # A different checked-out branch is harmless: never fold its commits into main.
    state = dict(main=local, remote=remote, pr_head=pr['headRefOid'], pr_state=pr['state'])
    if pr['state'] == 'MERGED':
        merged = (pr.get('mergeCommit') or {}).get('oid')
        if not merged or not ancestor(w, merged, remote):
            raise Conflict('GitHub merge is not present on origin/'+branch+'. Retry after confirmation.')
        run['github_merge_commit'] = merged
    retained = Path(run.get('integration_worktree', '/nonexistent'))
    prior = run.get('integration_attempt', {})
    resumable = (bool(run.get('conflicted_paths')) and retained.is_dir() and
                 not w.git('status', '--porcelain', cwd=retained) and
                 w.git('branch', '--show-current', cwd=retained) == run.get('integration_branch') and
                 all(ancestor(w, parent, 'HEAD', cwd=retained) for parent in
                     (prior.get('main', run['base']), prior.get('remote', run['base']), run['commit'])))
    if resumable:
        candidate = retained
        run['integration_attempt'] = state
        record(w, ident, run, stage='resume', attempt=state, message='Retesting the committed resolution in its retained candidate.')
    else:
        if run.get('integration_worktree'):
            run.setdefault('integration_history', []).append(dict(worktree=run['integration_worktree'],
                attempt=copy.deepcopy(prior), tested=run.get('integration_tested_commit'),
                final=run.get('integration_commit'), diagnostics=run.get('git_diagnostics')))
        token = uuid.uuid4().hex
        candidate = Path(run['worktree']).parent / (run['run_id']+'-integration-'+token[:8])
        run.update(integration_worktree=str(candidate), integration_branch='codex/integration-'+token, integration_attempt=state)
        record(w, ident, run, stage='candidate', attempt=state, message='Building isolated integration candidate; preserving shared checkout.')
        # Saving progress may itself advance main; retain that history too.
        local = w.git('rev-parse', 'refs/heads/'+branch)
        state['main'] = local
        w.git('worktree', 'add', '-b', run['integration_branch'], str(candidate), local)
    for target in [local, remote] + ([] if pr['state'] == 'MERGED' else [run['commit']]):
        try:
            w.git('merge', '--no-edit', target, cwd=candidate)
        except GitFailure as error:
            run['git_diagnostics'] = error.evidence
            run['conflicted_paths'] = w.git('diff', '--name-only', '--diff-filter=U', cwd=candidate).splitlines()
            raise Conflict('Substantive merge conflict; resolve and commit in the retained isolated candidate, then retry. '+str(error)) from error
    materialize_pins(w, Path(run['repository']), candidate)
    inspection = Changes(w, ident, run)
    inspection.commits(Path(run['repository']), run['base'], local)
    inspection.commits(Path(run['repository']), run['base'], remote)
    run['intervening_changes'] = inspection.entries
    commit = w.git('rev-parse', 'HEAD', cwd=candidate)
    issues = w.candidate_migration_issues(run, candidate)
    if issues:
        raise Conflict('; '.join(issues))
    # Fresh tests on the combined code. Reuse is limited to metadata arriving
    # after these checks; a restart always rechecks unless publication is proven.
    record(w, ident, run, stage='testing', candidate_commit=commit, message='Testing the combined candidate and exact pinned dependencies.')
    checked(w, run, ident, cwd=candidate, stage='integration')
    if w.git('rev-parse', 'HEAD', cwd=candidate) != commit or w.git('status', '--porcelain', cwd=candidate):
        raise Conflict('Integration checks changed the candidate; inspect the retained worktree.')
    run.update(integration_commit=commit, integration_tested_commit=commit,
               integration_evidence=dict(tested_commit=commit, final_commit=commit,
                    command=test_identity(w, run), scope=run['scope'], implementation=run['commit'], metadata=[]))
    guard(w, ident, run, pr)
    # A bounded repair pass merges only proven metadata into the tested tree.
    # Relevant movement leaves this candidate intact and rebuilds on next attempt.
    w.git('fetch', 'origin', branch)
    latest_remote = w.git('rev-parse', 'refs/remotes/origin/'+branch)
    latest_local = w.git('rev-parse', 'refs/heads/'+branch)
    for origin, old, new in (('remote', remote, latest_remote), ('local', local, latest_local)):
        if old == new:
            continue
        delta = Changes(w, ident, run)
        neutral = delta.commits(Path(run['repository']), old, new)
        run['integration_evidence']['metadata'].extend(delta.entries)
        if neutral and origin == 'local' and not policy['reuse_board_metadata']:
            run.setdefault('integration_repairs', []).append(dict(stage='retained_local_history',
                old=old, new=new, changes=delta.entries,
                message='Later local board history retained in shared checkout; publishing the exact tested candidate.'))
            continue
        if not neutral or not policy['automatic_repair'] or not policy['reuse_board_metadata']:
            record(w, ident, run, stage='rebuild', old=old, new=new, changes=delta.entries,
                   message='Main advanced with relevant changes; rebuilding and retesting the combined candidate.')
            raise StaleCandidate(run['message'])
        try:
            w.git('merge', '--no-edit', new, cwd=candidate)
        except GitFailure as error:
            run['git_diagnostics'] = error.evidence
            raise Conflict('Concurrent board history conflicts; retained candidate needs review.') from error
    final = w.git('rev-parse', 'HEAD', cwd=candidate)
    if final != commit:
        delta = Changes(w, ident, run)
        # Comparing the final trees also catches merge interactions. Intervening
        # edges were already inspected above; merging a code parent isn't a delta.
        if not delta.diff(Path(run['repository']), commit, final):
            raise StaleCandidate('Combined metadata merge changed relevant content; rebuild and retest.')
        run['integration_evidence']['metadata'].extend(delta.entries)
    materialize_pins(w, Path(run['repository']), candidate)
    if w.git('rev-parse', 'HEAD', cwd=candidate) != final or w.git('status', '--porcelain', cwd=candidate):
        raise Conflict('Final integration candidate changed; inspect before retrying.')
    run['integration_evidence']['final_commit'] = final
    run['integration_commit'] = final
    guard(w, ident, run, pr)
    inspect_checkout(w, ident, run)
    run['integration_publication'] = dict(status='pending', commit=final, implementation=run['commit'], scope=run['scope'])
    record(w, ident, run, stage='publish', tested_commit=commit, final_commit=final,
           message='Combined candidate verified; publishing with a normal fast-forward push.')
    try:
        w.git('push', 'origin', final+':refs/heads/'+branch)
    except GitFailure as error:
        run['git_diagnostics'] = error.evidence
        w.git('fetch', 'origin', branch)
        observed = w.git('rev-parse', 'refs/remotes/origin/'+branch)
        if ancestor(w, final, observed):
            finish(w, ident, run, final, policy)
            return
        if observed != latest_remote and ancestor(w, latest_remote, observed):
            record(w, ident, run, stage='push_retry', old=latest_remote, new=observed,
                   message='Remote advanced during push; preserving evidence and rebuilding.')
            raise StaleCandidate(run['message']) from error
        raise Conflict('Push unconfirmed; inspect authentication/protection/network diagnostics, then explicitly retry. No force push.') from error
    w.git('fetch', 'origin', branch)
    if not ancestor(w, final, w.git('rev-parse', 'refs/remotes/origin/'+branch)):
        raise Conflict('Push returned success but publication is not confirmed; retain intent and retry.')
    finish(w, ident, run, final, policy)


def test_identity(w, run):
    return digest(w.argv('test', dict(run, worktree='{candidate}')))


def evidence_valid(w, ident, run, final):
    evidence = run.get('integration_evidence', {})
    if (evidence.get('final_commit') != final or evidence.get('implementation') != run['commit']
            or evidence.get('scope') != run['scope'] or evidence.get('command') != test_identity(w, run)
            or evidence.get('tested_commit') != run.get('integration_tested_commit')):
        return False
    tested = evidence['tested_commit']
    if tested != final and not settings(w.options.get('integration', {}))['reuse_board_metadata']:
        return False
    return ancestor(w, tested, final) and Changes(w, ident, run).diff(Path(run['repository']), tested, final)


def finish(w, ident, run, final, policy):
    run.update(published_commit=final, merge_commit=final)
    run['integration_publication']['status'] = 'confirmed'
    # Persist confirmation before optional repair; a crash resumes from this intent.
    w.save(ident, run)
    with w.store.lock:
        synchronize(w, run, final, policy)
    w.save(ident, run)
    w.complete_publication(ident, run)
