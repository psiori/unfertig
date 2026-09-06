"""Explicit branch publication; prepare merges without changing the live checkout.

No network calls on ordinary reads/saves. Only refresh() and push() contact the
configured upstream. All board access shares the BoardStore lock.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
import uuid

from storage import Conflict, digest


class GitError(ValueError):
    pass


class Publication:
    def __init__(self, store):
        self.store = store
        self.operation = threading.Lock()
        self.checked_at = None
        self.remote_error = ''

    def git(self, root, *args, check=True):
        try:
            result = subprocess.run(['git', '-c', 'submodule.recurse=false', '-C', str(root), *args], capture_output=True,
                                    text=True, timeout=30,
                                    env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        except subprocess.TimeoutExpired:
            raise GitError('Git timed out. Remote outcome may be unknown; check remote status before retrying.') from None
        if check and result.returncode:
            raise GitError((result.stderr or result.stdout).strip() or 'Git command failed.')
        return result

    def out(self, root, *args):
        return self.git(root, *args).stdout.strip()

    def destination(self):
        if not self.store.git:
            raise GitError('Publication unavailable: local Git history is disabled.')
        root = Path(self.out(self.store.root, 'rev-parse', '--show-toplevel')).resolve()
        branch = self.out(root, 'symbolic-ref', '--quiet', 'HEAD')
        remote = self.out(root, 'config', '--get', f'branch.{branch[11:]}.remote')
        refs = self.out(root, 'config', '--get-all', f'branch.{branch[11:]}.merge').splitlines()
        if remote == '.' or remote.startswith('-') or len(refs) != 1 or not refs[0].startswith('refs/heads/'):
            raise GitError('Configure one remote upstream branch before publishing.')
        upstream = self.out(root, 'rev-parse', '--symbolic-full-name', '@{upstream}')
        for key in (f'branch.{branch[11:]}.pushRemote', 'remote.pushDefault'):
            override = self.git(root, 'config', '--get', key, check=False).stdout.strip()
            if override and override != remote:
                raise GitError('Push remote differs from upstream. Resolve the Git configuration manually.')
        fetch_url = self.out(root, 'remote', 'get-url', remote)
        push_urls = self.out(root, 'remote', 'get-url', '--push', '--all', remote).splitlines()
        if push_urls != [fetch_url]:
            raise GitError('Fetch and push URLs differ or multiple push URLs exist. Configure one unambiguous destination.')
        return root, branch, remote, refs[0], upstream, fetch_url

    def checkout_blocker(self, root):
        for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer'):
            if Path(self.out(root, 'rev-parse', '--path-format=absolute', '--git-path', name)).exists():
                return 'Finish the existing Git operation manually before pushing.'
        if self.out(root, 'status', '--porcelain', '--untracked-files=all'):
            return 'Commit or set aside working-tree changes manually before pushing.'
        return ''

    def board_at(self, root, commit):
        """Read only Git blobs: never initialize/migrate or execute candidate code."""
        header_path = self.store.path.relative_to(root).as_posix()
        tree = self.out(root, 'ls-tree', '-r', '-z', commit)
        entries = [entry.split('\t', 1) for entry in tree.split('\0') if entry]
        modes = {path: metadata.split()[0] for metadata, path in entries}
        listing = list(modes)
        board_folder = Path(header_path).parent.as_posix()
        prefix = '' if board_folder == '.' else board_folder + '/'
        for path, mode in modes.items():
            relative = path[len(prefix):] if path.startswith(prefix) else None
            if relative is not None and (relative in ('.transaction.json', '.history-pending.json', '.server.lock') or relative.startswith('.receipts/')):
                raise GitError('Board runtime recovery files are tracked in Git; resolve manually before publishing.')
            if (path == header_path or path.startswith(prefix + 'todos/')) and mode != '100644':
                raise GitError('Board records must be ordinary non-executable files, not links or submodules.')
        if header_path not in listing:
            if any(p.startswith(str(Path(header_path).parent / 'todos') + '/') for p in listing):
                raise GitError('Remote board is incomplete; missing ideas file.')
            return {'schema_version': 1, 'ideas': [], 'todos': []}
        data = json.loads(self.out(root, 'show', f'{commit}:{header_path}'))
        if data.get('schema_version') != 2 or 'todos' in data:
            raise GitError('Unsupported remote storage schema. Update/migrate manually before publication.')
        data['schema_version'] = 1
        data['todos'] = []
        folder = (Path(header_path).parent / 'todos').as_posix() + '/'
        for path in listing:
            if path.startswith(folder) and '/' not in path[len(folder):] and path.endswith('.json'):
                item = json.loads(self.out(root, 'show', f'{commit}:{path}'))
                if Path(path).stem != item.get('id'):
                    raise GitError('Remote todo ID does not match its filename.')
                data['todos'].append(item)
        self.store.validator(data)
        if len(json.dumps(data).encode()) > 5_000_000:
            raise GitError('Remote board exceeds 5 MB.')
        return data

    def status(self):
        with self.store.lock:
            data, revision = self.store.read()
            result = dict(available=False, can_push=False, records={'ideas': {}, 'todos': {}},
                          board_revision=revision, checked_at=self.checked_at,
                          message='', error=self.remote_error)
            try:
                root, branch, remote, target, upstream, url = self.destination()
                head = self.out(root, 'rev-parse', 'HEAD')
                tip = self.out(root, 'rev-parse', upstream)
                committed = self.board_at(root, head)
                published = self.board_at(root, tip)
                ahead, behind = map(int, self.out(root, 'rev-list', '--left-right', '--count', f'{head}...{tip}').split())
                blocker = self.checkout_blocker(root)
                dirty = bool(blocker)
                result.update(available=True, branch=branch[11:], remote=remote, target=target,
                              ahead=ahead, behind=behind, dirty=dirty,
                              confirmation=digest([str(root), branch, remote, target, url, head, tip, revision]),
                              can_push=bool(ahead and not dirty and not self.store.pending.exists() and not self.remote_error))
                for kind in ('ideas', 'todos'):
                    saved = {r['id']: r for r in committed[kind]}
                    upstream_records = {r['id']: r for r in published[kind]}
                    for record in data[kind]:
                        ident = record['id']
                        state = ('uncommitted' if saved.get(ident) != record else
                                 'unknown' if self.remote_error else
                                 'published' if upstream_records.get(ident) == record else 'local')
                        result['records'][kind][ident] = state
                result['message'] = (f'{ahead} outgoing / {behind} incoming commits · {remote}/{target[11:]}. '
                                     'Push publishes all outgoing branch commits, including changes outside this board.')
                if dirty:
                    result['message'] += ' ' + blocker
                if self.store.pending.exists():
                    result['message'] += ' Finish pending local Git history first.'
                if not self.checked_at:
                    result['message'] += ' Compared with locally cached upstream; use Refresh to refresh.'
            except (GitError, OSError, ValueError, KeyError) as error:
                result['message'] = 'Publication unavailable. Configure an upstream and verify the board repository. ' + str(error)
            return result

    def fetch(self, destination):
        root, _, remote, target, upstream, _ = destination
        # Update only this configured tracking ref; no pull, branch switch or tags.
        try:
            self.git(root, 'fetch', '--no-tags', '--no-recurse-submodules', remote, f'{target}:{upstream}')
            self.remote_error = ''
            self.checked_at = datetime.now(timezone.utc).isoformat()
        except GitError as error:
            self.remote_error = 'Remote status unknown: ' + str(error)
            raise

    def refresh(self):
        if not self.operation.acquire(blocking=False):
            raise Conflict('A publication operation is already running.')
        try:
            with self.store.lock:
                self.fetch(self.destination())
                return self.status()
        finally:
            self.operation.release()

    def reconcile(self, base, local, remote, candidate):
        """Reject semantic collisions even when Git merged disjoint JSON lines."""
        for previous in (base, local, remote):
            self.store.validator(candidate, previous)
        for kind in ('ideas', 'todos'):
            maps = [{r['id']: r for r in data[kind]} for data in (base, local, remote, candidate)]
            before, ours, theirs, merged = maps
            for ident in set(ours) | set(theirs):
                a, b, old = ours.get(ident), theirs.get(ident), before.get(ident)
                if a != old and b != old and a != b:
                    raise Conflict(f'{ident} changed on both branches or its ID collided. Resolve manually.')
                expected = b if a == old else a
                if merged.get(ident) != expected:
                    raise Conflict(f'Merge would lose fields in {ident}. Resolve manually.')
        # Two processors can create different IDs for the same previously pending idea.
        old_ids = {t['id'] for t in base['todos']}
        ours = {s for t in local['todos'] if t['id'] not in old_ids for s in t['source_ideas']}
        theirs = {s for t in remote['todos'] if t['id'] not in old_ids and t not in local['todos'] for s in t['source_ideas']}
        if ours & theirs:
            raise Conflict('Concurrent processing of source ideas: ' + ', '.join(sorted(ours & theirs)))
        for key in (set(base) | set(local) | set(remote)) - {'ideas', 'todos'}:
            old, a, b = base.get(key), local.get(key), remote.get(key)
            if a != old and b != old and a != b:
                raise Conflict(f'Board metadata conflict: {key}')
            if candidate.get(key) != (b if a == old else a):
                raise Conflict(f'Merge would lose board metadata: {key}')

    def protect_local_files(self, root, head, candidate):
        # Git can overwrite ignored untracked files during checkout/fast-forward.
        added = self.out(root, 'diff', '--name-only', '-z', '--diff-filter=A', head, candidate).split('\0')
        for relative in filter(None, added):
            path = root / relative
            if path.exists() or path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
                raise Conflict(f'Incoming file would replace local content: {relative}. Nothing pushed; set it aside manually.')

    def push(self, confirmation):
        if not self.operation.acquire(blocking=False):
            raise Conflict('A publication operation is already running; check status before retrying.')
        try:
            with self.store.lock:
                current = self.status()
                if not confirmation or confirmation != current.get('confirmation'):
                    raise Conflict('Board, branch or destination changed. Refresh and review Push again.')
                if not current['can_push']:
                    raise Conflict(current['message'] + ' ' + current.get('error', ''))
                destination = self.destination()
                root, branch, remote, target, upstream, _ = destination
                head = self.out(root, 'rev-parse', 'HEAD')
                self.fetch(destination)
                tip = self.out(root, 'rev-parse', upstream)
                local = self.board_at(root, head)
                incoming = self.board_at(root, tip)
                base = self.board_at(root, self.out(root, 'merge-base', head, tip))
                # No live branch/index/worktree edits before a successful publication.
                with tempfile.TemporaryDirectory(prefix='unfertig-publish-') as temporary:
                    checkout = Path(temporary) / 'candidate'
                    self.git(root, 'worktree', 'add', '--detach', str(checkout), head)
                    try:
                        # Worktree-specific identity settings belong to the owning checkout.
                        name = self.out(root, 'config', 'user.name')
                        email = self.out(root, 'config', 'user.email')
                        merge = self.git(checkout, '-c', f'user.name={name}', '-c', f'user.email={email}',
                                         'merge', '--no-edit', '--no-stat', tip, check=False)
                        if merge.returncode:
                            raise Conflict('Git could not merge the remote changes. Live checkout unchanged. Resolve manually. ' + (merge.stderr or merge.stdout).strip())
                        candidate = self.out(checkout, 'rev-parse', 'HEAD')
                        self.reconcile(base, local, incoming, self.board_at(root, candidate))
                        self.protect_local_files(root, head, candidate)
                        if self.destination() != destination or self.out(root, 'rev-parse', 'HEAD') != head or self.checkout_blocker(root):
                            raise Conflict('Repository changed outside the board server. Nothing pushed; review manually.')
                        # Retain a reachable candidate for uncertain delivery / interrupted promotion.
                        recovery = 'refs/unfertig/publication/' + uuid.uuid4().hex
                        self.git(root, 'update-ref', recovery, candidate)
                        try:
                            self.git(root, 'push', '--porcelain', '--no-follow-tags', '--recurse-submodules=no', remote, f'{candidate}:{target}')
                        except GitError as error:
                            # The server may have accepted a push whose response was lost.
                            try:
                                self.fetch(destination)
                                accepted = self.git(root, 'merge-base', '--is-ancestor', candidate, upstream, check=False).returncode == 0
                            except GitError:
                                accepted = False
                            if not accepted:
                                raise GitError(f'Push failed or its outcome is unknown. Live checkout unchanged. Candidate retained at {recovery}; check remote before retrying. {error}') from None
                        try:
                            self.fetch(destination)
                            if self.destination() != destination or self.out(root, 'rev-parse', 'HEAD') != head or self.checkout_blocker(root):
                                raise GitError('Local repository changed during push.')
                            self.protect_local_files(root, head, candidate)
                            self.git(root, 'merge', '--ff-only', '--no-autostash', '--no-edit', '--no-stat', candidate)
                            self.store.read()  # Re-read promoted records; never serve a cached board.
                        except (GitError, ValueError, Conflict) as error:
                            raise GitError(f'Remote accepted the push. Local synchronization needs manual attention; do not undo remote history. Candidate: {recovery}. {error}') from None
                        self.git(root, 'update-ref', '-d', recovery, candidate)
                        return {'message': 'All outgoing branch commits published. Local board refreshed.', 'publication': self.status()}
                    finally:
                        self.git(root, 'worktree', 'remove', '--force', str(checkout), check=False)
        finally:
            self.operation.release()
