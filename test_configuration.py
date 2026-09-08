from versions import semantic, FORMAT_VERSION, migrate
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from configuration import resolve
from server import validate
from storage import BoardStore
from split_board import split
from test_server import fixture


class ConfigurationTests(unittest.TestCase):
    def test_portable_identity_fallback_and_explicit_override(self):
        import hashlib
        config = self.root / 'unfertig.json'
        config.write_text(json.dumps({'data': 'boards/project/data.json'}))
        expected = hashlib.sha256(b'boards/project/data.json').hexdigest()[:32]
        self.assertEqual(resolve(self.root, config)['project_id'], expected)
        config.write_text(json.dumps({'data': 'boards/project/data.json', 'project_id': 'stable-board'}))
        self.assertEqual(resolve(self.root, config)['project_id'], 'stable-board')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.git(self.root,'init')
        self.git(self.root,'config','user.name','Test')
        self.git(self.root,'config','user.email','test@example.invalid')
        (self.root/'README').write_text('test')
        self.git(self.root,'add','README');self.git(self.root,'commit','-m','initial')
    def git(self,path,*args):
        return subprocess.run(['git','-C',str(path),*args],check=True,capture_output=True,text=True).stdout.strip()
    def test_standalone_starter_and_no_overwrite(self):
        config=resolve(self.root)
        store=BoardStore(config['path'],validate);store.acquire();self.addCleanup(store.close)
        store.create_starter();store.initialize()
        self.assertEqual(len(store.read()[0]['todos']),1)
        self.assertFalse(store.snapshot()['history']['pending'])
        head=self.git(self.root,'rev-parse','HEAD');store.create_starter()
        self.assertEqual(self.git(self.root,'rev-parse','HEAD'),head)
    def test_config_relative_to_file_not_cwd_and_owner(self):
        app=self.root/'app';app.mkdir();self.git(app,'init')
        config=self.root/'unfertig.json';config.write_text(json.dumps({'mode':'embedded','data':'boards/project/data.json','repository':'.'}))
        result=resolve(app,config)
        self.assertEqual(result['path'],self.root/'boards/project/data.json')
        self.assertEqual(result['repository'],self.root)
        self.assertEqual(resolve(app,config,data='boards/other/data.json')['path'],self.root/'boards/other/data.json')
        config.write_text(json.dumps({'mode':'embedded','data':'app/board/data.json'}))
        with self.assertRaises(ValueError):resolve(app,config)
    def test_missing_config_or_repository_fails(self):
        with self.assertRaises(OSError):resolve(self.root,self.root/'missing.json')
        with tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(ValueError):resolve(outside)
    def test_parent_commit_excludes_submodule_and_unrelated_staging(self):
        app=self.root/'app';app.mkdir();self.git(app,'init')
        for key,value in [('user.name','Test'),('user.email','test@example.invalid')]:self.git(app,'config',key,value)
        (app/'README').write_text('app');self.git(app,'add','README');self.git(app,'commit','-m','app')
        self.git(self.root,'add','app');self.git(self.root,'commit','-m','gitlink')
        (self.root/'unrelated').write_text('staged');self.git(self.root,'add','unrelated')
        config=self.root/'unfertig.json';config.write_text(json.dumps({'mode':'embedded','data':'boards/test/data.json'}))
        result=resolve(app,config)
        store=BoardStore(result['path'],validate);store.acquire();self.addCleanup(store.close)
        before=self.git(app,'rev-parse','HEAD');store.create_starter()
        self.assertFalse(store.snapshot()['history']['pending'])
        self.assertEqual(self.git(app,'rev-parse','HEAD'),before)
        self.assertEqual(self.git(self.root,'diff','--cached','--name-only'),'unrelated')
        self.assertTrue(all(p.startswith('boards/test/') for p in self.git(self.root,'show','--pretty=format:','--name-only','HEAD').splitlines()))
    def test_split_is_lossless_restartable_and_stops_live_writer(self):
        old=self.root/'old';old.mkdir();(old/'data.json').write_text(json.dumps(fixture()))
        store=BoardStore(old/'data.json',validate,git=False);store.initialize()
        assignments={'ideas':{'I0001':'app'},'todos':{'T0001':'app'}}
        args=(old/'data.json',self.root/'app-board',self.root/'project-board',self.root/'recovery',assignments)
        store.acquire()
        with self.assertRaises(ValueError):split(*args)
        store.close()
        import split_board
        real=split_board.atomic
        def interrupt(path,raw):
            if path == self.root/'project-board'/'data.json':raise OSError('interruption')
            real(path,raw)
        with patch('split_board.atomic',side_effect=interrupt):
            with self.assertRaises(OSError):split(*args)
        result=split(*args);self.assertTrue(result['complete'])
        self.assertEqual(semantic(BoardStore(self.root/'app-board/data.json',validate,git=False).read()[0]),dict(fixture(), todos=[semantic(migrate(t, 'todo')) for t in fixture()['todos']]))
        self.assertEqual(split(*args),result)
        self.assertEqual(json.loads((old/'data.json').read_text())['schema_version'],0)

    def test_host_owned_discovery_and_external_overrides(self):
        app=self.root/'tools/unfertig';app.mkdir(parents=True);self.git(app,'init')
        self.git(app,'config','user.name','Test');self.git(app,'config','user.email','test@example.invalid')
        (app/'README').write_text('app');self.git(app,'add','.');self.git(app,'commit','-m','app')
        self.git(self.root,'submodule','add',str(app),'tools/unfertig')
        self.git(self.root,'submodule','absorbgitdirs')
        config=self.root/'state/unfertig/config/config.json';config.parent.mkdir(parents=True)
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'state/unfertig/config/config.json'):resolve(app)
            config.write_text(json.dumps({'mode':'embedded','data':'../data/data.json','repository':'../../..'}))
            result=resolve(app)
            self.assertEqual(result['path'],self.root/'state/unfertig/data/data.json')
            self.assertEqual(result['repository'],self.root)
            self.assertEqual(result['config'],config)
            external=self.root/'external';(external/'config').mkdir(parents=True)
            other=external/'config/config.json'
            other.write_text(json.dumps({'data':'../data/data.json'}))
            with patch.dict(os.environ,{'UNFERTIG_CONFIG':str(other)}):
                self.assertEqual(resolve(app)['config'],other)
                self.assertEqual(resolve(app,state_dir=config.parent.parent)['config'],config)
            with patch.dict(os.environ,{'UNFERTIG_STATE_DIR':str(external)}):
                self.assertEqual(resolve(app)['path'],external/'data/data.json')
                self.assertEqual(resolve(app,config=config)['config'],config)
            with self.assertRaises(OSError):resolve(app,state_dir=self.root/'missing')

    def test_port_configuration_and_validation(self):
        from configuration import configure_port, choose_port, DEFAULT_PORT
        from io import StringIO
        config=self.root/'unfertig.json'
        config.write_text(json.dumps({'port':8799}))
        self.assertEqual(resolve(self.root)['port'],8799)
        for invalid in (0,65536,True,'8765'):
            config.write_text(json.dumps({'port':invalid}))
            with self.assertRaises(ValueError):resolve(self.root)
        config.unlink()
        with patch('configuration.port_occupied',return_value=False), patch('builtins.input',return_value=''), patch('sys.stdout',new_callable=StringIO):
            configure_port(resolve(self.root),self.root)
        self.assertEqual(json.loads(config.read_text()),{'format_version':FORMAT_VERSION,'port':DEFAULT_PORT,'workflow':{'automatic':False,'enabled':False,'max_workers':2,'automatic_merge':False,'automatic_publish':False,'automatic_deploy':False},'processing':{'enabled':True,'automatic':False,'idle_seconds':600,'closed_seconds':90}})
        self.assertTrue(resolve(self.root)['bootstrap'])
        with patch('configuration.port_occupied',side_effect=lambda p:p==8765), patch('builtins.input',side_effect=['bad','0','8765','8799']), patch('sys.stdout',new_callable=StringIO) as output:
            self.assertEqual(choose_port(),8799)
            self.assertIn('8765',output.getvalue())
            self.assertIn('8799',output.getvalue())
    def test_port_probe_detects_bound_listener(self):
        import socket
        from configuration import port_occupied
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen()
            self.assertTrue(port_occupied(listener.getsockname()[1]))

class ProjectNameTests(unittest.TestCase):
    setUp = ConfigurationTests.setUp
    git = ConfigurationTests.git
    def test_explicit_name_skips_discovery(self):
        config=self.root/'instance.json'
        for name in ('Customer <A> & "B"', '', '   '):
            config.write_text(json.dumps({'project_name':name,'data':'external/data.json'}))
            with patch('configuration.discover_project_name',side_effect=AssertionError('discovery called')):
                result=resolve(self.root,config)
            self.assertEqual(result['project_name'],name.strip())
            self.assertEqual(result['path'],self.root/'external/data.json')
            self.assertEqual(result['repository'],self.root)
            self.assertEqual(result['mode'],'standalone')
        for name in (None,42,False,[]):
            config.write_text(json.dumps({'project_name':name}))
            with self.assertRaisesRegex(ValueError,'project_name'):resolve(self.root,config)

    def test_wrapper_cwd_and_boundary(self):
        from configuration import discover_project_name
        (self.root/'node.json').write_text(json.dumps({'kind':'project-wrapper','name':'um-example','project':{'path':'different'}}))
        app=self.root/'tools/unfertig';app.mkdir(parents=True)
        self.assertEqual(discover_project_name(app),'um-example')
        before=Path.cwd()
        try:
            os.chdir('/tmp')
            self.assertEqual(resolve(app,data='/tmp/title-fixture.json',no_git=True)['project_name'],'um-example')
        finally:os.chdir(before)
        self.git(self.root/'tools','init')
        self.assertEqual(discover_project_name(app),'')

    def test_superproject_and_standalone(self):
        from configuration import discover_project_name
        self.assertEqual(discover_project_name(self.root),'')
        app=self.root/'tools/unfertig';app.mkdir(parents=True);self.git(app,'init')
        self.git(app,'config','user.name','Test');self.git(app,'config','user.email','test@example.invalid')
        (app/'README').write_text('app');self.git(app,'add','.');self.git(app,'commit','-m','app')
        self.git(self.root,'submodule','add',str(app),'tools/unfertig')
        self.git(self.root,'submodule','absorbgitdirs')
        self.assertEqual(discover_project_name(app),self.root.name)
        (self.root/'node.json').write_text(json.dumps({'kind':'project-wrapper','name':'Wrapper'}))
        self.assertEqual(discover_project_name(app),'Wrapper')
        (self.root/'node.json').write_text('{broken')
        self.assertEqual(discover_project_name(app),self.root.name)

    def test_server_exposes_startup_name_without_changing_board(self):
        import sys, socket, time
        from urllib.request import urlopen
        board=self.root/'data.json';board.write_text(json.dumps(fixture()))
        config=self.root/'instance.json'
        config.write_text(json.dumps({'data':str(board),'project_name':'<Project> & ü'}))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('server.py')),
            '--config',str(config),'--no-git','--no-browser','--port',str(port)],
            cwd='/tmp',stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if process.poll() is not None:self.fail(process.stderr.read().decode())
                try:
                    with urlopen(f'http://127.0.0.1:{port}/api/state') as response:snapshot=json.load(response)
                    break
                except OSError:time.sleep(.05)
            else:self.fail('server did not start')
            self.assertEqual(snapshot['context']['project_name'],'<Project> & ü')
            self.assertEqual(semantic(snapshot['data']),dict(fixture(), todos=[semantic(migrate(t, 'todo')) for t in fixture()['todos']]))
            config.write_text(json.dumps({'data':str(board),'project_name':'Changed'}))
            with urlopen(f'http://127.0.0.1:{port}/api/state') as response:again=json.load(response)
            self.assertEqual(again['context']['project_name'],'<Project> & ü')
            self.assertEqual(again['data'],snapshot['data'])
        finally:
            process.terminate();process.wait(timeout=5);process.stderr.close()
