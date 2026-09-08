import tempfile
import unittest
from pathlib import Path
from application_version import application_build


class ApplicationBuildTests(unittest.TestCase):
    def test_reproducible_archive_and_updated_runtime(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a, b = Path(first), Path(second)
            for root in (a, b):
                (root / 'server.py').write_text('server')
                (root / 'app.js').write_text('client')
            initial = application_build(a)
            self.assertRegex(initial, r'^[0-9a-f]{12}$')
            self.assertEqual(initial, application_build(b))
            (a / 'board').mkdir()
            (a / 'board/data.json').write_text('private board content')
            (a / 'test_extra.py').write_text('test')
            (a / 'README.md').write_text('documentation')
            (a / '.git').write_text('worktree metadata')
            (a / 'config.json').write_text('private local configuration')
            self.assertEqual(initial, application_build(a))
            for filename in ('server.py', 'app.js', 'style.css', 'index.html', 'categories.json', 'favicon.svg'):
                previous = application_build(a)
                (a / filename).write_text('updated runtime')
                self.assertNotEqual(previous, application_build(a), filename)
