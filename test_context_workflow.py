"""Real Git UM and collection integration against disposable remotes."""
import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch
import test_workflow as fixtures
from context_workflow import declarations, guard
from storage import Conflict
from versions import migrate, FORMAT_VERSION

class ContextWorkflowTests(unittest.TestCase):
    setUp=fixtures.WorkflowTests.setUp
    git=fixtures.WorkflowTests.git
    def run_stage(self,action,wait=True):
        snap=self.store.snapshot();todo=snap['data']['todos'][0];run=todo.get('workflow',{})
        self.workflow.start(dict(id=todo['id'],action=action,revision=snap['revisions']['todos'][todo['id']],commit=run.get('commit'),repositories=run.get('repository_heads')))
        if wait:self.workflow.workers[todo['id']].join(20);self.assertFalse(self.workflow.workers[todo['id']].is_alive())
        return self.store.snapshot()['data']['todos'][0]
    def fake_github(self,w,*a,**kwargs):
        repo=w.options['repository']
        if a[:2]==('pr','list'):return json.dumps([dict(url=s['url']) for s in self.prs.values() if s['repo']==repo and s['headRefName']==a[a.index('--head')+1]])
        if a[:2]==('pr','create'):
            url='https://github.com/test/repo/pull/'+str(len(self.prs)+1)
            self.prs[url]=dict(url=url,repo=repo,state='OPEN',isDraft=True,headRefName=a[a.index('--head')+1],baseRefName='main',mergeCommit=None)
            return url
        if a[:2]==('pr','view'):
            s=dict(self.prs[a[2]]);s['headRefOid']=w.git('ls-remote','origin','refs/heads/'+s['headRefName'],cwd=repo).split()[0];return json.dumps(s)
        if a[:2]==('pr','ready'):self.prs[a[2]]['isDraft']=False;return ''
        if a[:2]==('pr','edit'):return ''
        raise AssertionError(a)
    def um(self,collection=False,pinned=False):
        self.context=self.root/'um';self.context.mkdir();old=self.repo;self.repo=self.context/'code';old.rename(self.repo)
        self.workflow.options['repository']=str(self.repo);self.workflow.processing['working_directory']=str(self.context)
        def g(*a):return self.workflow.git(*a,cwd=self.context)
        g('init','-b','main');g('config','user.name','Context');g('config','user.email','context@example.invalid')
        self.node=dict(schema_version=1,kind='project-wrapper',name='um-code',project=dict(path='code',repository='test/code'))
        (self.context/'.gitignore').write_text('/code/\n/.worktrees/\n')
        if pinned:
            (self.context/'.gitignore').write_text('/.worktrees/\n')
            (self.context/'.gitmodules').write_text('[submodule "code"]\n path = code\n url = '+str(self.remote)+'\n')
            g('update-index','--add','--cacheinfo','160000,'+self.git('rev-parse','HEAD')+',code')
        if collection:
            self.node.pop('project');self.node.update(schema_version=2,id=str(uuid.uuid4()),projects=[])
            for name in ['code','second']:
                if name=='second':
                    g('clone',str(self.remote),str(self.context/name));self.workflow.git('checkout','-b','main','origin/main',cwd=self.context/name)
                self.node['projects'].append(dict(id=str(uuid.uuid4()),name=name,path=name,mount='submodule',repository=dict(owner='test',name=name,visibility='private')))
            (self.context/'.gitignore').write_text('/code/\n/second/\n/.worktrees/\n')
        (self.context/'node.json').write_text(json.dumps(self.node));g('add','.');g('commit','-m','UM baseline')
        remote=self.root/'um.git';g('init','--bare',str(remote));g('remote','add','origin',str(remote));g('push','-u','origin','main')
        self.store.context['repository']=str(self.context);self.changed={'context'}
    def agent(self,argv,cwd,ident,stdin=None,**kwargs):
        if 'exec' not in argv:return ''
        assigned=json.loads(stdin.split('Assigned repositories: ')[1].split('\n')[0]);self.assertEqual(str(cwd),assigned[0]['worktree']);results=[]
        for item in assigned:
            if not item['available']:continue
            p=Path(item['worktree'])
            if item['id'] in self.changed:
                f=p/'design/proposal.md' if item['role']=='context' else p/'feature.txt';f.parent.mkdir(exist_ok=True);f.write_text('Task work')
                self.workflow.git('add',str(f.relative_to(p)),cwd=p);self.workflow.git('commit','-m','Task work',cwd=p)
            results.append(dict(id=item['id'],commit=self.workflow.git('rev-parse','HEAD',cwd=p)))
        Path(argv[argv.index('-o')+1]).write_text(json.dumps(dict(status='complete',summary='Complete',tests=['checked'],limitations=[],repositories=results))+'\nUNFERTIG_IMPLEMENTATION_COMPLETE');return ''
    def implement(self):
        with patch.object(self.workflow,'command',side_effect=self.agent):todo=self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'],'ready',todo['workflow']['message']);return todo
    def test_context_only_one_pr_and_integration(self):
        self.um();todo=self.implement();self.assertEqual(len(self.prs),1);self.assertFalse((self.context/'design/proposal.md').exists())
        todo=self.run_stage('merge');self.assertEqual(todo['workflow']['phase'],'done',todo['workflow']['message']);self.assertTrue((self.context/'design/proposal.md').exists());self.assertEqual(todo['status'],'closed')
    def test_collection_missing_child_allows_context(self):
        self.um(collection=True);shutil.rmtree(self.context/'second');todo=self.implement();self.assertEqual(len(todo['workflow']['repositories']),3);self.assertEqual(len(self.prs),1);self.assertFalse((self.context/'second').exists())
    def test_child_and_context_publish_and_pin(self):
        self.um(pinned=True);self.changed={'context','project'};todo=self.implement();self.assertEqual(len(self.prs),2)
        with patch.object(self.workflow,'launch_deployment') as deploy:todo=self.run_stage('merge')
        self.assertTrue(deploy.called,todo['workflow']['message']);repos={x['id']:x for x in todo['workflow']['repositories']};self.assertTrue(repos['context'].get('published_commit'),todo['workflow']['message']);self.assertEqual(self.workflow.git('ls-tree','HEAD','code',cwd=self.context).split()[2],repos['project']['published_commit'])
    def test_secondary_code_requires_recipe(self):
        self.um(collection=True);self.changed={self.node['projects'][1]['id']}
        with patch.object(self.workflow,'command',side_effect=self.agent):todo=self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'],'implementation_failed');self.assertIn('Configure workflow.repositories',todo['workflow']['message'])
    def test_symlink_and_undeclared_target_rejected(self):
        self.um();(self.context/'linked').symlink_to(self.repo,target_is_directory=True);self.node['project']['path']='linked';(self.context/'node.json').write_text(json.dumps(self.node))
        with self.assertRaisesRegex(ValueError,'symlink'):declarations(self.workflow)
        self.node['project']['path']='other';(self.context/'node.json').write_text(json.dumps(self.node))
        with self.assertRaisesRegex(ValueError,'explicitly declared'):declarations(self.workflow)
    def test_changed_manifest_and_legacy_migration(self):
        self.um();todo=self.implement();self.node['purpose']='changed';(self.context/'node.json').write_text(json.dumps(self.node))
        with self.assertRaisesRegex(Conflict,'declarations changed'):guard(self.workflow,todo['workflow'])
        legacy={'format_version':'1.16.0','workflow':{'branch':'old'},'extension':{'keep':True}};n=migrate(legacy,'todo');self.assertEqual(n['workflow'],legacy['workflow']);self.assertEqual(migrate(n,'todo'),n);self.assertEqual(n['format_version'],FORMAT_VERSION)

    def test_context_and_child_keep_distinct_git_identity(self):
        self.um();todo=self.implement();entries=todo['workflow']['repositories']
        emails={r['id']:self.workflow.git('config','user.email',cwd=r['worktree']) for r in entries}
        self.assertEqual(emails,{'context':'context@example.invalid','project':'test@example.invalid'})

    def test_private_board_path_and_forged_report_are_rejected(self):
        self.um();todo=self.implement();run=todo['workflow'];context=run['repositories'][0]
        p=Path(context['worktree']);(p/'state').mkdir();(p/'state/fake.json').write_text('{}')
        self.workflow.git('add','state',cwd=p);self.workflow.git('commit','-m','Forbidden board edit',cwd=p)
        from context_workflow import protected
        with self.assertRaisesRegex(Conflict,'protected UM'):protected(self.workflow,context)
        self.assertFalse((self.context/'state').exists())

    def test_context_containers_do_not_discover_clients_or_projects(self):
        self.um();self.node=dict(schema_version=1,kind='company-context',projects=dict(path='projects',managed=False),clients=dict(path='clients',managed=False))
        (self.context/'node.json').write_text(json.dumps(self.node));self.workflow.options['repository']=str(self.context)
        self.assertEqual([r['id'] for r in declarations(self.workflow)[2]],['context'])

    def test_reverse_metadata_context_reference(self):
        context=self.repo/'um-code';context.mkdir()
        self.workflow.git('init','-b','main',cwd=context)
        (context/'node.json').write_text(json.dumps(dict(schema_version=1,kind='development-context',project=dict(path='..',mount='reference',repository='test/code'))))
        self.store.context['repository']=str(context)
        root,node,entries=declarations(self.workflow)
        self.assertEqual(root,context);self.assertEqual(entries[1]['repository'],str(self.repo))

    def test_secondary_recipe_and_partial_publication_resume(self):
        self.um(collection=True);second=self.context/'second'
        self.workflow.git('config','user.name','Second',cwd=second);self.workflow.git('config','user.email','second@example.invalid',cwd=second)
        remote=self.root/'second.git';self.workflow.git('init','--bare',str(remote),cwd=second);self.workflow.git('remote','set-url','origin',str(remote),cwd=second);self.workflow.git('push','-u','origin','main',cwd=second)
        artifact=self.node['projects'][1]['id'];self.workflow.options['repositories']={artifact:{'test':['true']}}
        self.changed={'context',artifact};todo=self.implement()
        from workflow import Workflow
        original=Workflow.integrate_and_deploy;blocked=[]
        def fail_context(w,ident,run,lock,migrate=False):
            if run.get('role')=='context' and not blocked:blocked.append(True);raise ValueError('Simulated second repository failure')
            return original(w,ident,run,lock,migrate)
        with patch.object(Workflow,'integrate_and_deploy',fail_context):todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'merge_failed')
        published=next(r['published_commit'] for r in todo['workflow']['repositories'] if r['id']==artifact)
        todo=self.run_stage('merge');self.assertEqual(todo['workflow']['phase'],'done',todo['workflow']['message'])
        self.assertEqual(next(r['published_commit'] for r in todo['workflow']['repositories'] if r['id']==artifact),published)
        self.assertEqual(self.workflow.git('log','--format=%s','main',cwd=second).splitlines().count('Task work'),1)

    def test_explicit_retry_expands_legacy_without_losing_its_pr(self):
        self.um();node=self.context/'node.json';saved=node.read_text();node.unlink()
        todo=self.run_stage('implement');legacy=todo['workflow'];self.assertNotIn('repositories',legacy)
        from storage import digest
        snap=self.store.snapshot();record=snap['data']['todos'][0]
        self.store.mutate(dict(actor='Codex',request_id=uuid.uuid4().hex,changes=[dict(collection='todos',id=record['id'],revision=digest(record),record=dict(record,workflow=dict(legacy,phase='implementation_failed')))]),workflow=True)
        node.write_text(saved)
        with patch.object(self.workflow,'command',side_effect=self.agent):todo=self.run_stage('retry')
        self.assertEqual(todo['workflow']['phase'],'ready',todo['workflow']['message'])
        self.assertEqual(todo['workflow']['legacy_repository_attempt']['pr_url'],legacy['pr_url'])
        primary=next(r for r in todo['workflow']['repositories'] if r['role']=='project')
        self.assertEqual(primary['worktree'],legacy['worktree']);self.assertEqual(primary['pr_url'],legacy['pr_url'])
