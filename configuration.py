"""Resolve one board and its owning repository independently of launch directory."""
import json
import os
from pathlib import Path
import subprocess
import socket
import errno

DEFAULT_PORT = 8765
PORT_RANGE = range(8765, 8800)

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
    settings["port"] = choose_port(configuration["port"])
    if (path.read_bytes() if path.exists() else None) != before:
        raise ValueError("Configuration changed while choosing a port; rerun configuration.")
    from storage import atomic, encode
    atomic(path, encode(settings))
    print(f"Saved port {settings["port"]} in {path}. Commit this configuration in its owning repository.")


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
    if not isinstance(settings, dict) or set(settings) - {'data', 'mode', 'repository', 'port'}:
        raise ValueError('Configuration supports only data, mode, repository, and port.')
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
                port=valid_port(settings.get("port", DEFAULT_PORT)),
                bootstrap=data is None and mode == 'standalone' and (selected is None or
                    (selected == app_root / 'unfertig.json' and 'data' not in settings)))
