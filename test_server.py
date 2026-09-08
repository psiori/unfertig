"""Persistence and HTTP boundary checks. Only temporary data is modified."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import Conflict, Server, Store, validate

STAMP = '2026-09-06T12:00:00Z'


def fixture():
    return {
        'schema_version': 1,
        'ideas': [{'id': 'I0001', 'author': 'Human', 'date_entered': STAMP, 'text': 'A rough idea <script> & ü'}],
        'todos': [{
            'id': 'T0001', 'source_ideas': ['I0001'], 'author': 'Human', 'date_entered': STAMP,
            'created_by': 'Codex', 'updated_at': STAMP, 'priority': 'normal', 'group': 'City',
            'name': 'Implement a small step', 'description': 'A clear result.', 'tags': ['visuals'],
            'status': 'open', 'closed_by': '', 'date_closed': '', 'pr_url': '', 'commit_url': '', 'commit_hash': '',
        }],
    }


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='unfertig-tests-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'data.json'
        self.path.write_text(json.dumps(fixture()), encoding='utf-8')
        self.store = Store(self.path)

    def test_save_and_restart_preserve_unicode(self):
        data, revision = self.store.read()
        data['todos'][0]['description'] = 'City — Grüße 🌱\nSecond line.'
        new_revision = self.store.save(data, revision)
        reopened, actual_revision = Store(self.path).read()
        self.assertEqual(data, reopened)
        self.assertEqual(new_revision, actual_revision)
        self.assertNotEqual(revision, actual_revision)
        self.assertTrue(self.path.read_text().endswith('\n'))
        self.assertEqual(list(Path(self.temp.name).glob('.data-*.tmp')), [])

    def test_stale_writer_cannot_overwrite_newer_save(self):
        first, revision = self.store.read()
        second = copy.deepcopy(first)
        first['todos'][0]['name'] = 'First change'
        self.store.save(first, revision)
        second['todos'][0]['name'] = 'Stale change'
        with self.assertRaises(Conflict):
            self.store.save(second, revision)
        self.assertEqual(self.store.read()[0]['todos'][0]['name'], 'First change')

    def test_external_edit_detected(self):
        data, revision = self.store.read()
        changed = copy.deepcopy(data)
        changed['todos'][0]['group'] = 'Rendering'
        self.path.write_text(json.dumps(changed))
        with self.assertRaises(Conflict):
            self.store.save(data, revision)
        self.assertEqual(self.store.read()[0], changed)

    def test_concurrent_writers_only_one_succeeds(self):
        data, revision = self.store.read()
        barrier = threading.Barrier(2)
        results = []
        def write(name):
            draft = copy.deepcopy(data)
            draft['todos'][0]['name'] = name
            barrier.wait()
            try:
                self.store.save(draft, revision)
                results.append('saved')
            except Conflict:
                results.append('conflict')
        threads = [threading.Thread(target=write, args=(name,)) for name in ('One', 'Two')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(results, ['saved', 'conflict'])

    def test_original_idea_and_attribution_immutable(self):
        for collection, key in [('ideas','text'), ('ideas','author'), ('todos','created_by'), ('todos','author')]:
            with self.subTest(collection=collection, key=key):
                data, revision = self.store.read()
                original = self.path.read_bytes()
                data[collection][0][key] = 'Replaced'
                with self.assertRaises(ValueError):
                    self.store.save(data, revision)
                self.assertEqual(original, self.path.read_bytes())

    def test_no_deletions_or_dangling_references(self):
        data, revision = self.store.read()
        data['todos'] = []
        with self.assertRaises(ValueError):
            self.store.save(data, revision)
        data = fixture()
        data['todos'][0]['source_ideas'] = ['I9999']
        with self.assertRaises(ValueError):
            validate(data)

    def test_closure_and_reopen_consistency(self):
        data = fixture()
        todo = data['todos'][0]
        todo['status'] = 'closed'
        with self.assertRaises(ValueError):
            validate(data)
        todo['closed_by'] = 'Claude'
        todo['date_closed'] = STAMP
        validate(data)
        todo['status'] = 'started'
        with self.assertRaises(ValueError):
            validate(data)
        todo['closed_by'] = todo['date_closed'] = ''
        validate(data)

    def test_bad_values_rejected(self):
        invalid = {
            'priority': 'critical', 'status': 'done', 'commit_hash': 'not-a-hash',
            'pr_url': 'javascript:alert(1)', 'commit_url': 'http://example.com',
            'name': '   ', 'tags': ['duplicate', 'duplicate'], 'source_ideas': ['I0001','I0001'],
            'updated_at': '2026-09-06T12:00:00',
        }
        for key, value in invalid.items():
            with self.subTest(key=key):
                data = fixture()
                data['todos'][0][key] = value
                with self.assertRaises(ValueError):
                    validate(data)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='unfertig-http-')
        self.path = Path(self.temp.name) / 'data.json'
        self.path.write_text(json.dumps(fixture()))
        self.server = Server(('127.0.0.1', 0), Store(self.path))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path='/api/state', data=None, headers=None):
        request = Request(self.url + path, data=json.dumps(data).encode() if data is not None else None,
                          headers=headers or {}, method='PUT' if data is not None else 'GET')
        try:
            with urlopen(request) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def test_header_renders_startup_application_build(self):
        from server import APPLICATION_BUILD
        for path in ('/', '/index.html'):
            status, raw, headers = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(('build ' + APPLICATION_BUILD).encode(), raw)
            self.assertNotIn(b'__APPLICATION_BUILD__', raw)
            self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_category_definitions_are_served_before_app(self):
        from categories import DEFINITIONS
        status, raw, headers = self.request('/categories-data.js')
        self.assertEqual(status, 200)
        self.assertIn('text/javascript', headers['Content-Type'])
        self.assertEqual(json.loads(raw.decode().removeprefix('const categoryDefinitions = ').removesuffix(';')), DEFINITIONS)
        _, html, _ = self.request('/')
        self.assertLess(html.index(b'/categories-data.js'), html.index(b'/app.js'))

    def test_full_api_save_and_conflict(self):
        status, raw, _ = self.request()
        self.assertEqual(status, 200)
        state = json.loads(raw)
        state['data']['todos'][0]['status'] = 'started'
        headers = {'X-Board-Token':state['token'],'Content-Type':'application/json'}
        status, _, _ = self.request(data=state, headers=headers)
        self.assertEqual(status, 200)
        status, _, _ = self.request(data=state, headers=headers)
        self.assertEqual(status, 409)
        self.assertEqual(Store(self.path).read()[0]['todos'][0]['status'], 'started')

    def test_boundary_and_file_allowlist(self):
        state = json.loads(self.request()[1])
        self.assertEqual(self.request(data=state)[0], 403)
        headers = {'X-Board-Token':state['token'],'Origin':'https://other.example'}
        self.assertEqual(self.request(data=state,headers=headers)[0], 403)
        self.assertEqual(self.request(headers={'Host':'other.example'})[0], 403)
        for path in ['/data.json','/PROCESS.md','/../CLAUDE.md','/.git/config','/server.py']:
            self.assertEqual(self.request(path)[0], 404)
        for path in ['/','/app.js','/style.css','/favicon.svg']:
            status, raw, headers = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(raw)
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_invalid_write_and_corrupt_file_preserved(self):
        state = json.loads(self.request()[1])
        original = self.path.read_bytes()
        state['data']['todos'][0]['priority'] = 'invalid'
        self.assertEqual(self.request(data=state, headers={'X-Board-Token':state['token']})[0], 400)
        self.assertEqual(original, self.path.read_bytes())
        self.path.write_text('{broken')
        self.assertEqual(self.request()[0], 500)
        self.assertEqual(self.path.read_text(), '{broken')


if __name__ == '__main__':
    unittest.main()
