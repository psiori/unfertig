"""Real loopback services and disposable repositories; no live boards/remotes."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from aggregation import Aggregation, exchange
from configuration import resolve
from server import Server, validate
from storage import BoardStore, Conflict, digest
from test_server import fixture, STAMP
from versions import FORMAT_VERSION, migrate


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aggregation-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.boards = []
        for name in ('inbox', 'alpha', 'beta'):
            root = self.root / name; root.mkdir()
            subprocess.run(['git','init','-q',str(root)],check=True)
            for k,v in [('user.name',name),('user.email',name+'@example.test')]:
                subprocess.run(['git','-C',str(root),'config',k,v],check=True)
            (root/'PROCESS.md').write_text('Test destination process; no pushes or conflict resolution.\n')
            store = BoardStore(root/'data.json',validate)
            store.context = dict(mode='aggregation' if name == 'inbox' else 'standalone', project_id=name,
                                 project_name='Alpha' if name == 'alpha' else '', data=str(store.path),
                                 repository=str(root), process=str(root/'PROCESS.md'), todos=str(root/'todos'))
            store.acquire(); self.addCleanup(store.close)
            store.create_starter(); store.initialize()
            self.boards.append(store)
        self.inbox, self.alpha, self.beta = self.boards
        self.sources=[]
        for board in self.boards[1:]:
            server=Server(('127.0.0.1',0),board)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            self.sources.append(dict(project_id=board.context['project_id'],data=str(board.path),url=f'http://127.0.0.1:{server.server_port}'))
        self.inbox.context['sources']=self.sources
        self.router=Aggregation(self.inbox)
        result=self.inbox.mutate(dict(request_id=uuid.uuid4().hex,actor='Requester',initials='SL',changes=[dict(collection='ideas',id=None,record=dict(author='Sascha',text='Improve Alpha',date_entered=STAMP))]))
        self.idea=result['data']['ideas'][0]

    def request(self, project='alpha'):
        board=next(b for b in self.boards if b.context['project_id']==project)
        todo=copy.deepcopy(fixture()['todos'][0]); todo.pop('id')
        context={k:board.context[k] for k in ('data','repository','process','project_id')}
        context['process_sha256']=hashlib.sha256(Path(context['process']).read_bytes()).hexdigest()
        idea=self.inbox.read()[0]['ideas'][0]
        return dict(idea_id=idea['id'],revision=digest(idea),actor='Codex',initials='CX',project_id=project,reason='Alpha explicitly named in text',todo=todo,preflight=context)

    def test_views_qualified_ids_fallback_stale_and_nested(self):
        view=self.router.view()['sources']
        self.assertEqual([s['name'] for s in view],['Alpha','beta'])
        self.assertEqual([s['data']['todos'][0]['id'] for s in view],['T0001','T0001'])
        with patch('aggregation.exchange',side_effect=ValueError('offline')):
            stale=self.router.view()['sources']
        self.assertEqual(stale[0]['status'],'stale');self.assertEqual(stale[0]['data'],view[0]['data'])
        fresh=Aggregation(self.inbox)
        self.alpha.context['mode']='aggregation'
        self.assertEqual(fresh.view()['sources'][0]['status'],'unavailable')

    def test_http_route_and_changed_source_revision(self):
        server=Server(('127.0.0.1',0),self.inbox)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        source=dict(url=f'http://127.0.0.1:{server.server_port}')
        snapshot=exchange(source)
        before=exchange(source,'/api/aggregate')['sources'][0]['revision']
        result=exchange(source,'/api/routes',self.request(),snapshot['token'])
        self.assertEqual(result['idea']['routing']['status'],'routed')
        after=exchange(source,'/api/aggregate')['sources'][0]
        self.assertNotEqual(before,after['revision'])
        self.assertEqual(len(after['data']['todos']),2)

    def test_simultaneous_first_claim_and_child_edit(self):
        request=self.request(); results=[];errors=[]
        todo=self.alpha.read()[0]['todos'][0];revision=digest(todo);todo['name']='Concurrent child edit'
        def route():
            try:results.append(self.router.route(request))
            except Exception as e:errors.append(e)
        def edit():
            self.alpha.mutate(dict(request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=todo['id'],revision=revision,record=todo)]))
        threads=[threading.Thread(target=route),threading.Thread(target=route),threading.Thread(target=edit)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,[]);self.assertEqual(len(results),2)
        todos=self.alpha.read()[0]['todos']
        self.assertEqual(len(todos),2);self.assertEqual(todos[0]['name'],'Concurrent child edit')

    def test_route_exact_original_retry_and_repository_commits(self):
        body=self.request(); result=self.router.route(body)['idea']
        self.assertEqual(result['routing']['status'],'routed')
        todo=self.alpha.read()[0]['todos'][-1]
        self.assertEqual(todo['id'],'CX_T0002');self.assertEqual(todo['author'],'Sascha');self.assertEqual(todo['created_by'],'Codex')
        self.assertEqual(todo['source_ideas'],[])
        self.assertEqual(todo['source_refs'],[dict(project_id='inbox',idea={k:self.idea[k] for k in ('id','text','author','date_entered')})])
        self.assertEqual(self.inbox.read()[0]['todos'],[])
        self.router.route(body)
        self.assertEqual(len(self.alpha.read()[0]['todos']),2)
        self.assertEqual(len(self.alpha.read()[0]['ideas']),0)
        for board in (self.inbox,self.alpha):
            author=subprocess.check_output(['git','-C',str(board.root),'log','-1','--format=%an']).decode().strip()
            self.assertEqual(author,board.context['project_id'])
            self.assertFalse(board.pending.exists())
        with self.assertRaises(Conflict):
            record=copy.deepcopy(result);record['selected_project']='beta'
            self.inbox.mutate(dict(request_id=uuid.uuid4().hex,changes=[dict(collection='ideas',id=record['id'],revision=digest(result),record=record)]))

    def test_unclear_correction_explicit_precedence_and_unreachable(self):
        body=dict(idea_id=self.idea['id'],revision=digest(self.idea),actor='Codex')
        result=self.router.route(body)['idea'];self.assertEqual(result['routing']['status'],'unclear')
        changed=dict(result,selected_project='beta')
        self.inbox.mutate(dict(request_id=uuid.uuid4().hex,changes=[dict(collection='ideas',id=result['id'],revision=digest(result),record=changed)]))
        request=self.request('beta');request['project_id']='alpha'
        with patch('aggregation.exchange',side_effect=ValueError('offline')):
            blocked=self.router.route(request)['idea']
        self.assertEqual(blocked['routing']['status'],'blocked');self.assertNotIn('request',blocked['routing'])
        request=self.request('beta');request['project_id']='alpha'
        self.assertEqual(self.router.route(request)['idea']['routing']['project_id'],'beta')
        self.assertEqual(len(self.alpha.read()[0]['todos']),1)

    def test_lost_destination_response_restart_and_concurrent_workers(self):
        body=self.request(); real=exchange
        def lose(source,path='/api/state',body=None,token=None):
            result=real(source,path,body,token)
            if path=='/api/changes': raise ValueError('lost response')
            return result
        with patch('aggregation.exchange',side_effect=lose):
            result=self.router.route(body)['idea']
        self.assertEqual(result['routing']['status'],'blocked');self.assertIn('request',result['routing'])
        self.assertEqual(len(self.alpha.read()[0]['todos']),2)
        restarted=Aggregation(self.inbox);errors=[]
        def retry():
            try: restarted.route(body)
            except Exception as e: errors.append(e)
        threads=[threading.Thread(target=retry) for _ in range(3)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,[]);self.assertEqual(len(self.alpha.read()[0]['todos']),2)
        self.assertEqual(self.inbox.read()[0]['ideas'][0]['routing']['status'],'routed')

    def test_local_history_failure_after_destination_save(self):
        body=self.request();real=self.inbox.commit_pending
        def fail_completion():
            ideas=json.loads(self.inbox.path.read_text())['ideas']
            if ideas and ideas[0].get('routing',{}).get('status')=='routed':return False
            return real()
        with patch.object(self.inbox,'commit_pending',side_effect=fail_completion):
            result=self.router.route(body)
        self.assertTrue(result['history']['pending'])
        self.assertEqual(len(self.alpha.read()[0]['todos']),2)
        self.assertTrue(self.inbox.commit_pending())
        self.router.route(body);self.assertEqual(len(self.alpha.read()[0]['todos']),2)

    def test_destination_history_failure_retains_claim(self):
        with patch.object(self.alpha,'commit_pending',return_value=False):
            result=self.router.route(self.request())['idea']
        self.assertEqual(result['routing']['status'],'blocked');self.assertIn('request',result['routing'])
        self.assertEqual(len(self.alpha.read()[0]['todos']),2)
        self.alpha.commit_pending()
        self.router.route(self.request());self.assertEqual(len(self.alpha.read()[0]['todos']),2)

    def test_preflight_and_invalid_todo_do_not_claim_or_write(self):
        body=self.request();body['preflight']['repository']='/wrong'
        result=self.router.route(body)['idea'];self.assertNotIn('request',result['routing'])
        body=self.request();body['todo']['name']=''
        result=self.router.route(body)['idea'];self.assertNotIn('request',result['routing'])
        self.assertEqual(len(self.alpha.read()[0]['todos']),1)
        with self.assertRaises(ValueError):
            self.inbox.mutate(dict(request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=None,record=body['todo'])]))

    def test_initials_legacy_changed_empty_collision_and_immutability(self):
        self.assertEqual(self.idea['id'],'SL_I0001')
        def create(initials):
            return self.inbox.mutate(dict(request_id=uuid.uuid4().hex,initials=initials,changes=[dict(collection='ideas',id=None,record=dict(author='A',text='X',date_entered=STAMP))]))['assigned'][0]['id']
        self.assertEqual(create('AB'),'AB_I0002');self.assertEqual(create(''),'I0003')
        with self.assertRaises(ValueError):create('../bad')
        old=self.inbox.read()[0]['ideas'][0];changed=dict(old,text='changed')
        with self.assertRaises(ValueError):
            self.inbox.mutate(dict(request_id=uuid.uuid4().hex,changes=[dict(collection='ideas',id=old['id'],revision=digest(old),record=changed)]))

    def test_duplicate_source_provenance_rejected_with_new_request_id(self):
        self.router.route(self.request())
        todo=self.alpha.read()[0]['todos'][-1]
        with self.assertRaises(Conflict):
            self.alpha.mutate(dict(request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=None,record=todo)]))

    def test_configuration_canonicalization_and_self_rejection(self):
        config=self.root/'config.json'
        source=self.sources[0]
        config.write_text(json.dumps(dict(mode='aggregation',project_id='inbox',data='inbox/data.json',sources=[source,dict(source,data=str(self.root/'alpha/../alpha/data.json'))])))
        self.assertEqual(len(resolve(self.root,config=config,no_git=True)['sources']),1)
        config.write_text(json.dumps(dict(mode='aggregation',project_id='alpha',data='inbox/data.json',sources=[source])))
        with self.assertRaises(ValueError):resolve(self.root,config=config,no_git=True)

    def test_12_migration_preserves_extensions_originals_and_old_writer_guard(self):
        original=dict(format_version='1.1.0',schema_version=2,ideas=[self.idea],custom={'x':1})
        result=migrate(original,'board');self.assertEqual(result,dict(original,format_version=FORMAT_VERSION))
        self.assertEqual(migrate(result,'board'),result)
        from versions import inspect
        self.assertEqual(inspect(result,supported='1.1.0')[0],'read_only')

if __name__=='__main__':unittest.main()
