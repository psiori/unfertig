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
        self.assertEqual(BoardStore(self.root/'app-board/data.json',validate,git=False).read()[0],fixture())
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
