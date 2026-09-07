"""Explicit local source views and a recoverable, one-way routing saga.

Both adapters use the shared BoardStore record transaction contract.
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
from urllib.error import URLError, HTTPError
from http.client import RemoteDisconnected, IncompleteRead
import errno

from storage import Conflict, digest
from versions import migrate, parse
from transports import filesystem, preflight_context


class Unreachable(ValueError):
    """Only connection establishment or lost-response failures allow fallback."""


class SourceRejected(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


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
    except HTTPError as error:
        raise SourceRejected(error.code, f'Source rejected HTTP request ({error.code}); filesystem fallback is forbidden. {error.read(4096).decode(errors="replace")}') from error
    except (TimeoutError, ConnectionError, RemoteDisconnected, IncompleteRead) as error:
        raise Unreachable(f'HTTP connection unavailable or response lost: {error}') from error
    except URLError as error:
        reason = error.reason
        if isinstance(reason, (TimeoutError, ConnectionError)) or getattr(reason, 'errno', None) in (errno.ECONNREFUSED, errno.ECONNRESET, errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH):
            raise Unreachable(f'HTTP source cannot be reached: {error}') from error
        raise ValueError(f'HTTP transport blocked: {error}') from error


class Aggregation:
    def __init__(self, store):
        self.store = store
        self.sources = store.context.get('sources', [])
        self.cache = {}
        self.lock = threading.RLock()

    def transfer(self, source, path='/api/state', body=None, token=None, expected=None):
        enabled = source.get('transports', {'http': True, 'filesystem': False})
        if not any(enabled.values()):
            raise ValueError('At least one transport must be enabled.')
        reason = ''
        if enabled['http']:
            try:
                return dict(exchange(source, path, body, token), transport='http', fallback_reason='')
            except Unreachable as error:
                if not enabled['filesystem']:
                    raise
                reason = str(error)
        try:
            return dict(filesystem(source, self.store.validator, body, expected), transport='filesystem', fallback_reason=reason)
        except Conflict as error:
            raise SourceRejected(409, str(error)) from error
        except OSError as error:
            raise SourceRejected(503, f'Filesystem operation interrupted; retain the request: {error}') from error
        except ValueError as error:
            raise ValueError(f'Filesystem blocked: {error}' + (f' (fallback: {reason})' if reason else '')) from error

    def source_record(self, body):
        source = next((s for s in self.sources if s['project_id'] == body.get('project_id')), None)
        if source is None:
            raise ValueError('Unknown source project.')
        snapshot = self.inspect_source(source)
        todo = next((t for t in snapshot['data']['todos'] if t['id'] == body.get('todo_id')), None)
        if todo is None:
            raise ValueError('Source todo is unavailable.')
        preflight = preflight_context(snapshot['context'])
        return dict(project_id=source['project_id'], todo=todo, ideas=snapshot['data']['ideas'],
                    revision=snapshot['revisions']['todos'][todo['id']], context=snapshot['context'],
                    compatibility=snapshot['compatibility'], history=snapshot['history'], preflight=preflight,
                    transport=snapshot['transport'], fallback_reason=snapshot['fallback_reason'])

    def source_priority(self, body):
        """Freeze a standard child mutation; receipt retries never rebuild it."""
        source = next((s for s in self.sources if s['project_id'] == body.get('project_id')), None)
        if source is None:
            raise ValueError('Unknown source project.')
        original = body.get('original')
        if not isinstance(original, dict) or original.get('id') != body.get('todo_id') or digest(original) != body.get('revision'):
            raise ValueError('Supply the exact saved original and its record revision.')
        if body.get('priority') not in ('low', 'normal', 'high', 'urgent'):
            raise ValueError('Invalid priority.')
        if not isinstance(body.get('actor'), str) or not body['actor'].strip():
            raise ValueError('Supply the actual editor name.')
        snapshot = self.inspect_source(source)
        expected = preflight_context(snapshot['context'])
        if expected != body.get('preflight'):
            raise ValueError('Source context changed; retain the draft and verify its owner.')
        if snapshot['compatibility'].get('read_only'):
            raise SourceRejected(409, 'Source is read-only; update it before editing.')
        if snapshot['history']['pending']:
            raise SourceRejected(409, 'Source history is pending; finish its local commit before retrying.')
        record = dict(original, priority=body['priority'])
        request = dict(request_id=body.get('request_id'), actor=body['actor'],
                       changes=[dict(collection='todos', id=original['id'], revision=body['revision'], record=record)])
        result = self.transfer(source, '/api/changes', request, snapshot.get('token'), expected)
        self.cache.pop(source['project_id'], None)
        saved = next(t for t in result['data']['todos'] if t['id'] == original['id'])
        return dict(todo=saved, revision=result['revisions']['todos'][original['id']], history=result['history'],
                    project_id=source['project_id'], transport=result['transport'], fallback_reason=result['fallback_reason'])

    def inspect_source(self, source):
        snapshot = self.transfer(source)
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
                                           url=source.get('url', ''), data=snapshot['data'], revision=snapshot['revision'],
                                           transport=snapshot['transport'], fallback_reason=snapshot['fallback_reason'],
                                           revisions=snapshot['revisions'], compatibility=snapshot['compatibility'], history=snapshot['history'],
                                           context=snapshot['context'], checked_at=datetime.now(timezone.utc).isoformat())
                    entry = dict(self.cache[key], status='reachable', error='')
                except (ValueError, KeyError, TypeError) as error:
                    entry = dict(self.cache.get(key, dict(project_id=key, name=Path(source['data']).parent.name,
                                                         url=source.get('url', ''), data=None, checked_at=None)),
                                 status='stale' if key in self.cache else 'unavailable', transport=None, fallback_reason='', error=str(error))
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
                expected = preflight_context(context)
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
                result = self.transfer(source, '/api/changes', state['request'], snapshot.get('token'), expected)
                assigned = next(a['id'] for a in result['assigned'] if a['collection'] == 'todos')
                if result['history']['pending']:
                    raise ValueError('Destination saved; local history pending. Retain claim and retry after history recovery.')
                state = dict(state, status='routed', todo_id=assigned, reason='', completed_at=datetime.now(timezone.utc).isoformat())
                idea = self.save_route(idea, state, actor)
            except (OSError, Conflict, ValueError, KeyError, TypeError, StopIteration) as error:
                # Claims are never erased after an uncertain destination result.
                state = dict(state, status='blocked', project_id=project, reason=str(error))
                idea = self.save_route(idea, state, actor)
            return dict(idea=idea, history=self.store.snapshot()['history'])
