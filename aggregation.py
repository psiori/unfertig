"""Explicit local source views and a recoverable, one-way routing saga.

No child files are written here: every destination save uses its record API.
The original inbox idea durably owns the exact destination request until receipt.
"""
import copy
import hashlib
import json
from pathlib import Path
import threading
import uuid
import re
from datetime import datetime, timezone
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import URLError

from configuration import git_root
from storage import Conflict, digest
from versions import migrate, parse


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Source redirects are not allowed.')


def exchange(source, path='/api/state', body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Board-Token'] = token
    request = Request(source['url'] + path, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None,
                      method='PUT' if body is not None else 'GET')
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=3) as response:
            raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ValueError('Source response exceeds 5 MB.')
            return json.loads(raw)
    except (URLError, OSError) as error:
        raise ValueError(f'Source unavailable or rejected the request: {error}') from error


class Aggregation:
    def __init__(self, store):
        self.store = store
        self.sources = store.context.get('sources', [])
        self.cache = {}
        self.lock = threading.RLock()

    def inspect_source(self, source):
        snapshot = exchange(source)
        context = snapshot['context']
        if snapshot.get('api_version') != 2 or snapshot.get('protocol_version') != '2.0.0':
            raise ValueError('Source requires the supported record API.')
        if parse(snapshot.get('compatibility', {}).get('format_version', '0.0.0')) < (1, 2, 0):
            raise ValueError('Source must support format 1.2 routing/provenance; update it first.')
        if context.get('mode') == 'aggregation':
            raise ValueError('Nested aggregators are not supported (cycles cannot be traversed).')
        if Path(context['data']).resolve() != Path(source['data']) or context.get('project_id') != source['project_id']:
            raise ValueError('Source context does not match configured data and project identity.')
        self.store.validator(snapshot['data'])
        return snapshot

    def view(self):
        with self.lock:
            result = []
            for source in self.sources:
                key = source['project_id']
                try:
                    snapshot = self.inspect_source(source)
                    self.cache[key] = dict(project_id=key, name=snapshot['context'].get('project_name') or Path(source['data']).parent.name,
                                           url=source['url'], data=snapshot['data'], revision=snapshot['revision'],
                                           context=snapshot['context'], checked_at=datetime.now(timezone.utc).isoformat())
                    entry = dict(self.cache[key], status='reachable', error='')
                except (ValueError, KeyError, TypeError) as error:
                    entry = dict(self.cache.get(key, dict(project_id=key, name=Path(source['data']).parent.name,
                                                         url=source['url'], data=None, checked_at=None)),
                                 status='stale' if key in self.cache else 'unavailable', error=str(error))
                result.append(entry)
            return dict(sources=result)

    def save_route(self, idea, state, actor):
        revision = digest(idea)
        record = copy.deepcopy(idea)
        record['routing'] = state
        result = self.store.mutate(dict(request_id=uuid.uuid4().hex, actor=actor,
                                       changes=[dict(collection='ideas', id=idea['id'], revision=revision, record=record)]), routing=True)
        return next(i for i in result['data']['ideas'] if i['id'] == idea['id'])

    def route(self, body):
        # Also excludes ordinary project-selection edits during a route attempt.
        with self.lock, self.store.lock:
            self.store.require_writable()
            idea = next((i for i in self.store.read()[0]['ideas'] if i['id'] == body.get('idea_id')), None)
            if idea is None:
                raise ValueError('Unknown inbox idea.')
            actor = body.get('actor')
            if not isinstance(actor, str) or not actor.strip():
                raise ValueError('Supply the actual processing actor.')
            state = idea.get('routing', {})
            if state.get('status') == 'routed':
                return dict(idea=idea, history=self.store.snapshot()['history'])
            if not state.get('request') and digest(idea) != body.get('revision'):
                raise Conflict('Idea changed; preserve the draft and re-read its selection.')
            project = (state.get('project_id') if state.get('request') else None) or idea.get('selected_project') or body.get('project_id')
            if not project:
                idea = self.save_route(idea, dict(status='unclear', reason='Unclear: select one project or infer a single destination from the idea text.'), actor)
                return dict(idea=idea)
            source = next((s for s in self.sources if s['project_id'] == project), None)
            if source is None:
                raise ValueError('Destination is not configured.')
            try:
                snapshot = self.inspect_source(source)
                context = snapshot['context']
                expected = {k: context[k] for k in ('data', 'repository', 'process', 'project_id')}
                process = Path(context['process'])
                if process.name != 'PROCESS.md' or not process.is_file() or git_root(source['data']) != Path(context['repository']).resolve():
                    raise ValueError('Destination PROCESS.md or owning repository is invalid.')
                expected['process_sha256'] = hashlib.sha256(process.read_bytes()).hexdigest()
                if not state.get('request') and body.get('preflight') != expected:
                    raise ValueError('Read destination context, PROCESS.md and repository rules; provide matching preflight including process_sha256. Location alone is not authorization.')
                if state.get('request') and state.get('preflight') != expected:
                    raise ValueError('Destination preflight changed after claim; retain claim and review context manually.')
                if snapshot.get('compatibility', {}).get('read_only'):
                    raise ValueError('Destination is read-only; update it first.')
                if snapshot['history']['pending']:
                    raise ValueError('Destination history is pending; finish its local commit before retrying.')
                if not state.get('request'):
                    todo = copy.deepcopy(body.get('todo'))
                    if not isinstance(todo, dict):
                        raise ValueError('Supply the refined todo record.')
                    original = {k: idea[k] for k in ('id', 'text', 'author', 'date_entered')}
                    todo.update(author=idea['author'], created_by=actor, source_ideas=[],
                                source_refs=[dict(project_id=self.store.context['project_id'], idea=original)])
                    todo.pop('id', None)
                    candidate = migrate(dict(todo, id='T0001'), 'todo')
                    self.store.validator(dict(schema_version=1, ideas=[], todos=[candidate]))
                    initials = body.get('initials', '')
                    if not isinstance(initials, str) or (initials and not re.fullmatch(r'[A-Z][A-Z0-9]{0,11}', initials)):
                        raise ValueError('Initials must be empty or 1–12 uppercase letters/digits, starting with a letter.')
                    # Stable across workers and restarts; destination also rejects
                    # duplicate qualified provenance under its own writer lock.
                    request_id = hashlib.sha256((self.store.context['project_id'] + ':' + idea['id']).encode()).hexdigest()
                    request = dict(request_id=request_id, actor=actor, initials=body.get('initials', ''),
                                   changes=[dict(collection='todos', id=None, record=todo)])
                    state = dict(status='routing', project_id=project, selection='explicit' if idea.get('selected_project') else 'inferred',
                                 reason=body.get('reason', ''), request=request, preflight=expected)
                    idea = self.save_route(idea, state, actor)
                if self.store.pending.exists():
                    raise ValueError('Inbox claim saved; finish pending local history before destination writes.')
                result = exchange(source, '/api/changes', state['request'], snapshot['token'])
                assigned = next(a['id'] for a in result['assigned'] if a['collection'] == 'todos')
                if result['history']['pending']:
                    raise ValueError('Destination saved; local history pending. Retain claim and retry after history recovery.')
                state = dict(state, status='routed', todo_id=assigned, reason='', completed_at=datetime.now(timezone.utc).isoformat())
                idea = self.save_route(idea, state, actor)
            except (ValueError, KeyError, TypeError, StopIteration) as error:
                # Claims are never erased after an uncertain destination result.
                state = dict(state, status='blocked', project_id=project, reason=str(error))
                idea = self.save_route(idea, state, actor)
            return dict(idea=idea, history=self.store.snapshot()['history'])
