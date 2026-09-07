"""Host-local Codex discovery; never persist discovered machine paths."""
from pathlib import Path
import shutil
import sys


def resolve_executable(configured='codex'):
    """Prefer a usable configured executable, then standard host installations."""
    home = Path.home()
    candidates = [str(Path(configured).expanduser()), 'codex',
                  str(home / '.local/bin/codex'), str(home / '.npm-global/bin/codex'),
                  str(home / '.local/share/mise/shims/codex'),
                  str(home / '.local/share/mise/installs/codex/latest/bin/codex'),
                  '/usr/local/bin/codex', '/usr/bin/codex', '/opt/homebrew/bin/codex']
    if sys.platform == 'darwin':
        for base in (Path('/Applications'), home / 'Applications'):
            for app in ('Codex.app', 'ChatGPT.app'):
                candidates.append(str(base / app / 'Contents/Resources/codex'))
    elif sys.platform.startswith('linux'):
        candidates.extend(['/opt/codex/resources/codex', '/opt/Codex/resources/codex',
                           str(home / '.local/share/codex/resources/codex')])
    for candidate in dict.fromkeys(candidates):
        executable = shutil.which(candidate)
        if executable:
            return executable
    return None
