"""Settings conformance on disposable owned repositories only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_workflow
from configuration import resolve
from instance_settings import FIELDS, InstanceSettings
from processing import Processor
from server import Server
from storage import Conflict
from versions import FORMAT_VERSION
from worker_capacity import WorkerSettings


class InstanceSettingsTests(unittest.TestCase):
    git = test_workflow.WorkflowTests.git
    fake_github = test_workflow.WorkflowTests.fake_github

    def setUp(self):
        test_workflow.WorkflowTests.setUp(self)
        self.store.config = self.store.root / 'config.json'
        self.original = dict(format_version=FORMAT_VERSION, project_name='Original',
            data='data.json', extension={'private':'kept'},
            processing={'executable':'private-command', 'automatic':False, 'idle_seconds':600, 'closed_seconds':90},
            workflow={'max_workers':4, 'enabled':False, 'automatic_publish':False,
                      'automatic_merge':False, 'automatic_deploy':False, 'test':['private-recipe']})
        self.store.config.write_text(json.dumps(self.original))
        self.store.context['project_name'] = 'Original'
        self.processor = Processor(self.store, 'http://127.0.0.1:1', self.processing)
        self.addCleanup(self.processor.close)
        self.editor = InstanceSettings(self.workflow, self.processor)

    def save(self, changes, revision=None):
        return self.editor.save(dict(changes=changes, revision=revision or self.editor.view()['revision']))

    def test_allowlist_effective_values_live_apply_reload_and_no_authority(self):
        view = self.editor.view()
        self.assertTrue(view['editable'])
        self.assertEqual(set(view['values']), set(FIELDS))
        self.assertNotIn('private', json.dumps(view))
        before_options = copy.deepcopy(self.workflow.options)
        result = self.save(dict(project_name='New title', max_workers=2, idle_seconds=120, closed_seconds=45))
        self.assertEqual(result['values'], dict(project_name='New title', max_workers=2, idle_seconds=120, closed_seconds=45))
        expected = copy.deepcopy(self.original)
        expected['project_name'] = 'New title'; expected['workflow']['max_workers'] = 2
        expected['processing'].update(idle_seconds=120, closed_seconds=45)
        self.assertEqual(json.loads(self.store.config.read_text()), expected)
        loaded = resolve(self.repo, config=self.store.config)
        self.assertEqual(loaded['project_name'], 'New title')
        self.assertEqual(loaded['processing']['idle_seconds'], 120)
        self.assertEqual(loaded['workflow']['max_workers'], 2)
        before_options['max_workers'] = 2
        self.assertEqual(self.workflow.options, before_options)
        self.assertFalse(loaded['workflow']['enabled'])
        self.assertFalse(loaded['processing']['automatic'])
        self.assertFalse(self.store.pending.exists())

    def test_invalid_and_excluded_fields_never_write(self):
        before = self.store.config.read_bytes()
        for changes in ({}, {'enabled':True}, {'workflow':{'enabled':True}}, {'categories':{}},
                        {'max_workers':True}, {'max_workers':9}, {'idle_seconds':59},
                        {'closed_seconds':'40'}, {'closed_seconds':86401}, {'project_name':None},
                        {'project_name':'a'*121}, {'project_name':'bad\nname'}, {'idle_seconds':60.5}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.save(changes)
        self.assertEqual(before, self.store.config.read_bytes())

    def test_panel_header_and_external_edit_conflicts(self):
        worker = WorkerSettings(self.workflow)
        first = self.editor.view()['revision']
        worker.save(dict(max_workers=3, revision=worker.view()['revision']))
        with self.assertRaises(Conflict): self.save({'project_name':'stale'}, first)
        second = worker.view()['revision']
        self.save({'idle_seconds':120})
        with self.assertRaises(Conflict): worker.save(dict(max_workers=5, revision=second))
        third = self.editor.view()['revision']
        data = json.loads(self.store.config.read_text()); data['extension']['concurrent'] = True
        self.store.config.write_text(json.dumps(data))
        with self.assertRaises(Conflict): self.save({'project_name':'stale'}, third)
        self.assertTrue(json.loads(self.store.config.read_text())['extension']['concurrent'])

    def host(self):
        root = self.store.root
        self.store.config = root/'state/unfertig/config/machine.local.json'
        self.store.config.parent.mkdir(parents=True)
        shared = self.store.config.with_name('config.json')
        shared.write_text(json.dumps(self.original))
        self.store.config.write_bytes(shared.read_bytes())
        baseline = self.store.config.with_name('machine-baseline.local.json')
        baseline.write_bytes(shared.read_bytes())
        (root/'.gitignore').write_text((root/'.gitignore').read_text()+'state/local/\nstate/unfertig/config/*.local.json\n')
        local = root/'state/local/unfertig/config/config.json'
        local.parent.mkdir(parents=True)
        local.write_text(json.dumps({'private_extension':{'secret':'preserve'}, 'processing':{'executable':'local-private-command'}}))
        return shared, baseline, local

    def test_host_durable_override_generated_files_and_shared_conflict(self):
        shared, baseline, local = self.host()
        before = [p.read_bytes() for p in (shared, baseline, self.store.config)]
        revision = self.editor.view()['revision']
        self.save(dict(project_name='Local title', max_workers=6, idle_seconds=180), revision)
        self.assertEqual(before, [p.read_bytes() for p in (shared, baseline, self.store.config)])
        saved = json.loads(local.read_text())
        self.assertEqual(saved['private_extension'], {'secret':'preserve'})
        self.assertEqual(saved['processing']['executable'], 'local-private-command')
        self.assertEqual(local.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.editor.view()['saved_values']['project_name'], 'Local title')
        self.assertNotIn('private', json.dumps(self.editor.view()))
        self.assertFalse(subprocess.check_output(['git','-C',str(self.store.root),'ls-files','--',str(local)],text=True))
        # Same recursive host merge contract; exact generated files are unedited.
        merged = json.loads(shared.read_text())
        for key, value in saved.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict): merged[key].update(value)
            else: merged[key] = value
        restart = shared.with_name('restart.json'); restart.write_text(json.dumps(merged))
        loaded = resolve(self.repo, config=restart)
        self.assertEqual(loaded['project_name'], 'Local title')
        self.assertEqual(loaded['workflow']['max_workers'], 6)
        self.assertEqual(loaded['processing']['idle_seconds'], 180)
        revision = self.editor.view()['revision']
        shared.write_text(json.dumps({**self.original, 'project_name':'Concurrent shared title'}))
        with self.assertRaises(Conflict): self.save({'max_workers':2}, revision)
        self.store.config.write_text('{}')
        self.assertFalse(self.editor.view()['editable'])
        with self.assertRaises(ValueError): self.save({'max_workers':2}, revision)

    @unittest.skipUnless(os.environ.get('UNFERTIG_TEST_HOST_CONTEXT'), 'Select a host for supervisor config regeneration conformance.')
    def test_actual_supervisor_config_regeneration(self):
        import importlib.util
        shared, baseline, local = self.host()
        # This host code operates only on the disposable fixture root.
        path = Path(os.environ['UNFERTIG_TEST_HOST_CONTEXT'])/'scripts/codex_setup.py'
        spec = importlib.util.spec_from_file_location('settings_test_host', path)
        host = importlib.util.module_from_spec(spec); spec.loader.exec_module(host)
        for key in ('data', 'repository'):
            self.original[key] = '../../../data.json' if key == 'data' else '../../..'
        shared.write_text(json.dumps(self.original))
        self.store.config.write_bytes(shared.read_bytes()); baseline.write_bytes(shared.read_bytes())
        self.save(dict(project_name='Retained by supervisor', max_workers=5, idle_seconds=300))
        config = host.runtime_config(self.store.root, 'unfertig')
        loaded = resolve(self.repo, config=config)
        self.assertEqual(loaded['project_name'], 'Retained by supervisor')
        self.assertEqual(loaded['workflow']['max_workers'], 5)
        self.assertEqual(loaded['processing']['idle_seconds'], 300)
        self.assertEqual(json.loads(config.read_text())['processing']['executable'], 'local-private-command')
        before = config.read_bytes()
        host.runtime_config(self.store.root, 'unfertig')
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(baseline.read_bytes(), before)

    def test_unsupported_untracked_and_future_configs_are_read_only(self):
        shared, baseline, local = self.host()
        revision = self.editor.view()['revision']
        value = json.loads(local.read_text()); value['format_version'] = '1.99.0'
        local.write_text(json.dumps(value))
        self.assertFalse(self.editor.view()['editable'])
        with self.assertRaises(ValueError): self.save({'max_workers':2}, revision)
        local.write_text('{}')
        subprocess.run(['git','-C',str(self.store.root),'add','-f',str(local)],check=True)
        self.assertFalse(self.editor.view()['editable'])
        with self.assertRaises(ValueError): self.save({'max_workers':2}, revision)

    def test_interrupted_local_replacement_and_uncertain_success(self):
        _, _, local = self.host()
        before = local.read_bytes(); revision = self.editor.view()['revision']
        with patch('instance_settings.atomic', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): self.save({'max_workers':7}, revision)
        self.assertEqual(before, local.read_bytes())
        self.assertEqual(self.workflow.options['max_workers'], 4)
        self.save({'max_workers':7}, revision)
        after = local.read_bytes()
        with self.assertRaises(Conflict): self.save({'max_workers':7}, revision)
        self.assertEqual(after, local.read_bytes())

    def test_transaction_interruption_history_failure_and_recovery(self):
        import storage
        original = storage.atomic
        def interrupted(path, content):
            if path == self.store.config: raise OSError('Interrupted')
            original(path, content)
        with patch('storage.atomic', side_effect=interrupted):
            with self.assertRaises(OSError): self.save({'project_name':'Recovered'})
        self.assertTrue(self.store.journal.exists())
        self.store.initialize()
        self.assertEqual(json.loads(self.store.config.read_text())['project_name'], 'Recovered')
        before = self.store.config.read_bytes(); self.store.initialize()
        self.assertEqual(before, self.store.config.read_bytes())
        with patch.object(self.store, 'commit_pending', return_value=False):
            with self.assertRaises(Conflict): self.save({'closed_seconds':40})
        self.assertFalse(self.editor.view()['editable'])
        self.store.commit_pending()
        self.assertEqual(self.editor.view()['saved_values']['closed_seconds'], 40)
        self.assertEqual(self.editor.view()['values']['closed_seconds'], 90)

    def test_http_token_validation_allowlist_and_conflict(self):
        server = Server(('127.0.0.1', 0), self.store)
        server.workflow = self.workflow; server.processing = self.processor
        # Keep autonomous service actions out of this endpoint fixture.
        server.service_actions = lambda: None
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            with urlopen(origin+'/api/settings') as response: view = json.load(response)
            self.assertNotIn('private', json.dumps(view))
            def send(token, changes, revision=view['revision'], origin_header=origin):
                return urlopen(Request(origin+'/api/settings', data=json.dumps(dict(changes=changes,revision=revision)).encode(), method='PUT',
                    headers={'X-Board-Token':token,'Origin':origin_header}))
            for token, changes, code, header in [('wrong',{'max_workers':3},403,origin),
                    (server.token,{'automatic_publish':True},400,origin),
                    (server.token,{'max_workers':3},403,'http://foreign.invalid')]:
                with self.assertRaises(HTTPError) as caught: send(token,changes,origin_header=header)
                self.assertEqual(caught.exception.code,code); caught.exception.close()
            with send(server.token,{'project_name':'HTTP title'}) as response:
                self.assertEqual(json.load(response)['values']['project_name'],'HTTP title')
            with self.assertRaises(HTTPError) as caught: send(server.token,{'max_workers':5})
            self.assertEqual(caught.exception.code,409); caught.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()
