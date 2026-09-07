import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from board_identity import project_identity


class IdentityTests(unittest.TestCase):
    def test_workspace_move_nested_repositories_and_distinct_boards(self):
        with tempfile.TemporaryDirectory() as folder:
            ids = []
            for name in ('machine-one/kermit', 'machine-two/renamed workspace'):
                root = Path(folder) / name
                board = root / 'companies/acme/project/state/unfertig/data/data.json'
                board.parent.mkdir(parents=True)
                (root / 'node.json').write_text(json.dumps(dict(
                    id='salange/kermit', kind='application', runtime_root='state/node.json')))
                (root / 'companies/acme/project/.git').mkdir()
                base = board.parent.parent / 'config'
                identity = project_identity({}, board, base)
                ids.append(identity)
                expected = hashlib.sha256(b'companies/acme/project/state/unfertig/data/data.json').hexdigest()[:32]
                self.assertEqual(identity, expected)
                self.assertEqual(project_identity({}, board.parent / '../data/data.json', base), identity)
                self.assertNotEqual(project_identity({}, root / 'other/data.json', base), identity)
                self.assertEqual(project_identity({'project_id': 'persistent'}, board, base), 'persistent')
            self.assertEqual(*ids)

    def test_repository_and_config_fallbacks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'state/data.json'
            expected = hashlib.sha256(b'state/data.json').hexdigest()[:32]
            with patch.object(Path, 'exists', return_value=False):
                self.assertEqual(project_identity({}, data, root), expected)
            (root / '.git').write_text('gitdir: elsewhere')
            self.assertEqual(project_identity({}, data, root / 'config'), expected)
            (root / 'node.json').write_text('invalid json')
            self.assertEqual(project_identity({}, data, root / 'config'), expected)

    def test_explicit_invalid_values_are_not_silently_replaced(self):
        for value in ('', None, 42):
            self.assertEqual(project_identity({'project_id': value}, '/unused/data.json', '/unused'), value)
