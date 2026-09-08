import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from processing import Processor, pending, settings
from versions import migrate, FORMAT_VERSION, inspect
from server import validate
from storage import BoardStore
from test_server import fixture, STAMP

LOCAL = 'a' * 64
OTHER = 'b' * 64

class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.options = settings({'automatic':True}, self.root, self.root, self.root)
        self.snapshot = dict(data=dict(ideas=[dict(id='I0002', captured_system=LOCAL)], todos=[]),
            compatibility={'read_only':False}, history={'pending':False,'enabled':True},
            context={'process':str(self.root/'PROCESS.md')})
        self.store = Mock(); self.store.snapshot.side_effect = lambda: copy.deepcopy(self.snapshot)
        self.now = 0
        self.processor = Processor(self.store, 'http://127.0.0.1:1234', self.options, lambda:self.now)
        self.machine = patch('processing.system_id', return_value=LOCAL); self.machine.start(); self.addCleanup(self.machine.stop)

    def test_processing_prompt_uses_shared_effort_selection_and_default(self):
        from efforts import processing_guidance
        text = self.processor.prompt(self.snapshot, ['I0002'])
        self.assertIn(processing_guidance(), text)
        self.assertIn('Select and persist effort', text)
        self.assertIn('Planning only', text)

    def test_only_capturing_system_and_manual_legacy(self):
        self.snapshot['data']['ideas'] += [dict(id='I0003', captured_system=OTHER), dict(id='I0004')]
        self.assertEqual([i['id'] for i in pending(self.snapshot, True)], ['I0002'])
        self.assertEqual(len(pending(self.snapshot)), 3)
        self.snapshot['data']['todos'] = [dict(source_ideas=['I0002'])]
        self.assertEqual(pending(self.snapshot, True), [])

    def test_bounded_auto_and_explicit_override(self):
        child = self.root/'one'/'two'/'three'; child.mkdir(parents=True)
        (self.root/'AGENTS.md').write_text('too far')
        self.assertEqual(settings({},self.root,child,child)['working_directory'],str(child))
        (self.root/'one'/'node.json').write_text('{}')
        self.assertEqual(settings({},self.root,child,child)['working_directory'],str(self.root/'one'))
        self.assertEqual(settings({'working_directory':'.'},self.root,child,child)['working_directory'],str(self.root))
        with self.assertRaises(ValueError): settings({'working_directory':'missing'},self.root,child,child)
        with self.assertRaises(ValueError): settings({'idle_seconds':True},self.root,child,child)

    def beat(self, ident='one', **values):
        self.processor.presence(dict(client_id=ident, **values))

    def test_idle_activity_drafts_and_multiple_tabs(self):
        with patch.object(self.processor, 'start') as start:
            self.beat(active=True)
            self.now=590; self.beat(active=True); self.processor.tick(); start.assert_not_called()
            self.now=1190; self.beat(draft=True); self.processor.tick(); start.assert_not_called()
            self.now=1196; self.beat(); self.processor.tick(); start.assert_called_once_with(automatic=True)
        self.processor.next_tick=0
        with patch.object(self.processor, 'start') as start:
            self.beat('two', active=True); self.beat('one', closed=True)
            self.now+=91; self.beat('two'); self.processor.tick(); start.assert_not_called()
            self.beat('two',closed=True); self.now+=89; self.processor.tick(); start.assert_not_called()
            self.now+=6; self.processor.tick(); start.assert_called_once()

    def test_missing_close_event_and_no_startup_trigger(self):
        with patch.object(self.processor, 'start') as start:
            self.now=1000; self.processor.tick(); start.assert_not_called()
            self.beat(active=True); self.now+=96; self.processor.tick(); start.assert_called_once()

    def test_single_run_attempts_and_compatibility(self):
        with patch('processing.shutil.which',return_value='/codex'), patch('processing.threading.Thread') as thread:
            self.processor.start(True); self.processor.start(True)
            thread.assert_called_once()
            self.processor.state['status']='failed'; self.processor.start(True)
            thread.assert_called_once()
            self.processor.start(False); self.assertEqual(thread.call_count,2)
            self.processor.state['status']='idle'; self.snapshot['history']['pending']=True
            with self.assertRaises(ValueError): self.processor.start()

    def test_migration_preserves_legacy_origin_and_extensions(self):
        config = {'format_version':'1.3.0','extension':42}
        migrated = migrate(config,'config')
        self.assertEqual(migrated['format_version'],FORMAT_VERSION)
        self.assertFalse(migrated['processing']['automatic'])
        self.assertEqual(migrate(migrated,'config'),migrated)
        data = migrate({'format_version':'1.3.0','ideas':[{'id':'I0001'}]},'data')
        self.assertNotIn('captured_system',data['ideas'][0])
        self.assertEqual(inspect(data, supported='1.3.0')[0],'read_only')

    def test_store_assigns_origin_and_forbids_rewrite(self):
        path=self.root/'data.json';path.write_text(json.dumps(fixture()))
        store=BoardStore(path,validate,git=False);store.initialize();self.addCleanup(store.close)
        result=store.mutate(dict(request_id='capture-test-000001',actor='Test',changes=[dict(collection='ideas',id=None,
            record=dict(author='Test',text='New idea',date_entered=STAMP,captured_system=OTHER))]))
        idea=result['data']['ideas'][-1]
        self.assertEqual(idea['captured_system'],LOCAL)
        original=copy.deepcopy(idea); idea['captured_system']=OTHER
        with self.assertRaises(ValueError):
            store.mutate(dict(request_id='change-origin-00001',actor='Test',changes=[dict(collection='ideas',id=idea['id'],
                revision=result['revisions']['ideas'][idea['id']],record=idea)]))
        self.assertEqual(store.snapshot()['data']['ideas'][-1],original)

class LauncherIntegrationTests(unittest.TestCase):
    def test_button_launch_runs_in_project_and_commits_through_api(self):
        import os
        import subprocess
        import sys
        import threading
        import time
        import urllib.request
        import urllib.error
        from server import Server
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            subprocess.run(['git','init','-q',str(root)],check=True)
            for key,value in [('user.name','Test'),('user.email','test@example.invalid')]:
                subprocess.run(['git','-C',str(root),'config',key,value],check=True)
            (root/'.gitignore').write_text('.server.lock\n.service.lock\n.operation.lock\n.transaction.json\n.history-pending.json\n.receipts/\n')
            path=root/'data.json'; path.write_text(json.dumps(fixture()))
            store=BoardStore(path,validate);store.initialize()
            store.context=dict(process=str(root/'PROCESS.md'),data=str(path),todos=str(root/'todos'),repository=str(root),mode='standalone')
            (root/'PROCESS.md').write_text('Read test project context.')
            created=store.mutate(dict(request_id='integration-idea-0001',actor='Test',changes=[dict(collection='ideas',id=None,
                record=dict(author='Test',text='Add a useful task',date_entered=STAMP))]))
            ident=created['data']['ideas'][-1]['id']
            todo=copy.deepcopy(fixture()['todos'][0]);todo.pop('id');todo.update(source_ideas=[ident],author='Test',created_by='Codex')
            server=Server(('127.0.0.1',0),store)
            url=f'http://127.0.0.1:{server.server_port}'
            executable=root/'fake-codex'
            executable.write_text(f'''#!{sys.executable}
import json, os, pathlib, sys, urllib.request
assert os.getcwd() == {str(root)!r}
assert '--approve-for-me' in sys.argv
assert '--sandbox' not in sys.argv
prompt = sys.stdin.read()
assert {ident!r} in prompt and 'Planning only' in prompt
base = {url!r}
snapshot = json.load(urllib.request.urlopen(base + '/api/state'))
body = {{'request_id':'integration-todo-0001','actor':'Codex','changes':[{{'collection':'todos','id':None,'record':{todo!r}}}]}}
request = urllib.request.Request(base+'/api/changes',data=json.dumps(body).encode(),method='PUT',headers={{'Content-Type':'application/json','X-Board-Token':snapshot['token']}})
result = json.load(urllib.request.urlopen(request))
assert not result['history']['pending']
pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('Created and committed '+result['assigned'][0]['id'])
''')
            executable.chmod(0o755)
            options=settings({'executable':str(executable),'working_directory':str(root)},root,root,root)
            server.processing=Processor(store,url,options)
            worker=threading.Thread(target=server.serve_forever,kwargs={"poll_interval": 0.01},daemon=True);worker.start()
            try:
                request=urllib.request.Request(url+'/api/processing/start',data=b'{}',method='PUT')
                with self.assertRaises(urllib.error.HTTPError) as rejected: urllib.request.urlopen(request)
                self.assertEqual(rejected.exception.code,403)
                request.add_header('X-Board-Token',server.token)
                result=json.load(urllib.request.urlopen(request));self.assertEqual(result['status'],'running')
                deadline=time.monotonic()+10
                while server.processing.status()['status']=='running' and time.monotonic()<deadline: time.sleep(.05)
                result=server.processing.status()
                self.assertEqual(result['status'],'completed',result)
                self.assertIn('Created and committed',result['message'])
                self.assertEqual(len(store.snapshot()['data']['todos']),2)
                self.assertFalse(store.snapshot()['history']['pending'])
            finally:
                server.shutdown();server.server_close();worker.join();store.close()
