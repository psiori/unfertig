"""Resolve one board and its owning repository independently of launch directory."""
import json
import os
from pathlib import Path
import subprocess
import socket
import errno
import re
from board_identity import project_identity
from urllib.parse import urlsplit
from versions import inspect, migrate, VersionError

DEFAULT_PORT = 8765
PORT_RANGE = range(8765, 8800)

def transports(value):
    if not isinstance(value, dict) or set(value) != {'http', 'filesystem'} or any(type(v) is not bool for v in value.values()):
        raise ValueError('transports requires exactly boolean http and filesystem fields.')
    if not any(value.values()):
        raise ValueError('At least one aggregation transport must be enabled.')
    return dict(value)


def board_context(configuration, app_root):
    app_root = Path(app_root).resolve()
    path = configuration['path']
    return dict(config=str(configuration['config'] or ''), app_root=str(app_root),
                process=str(app_root / 'PROCESS.md'), data=str(path), todos=str(path.parent / 'todos'),
                repository=str(configuration['repository'] or ''), mode=configuration['mode'],
                project_name=configuration['project_name'], project_id=configuration['project_id'],
                sources=configuration['sources'], search_paths=configuration.get('search_paths', []),
                transports=configuration.get('transports', {'http': True, 'filesystem': False}), processing=configuration['processing'], workflow=configuration['workflow'])

def valid_port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("Port must be an integer from 1 to 65535.")
    return value

def port_occupied(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                return True
            raise
    return False

def choose_port(current=DEFAULT_PORT):
    occupied = [p for p in PORT_RANGE if port_occupied(p)]
    print(f"Default port: {DEFAULT_PORT}; suggested unfertig range: {PORT_RANGE.start}–{PORT_RANGE.stop - 1}")
    print("Currently occupied in this range: " + (", ".join(map(str, occupied)) or "none"))
    print("Availability is checked now; another process can claim a port before startup.")
    while True:
        try:
            value = input(f"Port [{current}]: ").strip()
            port = valid_port(int(value) if value else current)
            if port_occupied(port):
                print(f"Port {port} is occupied. Choose another.")
                continue
            return port
        except ValueError:
            print("Enter an integer from 1 to 65535.")

def configure_port(configuration, app_root):
    # Called under the board writer lock; preserve all existing config fields.
    path = configuration["config"] or Path(app_root) / "unfertig.json"
    before = path.read_bytes() if path.exists() else None
    settings = json.loads(before) if before is not None else {}
    if inspect(settings, str(path))[0] == 'read_only':
        raise VersionError('Newer config version is read-only; update Unfertig before configuring.')
    settings = migrate(settings, 'config', str(path))
    settings["port"] = choose_port(configuration["port"])
    if (path.read_bytes() if path.exists() else None) != before:
        raise ValueError("Configuration changed while choosing a port; rerun configuration.")
    from storage import atomic, encode
    atomic(path, encode(settings))
    print(f"Saved port {settings["port"]} in {path}. Commit this configuration in its owning repository.")


def configure_mode(configuration, app_root):
    """Explicit setup, no discovery of services or directory recursion."""
    path = configuration['config'] or Path(app_root) / 'unfertig.json'
    before = path.read_bytes() if path.exists() else None
    settings = migrate(json.loads(before) if before else {}, 'config')
    mode = input('Mode (standalone / embedded / aggregation): ').strip()
    if mode not in ('standalone', 'embedded', 'aggregation'):
        raise ValueError('Choose standalone, embedded or aggregation.')
    settings['mode'] = mode
    if mode == 'aggregation':
        settings['transports'] = transports(json.loads(input('Transports JSON {"http":true,"filesystem":false}: ')))
        print('Sources: JSON array with data and project_id; HTTP requires url; filesystem requires config and app_root. Paths are relative to this config. See README.md for examples.')
        settings['sources'] = json.loads(input('Sources: '))
    else:
        settings.pop('sources', None)
        settings.pop('search_paths', None)
    if (path.read_bytes() if path.exists() else None) != before:
        raise ValueError('Configuration changed during setup; rerun.')
    # Validate the proposed file using a sibling temporary file so paths retain
    # their config-relative meaning; never replace an invalid configuration.
    import tempfile
    from storage import atomic, encode
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.json') as check:
        check.write(encode(settings)); check.flush()
        resolve(app_root, config=check.name, no_git=configuration['repository'] is None)
    atomic(path, encode(settings))
    print(f'Saved mode in {path}. Commit the configuration in its owning repository; restart to apply.')


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


def discover_project_name(app_root):
    """Inspect the installation's host, never the board or launch directory."""
    app_root = Path(app_root).resolve()
    result = subprocess.run(['git', '-C', str(app_root), 'rev-parse',
                             '--show-superproject-working-tree'], capture_output=True, text=True)
    superproject = result.stdout.strip() if result.returncode == 0 else ''
    host = Path(superproject).resolve() if superproject else None
    # Stop at the nearest enclosing repository; unrelated outer wrappers do not
    # identify an installation nested inside a different project.
    for parent in app_root.parents:
        metadata = parent / 'node.json'
        if metadata.is_file():
            try:
                node = json.loads(metadata.read_text())
            except (OSError, ValueError):
                node = {}
            if isinstance(node, dict) and node.get('kind') == 'project-wrapper':
                name = node.get('name')
                return name.strip() if isinstance(name, str) else ''
        if parent == host or (parent / '.git').exists():
            break
    return host.name if host else ''


def resolve(app_root, config=None, data=None, no_git=False, state_dir=None):
    app_root = Path(app_root).resolve()
    # CLI selectors override environment selectors; config wins within each tier.
    if config is None and state_dir is None:
        config = os.environ.get("UNFERTIG_CONFIG")
        state_dir = os.environ.get("UNFERTIG_STATE_DIR") if not config else None
    selected = Path(config).resolve() if config else None
    if selected is None and state_dir is not None:
        selected = Path(state_dir).resolve() / "config/config.json"
    if selected is None and data is None:
        result = subprocess.run(['git', '-C', str(app_root), 'rev-parse', '--show-superproject-working-tree'], capture_output=True, text=True)
        parent = result.stdout.strip() if result.returncode == 0 else ''
        if parent:
            selected = Path(parent) / 'state/unfertig/config/config.json'
            if not selected.is_file():
                raise ValueError(f'Embedded app requires {selected}; copy the parent configuration example.')
        elif (app_root / 'unfertig.json').is_file():
            selected = app_root / 'unfertig.json'
    settings = json.loads(selected.read_text()) if selected else {}
    inspect(settings, str(selected or 'configuration'))
    # Unrecognized extension fields survive configuration edits and migrations.
    settings = migrate(settings, 'config', str(selected or 'configuration'))
    if 'project_name' in settings and not isinstance(settings['project_name'], str):
        raise ValueError('project_name must be a string (empty suppresses the project title).')
    project_name = settings['project_name'].strip() if 'project_name' in settings else discover_project_name(app_root)
    base = selected.parent if selected else app_root
    mode = settings.get('mode', 'standalone')
    if mode not in ('standalone', 'embedded', 'aggregation'):
        raise ValueError('mode must be standalone, embedded or aggregation.')
    value = str(data) if data is not None else settings.get('data', 'board/data.json')
    if not isinstance(value, str) or not value.strip():
        raise ValueError('data must be a nonempty file path.')
    path = (base / value).resolve()
    if path.suffix != '.json' or path.is_dir():
        raise ValueError('data must name a JSON file.')
    owner = git_root(path) if not no_git else None
    if selected and owner and git_root(selected) != owner:
        raise ValueError('Configuration and board must belong to the same repository.')
    if mode == 'embedded':
        if path.is_relative_to(app_root):
            raise ValueError('Embedded board data must be outside the app checkout.')
        expected = (base / settings.get('repository', '.')).resolve()
        if owner and owner != expected:
            raise ValueError(f'Board owner {owner} does not match configured parent {expected}.')
    elif selected is None and data is None and owner and owner != git_root(app_root):
        raise ValueError('Default board belongs to an unexpected repository.')
    project_id = project_identity(settings, path, base)
    if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', project_id):
        raise ValueError('project_id must contain 1–100 letters, digits, underscores or hyphens.')
    enabled = transports(settings.get('transports', {'http': True, 'filesystem': False}))
    sources = normalize_sources(settings.get('sources', []), base, path, project_id, enabled)
    from discovery import search_paths
    patterns = search_paths(settings.get('search_paths', []), base)
    if (sources or patterns) and mode != 'aggregation':
        raise ValueError('Sources require aggregation mode.')
    from processing import settings as processing_settings
    processing = processing_settings(settings.get('processing', {}), base, app_root, owner)
    from workflow import settings as workflow_settings
    workflow = workflow_settings(settings.get('workflow', {}), base, processing, mode)
    return dict(workflow=workflow, processing=processing, path=path, repository=owner, mode=mode, config=selected, project_name=project_name,
                project_id=project_id, sources=sources, search_paths=patterns, transports=enabled,
                port=valid_port(settings.get("port", DEFAULT_PORT)),
                bootstrap=data is None and mode == 'standalone' and (selected is None or
                    (selected == app_root / 'unfertig.json' and 'data' not in settings)))


def normalize_sources(values, base, path, project_id, enabled):
    """Shared exact-source validation for configuration and discovery."""
    sources, paths, identities = [], set(), set()
    if not isinstance(values, list):
        raise ValueError('sources must be an explicit list.')
    if len(values) > 20:
        raise ValueError('At most 20 explicit sources are supported.')
    for source in values:
        if not isinstance(source, dict) or not all(isinstance(source.get(k), str) and source[k] for k in ('data', 'project_id')):
            raise ValueError('Each source requires data (JSON file) and project_id.')
        if 'transports' in source:
            raise ValueError('Transport enablement is global; source overrides are not supported.')
        if any(any(c in source.get(field, '') for c in '*?[]') for field in ('data', 'config', 'app_root') if isinstance(source.get(field, ''), str)):
            raise ValueError('Exact source paths cannot contain wildcards; use search_paths for config discovery.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', source['project_id']):
            raise ValueError('Invalid source project_id.')
        location = (base / source['data']).resolve()
        url = source.get('url', '')
        if not isinstance(url, str) or (enabled['http'] and not url):
            raise ValueError('HTTP-enabled sources require a URL.')
        if url:
            parsed = urlsplit(url)
            if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or not parsed.port or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError('Source URL must be an explicit loopback HTTP origin with a port.')
        normalized = dict(data=str(location), url=url.rstrip('/'), project_id=source['project_id'], transports=enabled)
        for field in ('config', 'app_root'):
            if enabled['filesystem'] and (not isinstance(source.get(field), str) or not source[field].strip()):
                raise ValueError('Filesystem sources require explicit config and app_root paths.')
            if source.get(field):
                normalized[field] = str((base / source[field]).resolve())
        if normalized.get('config') and Path(normalized['config']).suffix != '.json':
            raise ValueError('Source config must name a JSON file.')
        if location == path or source['project_id'] == project_id:
            raise ValueError('An aggregator cannot include itself.')
        if location.suffix != '.json':
            raise ValueError('Source data must name a JSON file.')
        if location in paths:
            prior = next(s for s in sources if s['data'] == str(location))
            if prior != normalized:
                raise ValueError('Duplicate source has conflicting metadata.')
            continue
        if source['project_id'] in identities:
            raise ValueError('Distinct boards cannot share a configured project_id.')
        paths.add(location); identities.add(source['project_id'])
        sources.append(normalized)
    return sources
