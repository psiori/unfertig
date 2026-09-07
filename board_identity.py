"""Portable fallback identity; keep in sync with Unfertig's board_identity.py."""
import hashlib
import json
import os
from pathlib import Path


def project_identity(settings, data, base):
    """Explicit IDs win; otherwise hash a canonical, root-relative POSIX path."""
    if 'project_id' in settings:
        return settings['project_id']
    data, base = Path(data).resolve(), Path(base).resolve()
    root = None
    for parent in data.parents:
        try:
            node = json.loads((parent / 'node.json').read_text())
        except (OSError, ValueError):
            continue
        if (isinstance(node, dict) and node.get('kind') == 'application'
                and node.get('runtime_root') == 'state/node.json'
                and node.get('id') == 'salange/kermit'):
            root = parent
            break
    if root is None:
        root = next((parent for parent in data.parents if (parent / '.git').exists()), base)
    relative = Path(os.path.relpath(data, root)).as_posix()
    return hashlib.sha256(relative.encode('utf-8')).hexdigest()[:32]
