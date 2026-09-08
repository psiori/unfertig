"""Config discovery only touches disposable repositories and boards."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from configuration import board_context, resolve
from discovery import discover, matches, search_paths
from versions import FORMAT_VERSION


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='unfertig-discovery-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = dict(data=str(self.root / 'inbox.json'), project_id='inbox',
                            transports=dict(http=True, filesystem=False), search_paths=[])

    def child(self, name='um-a', **overrides):
        root = self.root / name
        root.mkdir(exist_ok=True)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        (root / 'PROCESS.md').write_text('Disposable process.\n')
        (root / 'data.json').write_text('{}\n')
        value = dict(format_version=FORMAT_VERSION, project_id=name, data='data.json',
                     aggregation_source=dict(app_root='.', url='http://127.0.0.1:8765'))
        value.update(overrides)
        path = root / 'config.json'
        path.write_text(json.dumps(value))
        return path

    def scan(self, patterns, explicit=()):
        self.context['search_paths'] = search_paths(patterns, self.root)
        return discover(self.context, explicit)

    def test_star_question_literal_added_removed_and_config_relative(self):
        a = self.child(); self.child('um-long')
        sources, reports = self.scan(['um-?/config.json', 'absent*/config.json'])
        self.assertEqual([s['project_id'] for s in sources], ['um-a'])
        self.assertEqual(reports[1]['status'], 'unmatched')
        self.child('um-b')
        self.assertEqual(len(self.scan(['um-?/config.json'])[0]), 2)
        a.unlink()
        self.assertEqual([s['project_id'] for s in self.scan(['um-?/config.json'])[0]], ['um-b'])
        self.assertEqual(len(self.scan(['um-*/config.json'])[0]), 2)
        self.assertEqual(len(self.scan(['um-b/config.json'])[0]), 1)
        self.assertEqual(self.scan(['um-a/config.json'])[1][0]['status'], 'unmatched')

    def test_symlink_dedup_and_explicit_metadata_enrichment(self):
        config = self.child()
        (self.root / 'alias').symlink_to(config.parent, target_is_directory=True)
        source = self.scan(['um-a/config.json'])[0][0]
        explicit = {k: v for k, v in source.items() if k not in ('config', 'app_root')}
        sources, reports = self.scan(['*/config.json', 'alias/config.json'], [explicit])
        self.assertEqual(sources, [source])
        self.assertFalse(any(r['status'] == 'invalid' for r in reports))
        # A finite path may intentionally follow a symlink outside its prefix.
        (config.parent / 'loop').symlink_to(config.parent, target_is_directory=True)
        self.assertEqual(len(self.scan(['um-a/loop/loop/config.json'])[0]), 1)

    def test_invalid_identity_url_nested_self_missing_metadata_and_data(self):
        variants = [dict(project_id='bad id'), dict(project_id='inbox'), dict(mode='aggregation'),
                    dict(aggregation_source={}), dict(data='missing.json'),
                    dict(aggregation_source=dict(app_root='.', url='https://example.com:8765')),
                    dict(aggregation_source=dict(app_root='.', url='http://127.0.0.1:8765/path')),
                    dict(aggregation_source=dict(app_root='.', url='http://user@127.0.0.1:8765'))]
        for value in variants:
            with self.subTest(value=value):
                self.child(**value)
                sources, reports = self.scan(['um-a/config.json'])
                self.assertEqual(sources, [])
                self.assertEqual(reports[-1]['status'], 'invalid')
        self.child()
        self.context['data'] = str(self.root / 'um-a/data.json')
        self.assertEqual(self.scan(['um-a/config.json'])[0], [])

    def test_conflicting_ids_block_both_and_no_board_writes(self):
        self.child('um-a', project_id='same'); self.child('um-b', project_id='same')
        before = {p: p.read_bytes() for p in self.root.glob('*/*.json')}
        sources, reports = self.scan(['*/config.json'])
        self.assertEqual(sources, [])
        self.assertIn('Conflicting', reports[-1]['error'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.glob('*/*.json')})
        self.assertFalse(list(self.root.glob('*/.server.lock')))

    def test_bounds_hidden_entries_and_unsupported_patterns(self):
        self.child('.hidden', project_id='hidden'); self.child('um-a')
        self.assertEqual(len(self.scan(['*/config.json'])[0]), 1)
        self.assertEqual(len(self.scan(['.*/config.json'])[0]), 1)
        for patterns in (['**/config.json'], ['um-[ab]/config.json'], ['*.txt'], ['x.json'] * 21, 'x'):
            with self.assertRaises(ValueError): search_paths(patterns, self.root)
        with patch('discovery.MAX_ENTRIES', 1):
            self.assertIsNone(self.scan(['*/config.json'])[0])
        with patch('discovery.MAX_MATCHES', 0):
            self.assertIsNone(self.scan(['um-a/config.json'])[0])
        with patch('discovery.MAX_SOURCES', 0):
            self.assertIsNone(self.scan(['um-a/config.json'])[0])

    def test_resolver_validates_patterns_and_exposes_context_without_scanning(self):
        path = self.child(mode='aggregation', search_paths=['../um-?/config.json'])
        with patch('discovery.discover', side_effect=AssertionError('not at config resolution')):
            config = resolve(path.parent, config=path)
        context = board_context(config, path.parent)
        self.assertEqual(context['search_paths'], [str(path.parent / '../um-?/config.json')])
        self.assertEqual(context['sources'], [])

    def test_filesystem_metadata_url_optional_but_validated_when_present(self):
        self.child(aggregation_source=dict(app_root='.'))
        self.assertEqual(self.scan(['*/config.json'])[0], [])
        self.context['transports'] = dict(http=False, filesystem=True)
        self.assertEqual(len(self.scan(['*/config.json'])[0]), 1)


if __name__ == '__main__':
    unittest.main()
