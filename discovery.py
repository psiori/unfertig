"""Bounded, read-only config discovery. No BoardStore or service startup here."""
import json
import os
from pathlib import Path
import re

from configuration import normalize_sources, resolve
from versions import inspect

MAX_SOURCES = 20
MAX_ENTRIES = 10000
MAX_MATCHES = 200


def search_paths(values, base):
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError('search_paths must be a list of at most 20 config patterns.')
    result = []
    for value in values:
        if (not isinstance(value, str) or not value.strip() or '**' in value
                or any(c in value for c in '[]\x00') or not value.endswith('.json')):
            raise ValueError('Search paths target config JSON files; only * and ? wildcards are supported (no ** or brackets).')
        # Do not resolve wildcard components or collapse .. across a wildcard.
        path = Path(base) / value
        if len(path.parts) > 64:
            raise ValueError('Search paths support at most 64 path components.')
        result.append(str(path))
    return result


def matches(pattern, budget):
    """Follow only the finite components requested, including directory symlinks."""
    parts = Path(pattern).parts
    def walk(parent, remaining):
        if not remaining:
            if parent.exists():
                budget[1] += 1
                if budget[1] > MAX_MATCHES:
                    raise ValueError('Discovery exceeds 200 matches; narrow search_paths.')
                yield parent.resolve()
            return
        component, *tail = remaining
        if '*' not in component and '?' not in component:
            yield from walk(parent / component, tail)
            return
        expression = re.compile('^' + ''.join('.*' if c == '*' else '.' if c == '?' else re.escape(c) for c in component) + '$')
        try:
            with os.scandir(parent) as entries:
                selected = []
                for entry in entries:
                    budget[0] += 1
                    if budget[0] > MAX_ENTRIES:
                        raise ValueError('Discovery exceeds 10000 directory entries; narrow search_paths.')
                    if entry.name.startswith('.') and not component.startswith('.'):
                        continue
                    if expression.fullmatch(entry.name):
                        selected.append(entry.name)
            for name in sorted(selected):
                yield from walk(parent / name, tail)
        except (FileNotFoundError, NotADirectoryError):
            return
    yield from walk(Path(parts[0]), parts[1:])


def source_from_config(path, context):
    if not path.is_file():
        raise ValueError('Matched config must be a regular JSON file.')
    with path.open('rb') as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('Matched config exceeds 1 MB.')
    settings = json.loads(raw)
    if inspect(settings, str(path))[0] == 'read_only':
        raise ValueError('Matched config requires a newer reader.')
    if settings.get('mode') == 'aggregation':
        raise ValueError('Nested aggregators are not supported.')
    metadata = settings.get('aggregation_source')
    if not isinstance(metadata, dict) or not isinstance(metadata.get('app_root'), str) or not metadata['app_root'].strip():
        raise ValueError('Matched config requires aggregation_source.app_root and transport service metadata; a data file is not a source config.')
    if not settings.get('project_id') or not settings.get('data'):
        raise ValueError('Matched config requires explicit data and stable project_id.')
    app = (path.parent / metadata['app_root']).resolve()
    if not (app / 'PROCESS.md').is_file():
        raise ValueError('Matched app_root must contain PROCESS.md.')
    child = resolve(app, config=path)
    if not child['path'].is_file():
        raise ValueError('Matched board data is missing; discovery never creates boards.')
    return normalize_sources([dict(data=str(child['path']), project_id=child['project_id'],
                                   config=str(path), app_root=str(app), url=metadata.get('url', ''))],
                             path.parent, Path(context['data']), context['project_id'], context['transports'])[0]


def compatible(left, right):
    # Optional filesystem metadata may enrich an exact HTTP-only source.
    return all(left[k] == right[k] for k in left.keys() & right.keys())


def discover(context, explicit):
    sources = [dict(s) for s in explicit]
    reports, candidates, budget = [], {}, [0, 0]
    for pattern in context.get('search_paths', []):
        try:
            found = list(matches(pattern, budget))
            reports.append(dict(pattern=pattern, status='matched' if found else 'unmatched',
                                matches=len(found), error='' if found else 'No existing config matches.'))
            for path in found:
                candidates.setdefault(path, pattern)
        except (OSError, ValueError, RuntimeError) as error:
            # Fail the whole scan closed: never apply a truncated membership set.
            return None, [dict(pattern=pattern, status='invalid', error=str(error))]
    invalid = set()
    for path, pattern in sorted(candidates.items()):
        try:
            source = source_from_config(path, context)
            prior = next((s for s in sources if s['data'] == source['data'] or s['project_id'] == source['project_id']), None)
            if prior:
                if not compatible(prior, source):
                    invalid.add(prior['project_id'])
                    raise ValueError('Conflicting source identity or metadata; all conflicting matches are blocked.')
                prior.update(source)
            else:
                sources.append(source)
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            reports.append(dict(pattern=pattern, config=str(path), status='invalid', error=str(error)))
    if len(sources) > MAX_SOURCES:
        return None, reports + [dict(status='invalid', error='Discovery exceeds 20 unique sources; narrow search_paths.')]
    return [s for s in sources if s['project_id'] not in invalid], reports
