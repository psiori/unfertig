"""Portable project declarations. Vendored unchanged by Kermit; no I/O on import."""
from pathlib import Path
import re
import uuid

RESERVED = {'tools', 'state', 'ruleset', 'design', 'decisions', 'sources', 'context',
            'scripts', 'node.json', 'project-manifest.json'}
WINDOWS_RESERVED = {'con', 'prn', 'aux', 'nul'} | {
    f'{prefix}{i}' for prefix in ('com', 'lpt') for i in range(1, 10)}


def safe_path(value):
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError('Project path must be a nonempty relative path.')
    parts = value.split('/')
    if any(not re.fullmatch(r'[a-z0-9]+(?:[-_][a-z0-9]+)*', p)
           or p in WINDOWS_RESERVED for p in parts) or parts[0] in RESERVED:
        raise ValueError(f'Unsafe or reserved project path: {value!r}')
    return value


def repository_name(spec):
    value = spec.get('repository')
    if isinstance(value, dict):
        if value.get('visibility') not in ('private', 'public', 'internal', 'undecided'):
            raise ValueError('Declare repository visibility explicitly.')
        value = f"{value.get('owner', '')}/{value.get('name', '')}"
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*', value):
        raise ValueError('Expected repository owner/name.')
    return value


def projects(node):
    """Read legacy singular metadata or v2, never silently choose between both."""
    if 'projects' not in node:
        value = node.get('project')
        return [dict(value)] if isinstance(value, dict) and value else []
    if 'project' in node:
        raise ValueError('Declare project or projects, never both.')
    if node.get('schema_version') != 2:
        raise ValueError('A projects collection requires schema_version 2.')
    values = node['projects']
    if not isinstance(values, list) or not values:
        raise ValueError('projects must be a nonempty list.')
    paths, ids, repos = [], set(), set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('Each project must be an object.')
        ident = item.get('id')
        try:
            if str(uuid.UUID(ident)) != ident:
                raise ValueError('Project IDs must be canonical UUIDs.')
        except (TypeError, AttributeError):
            raise ValueError('Project IDs must be canonical UUIDs.') from None
        if ident in ids or ident == node.get('id'):
            raise ValueError('Context and project IDs must be distinct and unique.')
        ids.add(ident)
        if not isinstance(item.get('name'), str) or not re.fullmatch(r'[a-z0-9]+(?:[-_][a-z0-9]+)*', item['name']):
            raise ValueError('Each project needs a safe name.')
        path = safe_path(item.get('path'))
        if any(path == p or path.startswith(p + '/') or p.startswith(path + '/') for p in paths):
            raise ValueError('Project paths must be unique and must not overlap.')
        paths.append(path)
        repo = repository_name(item).lower()
        if repo in repos:
            raise ValueError('Declare each deliverable repository only once.')
        repos.add(repo)
        if item.get('mount') != 'submodule':
            raise ValueError('Collection projects must be pinned submodules.')
    return [dict(item) for item in values]


def project_path(root, spec):
    relative = safe_path(spec.get('path'))
    path = Path(root)
    for part in relative.split('/'):
        path /= part
        if path.is_symlink():
            raise ValueError(f'Project path crosses a symlink: {relative}')
    return path
