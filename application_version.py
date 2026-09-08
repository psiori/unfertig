"""Authoritative application build identity; independent of board/API versions."""
import hashlib
from pathlib import Path


def application_build(root: Path) -> str:
    """Fingerprint shipped runtime files, identically in Git and source archives."""
    digest = hashlib.sha256()
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if (not path.is_file() or path.name.startswith('test_')
                or (path.suffix not in {'.py', '.js', '.css', '.html', '.svg'}
                    and path.name not in {'agent_advice.json', 'categories.json', 'efforts.json'})):
            continue
        payload = path.read_bytes()
        digest.update(path.name.encode('utf-8') + b'\0')
        digest.update(str(len(payload)).encode('ascii') + b'\0' + payload)
    return digest.hexdigest()[:12]
