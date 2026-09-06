"""Resolve one board and its owning repository independently of launch directory."""
import json
from pathlib import Path
import subprocess


def git_root(path):
    path = Path(path).resolve()
    while not path.exists():
        if path == path.parent:
            raise ValueError('No existing parent for board path.')
        path = path.parent
    if path.is_file():
        path = path.parent
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'Board must belong to a Git repository: {path}')
    return Path(result.stdout.strip()).resolve()


def resolve(app_root, config=None, data=None, no_git=False):
    app_root = Path(app_root).resolve()
    selected = Path(config).resolve() if config else None
    if selected is None and data is None:
        result = subprocess.run(['git', '-C', str(app_root), 'rev-parse', '--show-superproject-working-tree'], capture_output=True, text=True)
        parent = result.stdout.strip() if result.returncode == 0 else ''
        if parent:
            selected = Path(parent) / 'unfertig.json'
            if not selected.is_file():
                raise ValueError(f'Embedded app requires {selected}; copy the parent configuration example.')
        elif (app_root / 'unfertig.json').is_file():
            selected = app_root / 'unfertig.json'
    settings = json.loads(selected.read_text()) if selected else {}
    if not isinstance(settings, dict) or set(settings) - {'data', 'mode', 'repository'}:
        raise ValueError('Configuration supports only data, mode, and repository.')
    base = selected.parent if selected else app_root
    mode = settings.get('mode', 'standalone')
    if mode not in ('standalone', 'embedded'):
        raise ValueError('mode must be standalone or embedded.')
    value = str(data) if data is not None else settings.get('data', 'board/data.json')
    if not isinstance(value, str) or not value.strip():
        raise ValueError('data must be a nonempty file path.')
    path = (base / value).resolve()
    if path.suffix != '.json' or path.is_dir():
        raise ValueError('data must name a JSON file.')
    owner = git_root(path) if not no_git else None
    if mode == 'embedded':
        if path.is_relative_to(app_root):
            raise ValueError('Embedded board data must be outside the app checkout.')
        expected = (base / settings.get('repository', '.')).resolve()
        if owner and owner != expected:
            raise ValueError(f'Board owner {owner} does not match configured parent {expected}.')
    elif selected is None and data is None and owner and owner != git_root(app_root):
        raise ValueError('Default board belongs to an unexpected repository.')
    return dict(path=path, repository=owner, mode=mode, config=selected,
                bootstrap=selected is None and data is None and mode == 'standalone')
