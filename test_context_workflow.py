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
    def test_preview_checks_primary_once_and_starts_real_listener(self):
        import urllib.request
        self.um(); self.changed = {'context', 'project'}; self.implement()
        command = self.workflow.command
        calls = []
        def count(argv, *args, **kwargs):
            calls.append(tuple(argv))
            return command(argv, *args, **kwargs)
        from context_workflow import metadata_checks
        with patch.object(self.workflow, 'command', side_effect=count), \
             patch('context_workflow.metadata_checks', wraps=metadata_checks) as metadata:
            todo = self.run_stage('test')
        self.assertEqual(todo['workflow']['phase'], 'tested', todo['workflow']['message'])
        self.assertEqual(calls.count(tuple(self.workflow.options['test'])), 1)
        self.assertEqual(metadata.call_count, 1)
        with urllib.request.urlopen(todo['workflow']['preview_url'], timeout=2) as response:
            self.assertEqual(response.status, 200)

    def test_preview_missing_or_invalidated_evidence_rechecks(self):
        from context_workflow import finish
        self.um(); self.changed = {'project'}; self.implement()
        command = self.workflow.command
        for missing in (True, False):
            calls = []
            def count(argv, *args, **kwargs):
                calls.append(tuple(argv))
                return command(argv, *args, **kwargs)
            def checked(*args, **kwargs):
                result = finish(*args, **kwargs)
                return {} if missing else result
            with patch.object(self.workflow, 'command', side_effect=count), \
                 patch('context_workflow.finish', side_effect=checked), \
                 patch('preview_check.PreviewCheck.consume', return_value=False):
                todo = self.run_stage('test')
            self.assertEqual(todo['workflow']['phase'], 'tested', todo['workflow']['message'])
            self.assertEqual(calls.count(tuple(self.workflow.options['test'])), 2)

    def test_preview_failure_blocks_listener_and_other_repository_checks_remain(self):
        self.um(); self.changed = {'context', 'project'}; self.implement()
        with patch.object(self.workflow, 'command', side_effect=ValueError('injected test failure')):
            todo = self.run_stage('test')
        self.assertNotEqual(todo['workflow']['phase'], 'tested')
        self.assertIn('injected test failure', todo['workflow']['message'])
        self.assertFalse(self.workflow.previews)

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
    def stage_task(self, category):
        from storage import digest
        todo = self.store.snapshot()['data']['todos'][0]
        record = dict(todo, category=category, description='Planning only in this run; implementation requires separate authorization.')
        self.store.mutate(dict(actor='SL', request_id=uuid.uuid4().hex, changes=[dict(
            collection='todos', id=todo['id'], revision=digest(todo), record=record)]))
        return record

    def test_saved_category_after_prepare_updates_all_scopes_and_prompt(self):
        from storage import digest
        import context_workflow
        self.um(); self.changed={'context','project'}
        original=context_workflow.prepare; prompts=[]
        def prepare(*args):
            original(*args)
            todo=self.store.snapshot()['data']['todos'][0]
            self.store.mutate(dict(actor='Owner',request_id=uuid.uuid4().hex,changes=[dict(
                collection='todos',id=todo['id'],revision=digest(todo),record=dict(todo,category='concept'))]),owner_editor=True)
        def agent(argv,cwd,ident,stdin=None,**kwargs):
            if 'exec' in argv: prompts.append(stdin)
            return self.agent(argv,cwd,ident,stdin,**kwargs)
        with patch('context_workflow.prepare',side_effect=prepare), patch.object(self.workflow,'command',side_effect=agent):
            todo=self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'],'ready',todo['workflow']['message'])
        self.assertIn('Work category: Concept',prompts[0])
        self.assertTrue(all(r['scope']==todo['workflow']['scope'] for r in todo['workflow']['repositories']))
        self.assertEqual(len(todo['workflow']['scope_attempts']),1)

    def test_saved_scope_during_combined_checks_cannot_finish_old_result(self):
        from storage import digest
        import context_workflow
        self.um(); self.changed={'context','project'}
        original=context_workflow.checked
        def checks(*args,**kwargs):
            result=original(*args,**kwargs)
            todo=self.store.snapshot()['data']['todos'][0]
            self.store.mutate(dict(actor='Owner',request_id=uuid.uuid4().hex,changes=[dict(
                collection='todos',id=todo['id'],revision=digest(todo),record=dict(todo,description='Revised result required'))]),owner_editor=True)
            return result
        with patch.object(self.workflow,'command',side_effect=self.agent), patch('context_workflow.checked',side_effect=checks):
            todo=self.run_stage('implement')
        self.assertNotEqual(todo['workflow']['phase'],'ready')
        self.assertNotEqual(todo['status'],'closed')
        self.assertIn('Retry implementation',todo['workflow']['message'])

    def test_um_profile_is_reloaded_after_prepare_and_frozen_on_launch(self):
        from storage import digest
        import context_workflow
        self.um(); self.changed={'project'}
        original=context_workflow.prepare; captured=[]
        def select(value):
            t=self.store.snapshot()['data']['todos'][0]
            self.store.mutate(dict(actor='SL',request_id=uuid.uuid4().hex,changes=[dict(
                collection='todos',id=t['id'],revision=digest(t),record=dict(t,execution_profile=value))]))
        def prepare(*args):
            original(*args); select('terra-medium')
        def agent(argv,cwd,ident,stdin=None,**kwargs):
            captured.append((argv,stdin)); select('astra-high')
            return self.agent(argv,cwd,ident,stdin,**kwargs)
        with patch('context_workflow.prepare',side_effect=prepare), patch.object(self.workflow,'command',side_effect=agent):
            todo=self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'],'ready',todo['workflow']['message'])
        self.assertEqual(todo['execution_profile'],'astra-high')
        self.assertEqual(todo['workflow']['agent_runs'][-1]['profile'],'terra-medium')
        self.assertEqual(captured[0][0][captured[0][0].index('-m')+1],'gpt-5.6-terra')
        self.assertIn('Agent effort: Terra medium',captured[0][1])
        self.assertIn('well-structured, concise',captured[0][1])
        self.assertNotIn('TRANSPORTS.md',captured[0][1])

    def test_um_worker_receives_authorized_code_stage_with_original_provenance(self):
        from categories import managed_briefing
        self.um(); self.changed = {'project'}
        record = self.stage_task('implementation'); prompts = []
        def agent(argv, cwd, ident, stdin=None, **kwargs):
            if 'exec' in argv:
                prompts.append(stdin)
            return self.agent(argv, cwd, ident, stdin, **kwargs)
        with patch.object(self.workflow, 'command', side_effect=agent):
            todo = self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'], 'ready', todo['workflow']['message'])
        self.assertEqual(todo['description'], record['description'])
        self.assertIn(managed_briefing(record), prompts[0])
        self.assertIn(record['description'], prompts[0])
        self.assertTrue(next(r for r in todo['workflow']['repositories'] if r['id']=='project')['changed'])

    def test_um_concept_keeps_context_only_scope(self):
        from categories import managed_briefing
        self.um(); record = self.stage_task('concept'); prompts = []
        def agent(argv, cwd, ident, stdin=None, **kwargs):
            if 'exec' in argv:
                prompts.append(stdin)
            return self.agent(argv, cwd, ident, stdin, **kwargs)
        with patch.object(self.workflow, 'command', side_effect=agent):
            todo = self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'], 'ready', todo['workflow']['message'])
        self.assertIn(managed_briefing(record), prompts[0])
        self.assertNotIn('this action supplies that implementation authorization', prompts[0])
        self.assertFalse(next(r for r in todo['workflow']['repositories'] if r['id']=='project')['changed'])
        todo = self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'], 'done', todo['workflow']['message'])
        self.assertEqual(todo['status'], 'closed')
        context = next(r for r in todo['workflow']['repositories'] if r['id']=='context')
        self.assertTrue(context['published_commit'])
        self.assertEqual(len(self.prs), 1)

    def test_implementation_category_does_not_accept_incomplete_report(self):
        self.um(); self.stage_task('implementation')
        def agent(argv, cwd, ident, stdin=None, **kwargs):
            result = self.agent(argv, cwd, ident, stdin, **kwargs)
            if 'exec' in argv:
                path = Path(argv[argv.index('-o')+1])
                report = json.loads(path.read_text().split('\nUNFERTIG')[0])
                report.update(status='needs_attention', summary='Named design approval is missing.')
                path.write_text(json.dumps(report)+'\nUNFERTIG_NEEDS_ATTENTION')
            return result
        with patch.object(self.workflow, 'command', side_effect=agent):
            todo = self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'], 'implementation_failed')
        self.assertIn('Named design approval is missing', todo['workflow']['message'])

    def test_context_only_one_pr_and_integration(self):
        self.um();todo=self.implement();self.assertEqual(len(self.prs),1);self.assertFalse((self.context/'design/proposal.md').exists())
        todo=self.run_stage('merge');self.assertEqual(todo['workflow']['phase'],'done',todo['workflow']['message']);self.assertTrue((self.context/'design/proposal.md').exists());self.assertEqual(todo['status'],'closed')
    def test_collection_missing_child_allows_context(self):
        self.um(collection=True);shutil.rmtree(self.context/'second');todo=self.implement();self.assertEqual(len(todo['workflow']['repositories']),3);self.assertEqual(len(self.prs),1);self.assertFalse((self.context/'second').exists())
    def test_child_and_context_publish_and_pin(self):
        self.um(pinned=True);self.changed={'context','project'};todo=self.implement();self.assertEqual(len(self.prs),2)
        with patch.object(self.workflow,'launch_deployment') as deploy:todo=self.run_stage('merge')
        self.assertFalse(deploy.called,todo['workflow']['message']);repos={x['id']:x for x in todo['workflow']['repositories']};self.assertTrue(repos['context'].get('published_commit'),todo['workflow']['message']);self.assertEqual(self.workflow.git('ls-tree','HEAD','code',cwd=self.context).split()[2],repos['project']['published_commit'])
    def test_relaxed_child_publication_precedes_context_pin_with_deferred_sync(self):
        self.um(pinned=True)
        self.workflow.options['integration'] = dict(mode='relaxed', automatic_repair=False, max_attempts=3)
        self.changed={'context','project'};self.implement()
        context_before=self.workflow.git('rev-parse','HEAD',cwd=self.context)
        todo=self.run_stage('merge');run=todo['workflow']
        self.assertEqual(run['phase'],'done',run['message'])
        repos={r['id']:r for r in run['repositories']}
        pin=self.workflow.git('ls-tree',repos['context']['published_commit'],'code',cwd=self.context).split()[2]
        self.assertEqual(pin,repos['project']['published_commit'])
        self.assertEqual(self.workflow.git('rev-parse','HEAD',cwd=self.context),context_before)
        self.assertTrue(all(r['checkout_sync']['status']=='deferred' for r in repos.values()))

    def test_relaxed_child_sync_drift_is_recognized_as_the_published_dependency(self):
        self.um(pinned=True)
        self.workflow.options['integration'] = dict(mode='relaxed', automatic_repair=True, max_attempts=3)
        self.changed={'context','project'};self.implement()
        todo=self.run_stage('merge');run=todo['workflow']
        self.assertEqual(run['phase'],'done',run['message'])
        repos={r['id']:r for r in run['repositories']}
        pin=self.workflow.git('ls-tree',repos['context']['published_commit'],'code',cwd=self.context).split()[2]
        self.assertEqual(pin,repos['project']['published_commit'])
        self.assertEqual(repos['context']['checkout_sync']['status'],'deferred')

    def test_relaxed_pin_generation_preserves_unrelated_staged_worker_edits(self):
        self.um(pinned=True)
        self.workflow.options['integration'] = dict(mode='relaxed', automatic_repair=False, max_attempts=3)
        self.changed={'context','project'};todo=self.implement()
        context=next(r for r in todo['workflow']['repositories'] if r['role']=='context')
        path=Path(context['worktree'])/'user-notes.txt';path.write_text('User staging')
        self.workflow.git('add','user-notes.txt',cwd=context['worktree'])
        before=self.workflow.git('diff','--cached',cwd=context['worktree'])
        result=self.run_stage('merge');run=result['workflow']
        self.assertEqual(run['phase'],'merge_failed')
        self.assertIn('Context worktree has edits',run['message'])
        self.assertEqual(self.workflow.git('diff','--cached',cwd=context['worktree']),before)
        project=next(r for r in run['repositories'] if r['role']=='project')
        self.assertTrue(project.get('published_commit'))
        self.assertFalse(next(r for r in run['repositories'] if r['role']=='context').get('published_commit'))

    def test_relaxed_pin_commit_interruption_recovers_index_without_republishing_child(self):
        self.um(pinned=True)
        self.workflow.options['integration'] = dict(mode='relaxed', automatic_repair=False, max_attempts=3)
        self.changed={'context','project'};self.implement()
        with patch('context_workflow.synchronize_pin_index',side_effect=OSError('Interrupted pin index synchronization')):
            todo=self.run_stage('merge')
        first={r['id']:r for r in todo['workflow']['repositories']}
        self.assertEqual(todo['workflow']['phase'],'merge_failed')
        self.assertEqual(first['context']['pin_update']['status'],'pending')
        todo=self.run_stage('merge');run=todo['workflow']
        self.assertEqual(run['phase'],'done',run['message'])
        final={r['id']:r for r in run['repositories']}
        self.assertEqual(final['project']['published_commit'],first['project']['published_commit'])
        self.assertEqual(final['context']['pin_update']['status'],'complete')
        self.assertEqual(self.workflow.git('log','--format=%s',cwd=final['context']['worktree']).splitlines().count('Pin published task repositories for T0001'),1)

    def test_pin_commit_compare_and_swap_preserves_a_concurrent_worker_commit(self):
        from workflow import Workflow
        self.um(pinned=True)
        self.workflow.options['integration'] = dict(mode='relaxed', automatic_repair=False, max_attempts=3)
        self.changed={'context','project'};todo=self.implement()
        context=next(r for r in todo['workflow']['repositories'] if r['role']=='context')
        original=Workflow.git;advanced=[]
        def git(w,*args,**kwargs):
            if args[0]=='update-ref' and args[1]=='refs/heads/'+context['branch'] and not advanced:
                advanced.append(True)
                original(w,'commit','--allow-empty','-m','Concurrent user checkpoint',cwd=context['worktree'])
            return original(w,*args,**kwargs)
        with patch.object(Workflow,'git',new=git):result=self.run_stage('merge')
        self.assertEqual(result['workflow']['phase'],'merge_failed')
        self.assertEqual(self.workflow.git('log','-1','--format=%s',cwd=context['worktree']),'Concurrent user checkpoint')
        self.assertFalse(next(r for r in result['workflow']['repositories'] if r['role']=='context').get('published_commit'))

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

    def test_worker_cannot_remove_a_child_pin(self):
        self.um(pinned=True);todo=self.implement();context=todo['workflow']['repositories'][0]
        self.workflow.git('rm','--cached','code',cwd=context['worktree'])
        self.workflow.git('commit','-m','Remove child pin',cwd=context['worktree'])
        from context_workflow import protected
        with self.assertRaisesRegex(Conflict,'child pins'):
            protected(self.workflow,context)

    def context_commit(self, name):
        path=self.context/name;path.write_text(name)
        self.workflow.git('add',name,cwd=self.context)
        self.workflow.git('commit','-m',name,cwd=self.context)
        return self.workflow.git('rev-parse','HEAD',cwd=self.context)

    def test_local_ahead_context_inherits_commits_without_pushing_main(self):
        self.um();remote=self.workflow.git('rev-parse','origin/main',cwd=self.context)
        local=self.context_commit('unpublished-context.txt')
        todo=self.implement();item=todo['workflow']['repositories'][0]
        self.assertEqual(item['base'],local)
        self.assertEqual((Path(item['worktree'])/'unpublished-context.txt').read_text(),'unpublished-context.txt')
        self.assertEqual(self.workflow.git('ls-remote','origin','refs/heads/main',cwd=self.context).split()[0],remote)
        body=(Path(item['worktree']).parent/(item['run_id']+'-pr.md')).read_text()
        self.assertIn(local+' unpublished-context.txt',body)
        self.assertIn('They predate this task',body)
        todo=self.run_stage('merge')
        self.assertEqual(todo['workflow']['phase'],'done',todo['workflow']['message'])
        self.assertEqual(self.workflow.git('log','--format=%s','main',cwd=self.context).splitlines().count('unpublished-context.txt'),1)

    def test_local_ahead_without_task_changes_does_not_publish_context(self):
        self.um();local=self.context_commit('board-history.txt');self.changed={'project'}
        todo=self.implement();context=todo['workflow']['repositories'][0]
        self.assertEqual(context['base'],local);self.assertFalse(context['changed'])
        self.assertNotIn('pr_url',context);self.assertEqual(len(self.prs),1)

    def test_remote_ahead_selects_remote_without_moving_local_main(self):
        self.um();baseline=self.workflow.git('rev-parse','HEAD',cwd=self.context)
        remote=self.context_commit('remote-change.txt')
        self.workflow.git('push','origin','main',cwd=self.context)
        self.workflow.git('reset','--hard',baseline,cwd=self.context)  # disposable fixture only
        todo=self.implement();item=todo['workflow']['repositories'][0]
        self.assertEqual(item['base'],remote)
        self.assertEqual(self.workflow.git('rev-parse','main',cwd=self.context),baseline)
        self.assertTrue((Path(item['worktree'])/'remote-change.txt').is_file())

    def test_queued_run_selects_latest_local_commit_at_preparation(self):
        from context_workflow import prepare
        self.um();selected=[]
        def advance(w,todo,run):
            old=run['repositories'][0]['base'];new=self.context_commit('queued-change.txt')
            self.assertNotEqual(old,new);selected.append(new)
            return prepare(w,todo,run)
        with patch('context_workflow.prepare',side_effect=advance):todo=self.implement()
        self.assertEqual(todo['workflow']['repositories'][0]['base'],selected[0])

    def test_divergence_stops_before_agent_with_actionable_error(self):
        self.um();baseline=self.workflow.git('rev-parse','HEAD',cwd=self.context)
        self.context_commit('remote-change.txt');self.workflow.git('push','origin','main',cwd=self.context)
        self.workflow.git('reset','--hard',baseline,cwd=self.context)
        local=self.context_commit('local-change.txt')
        with patch.object(self.workflow,'command') as command:todo=self.run_stage('implement')
        command.assert_not_called();self.assertEqual(todo['workflow']['phase'],'implementation_failed')
        self.assertIn('diverged main',todo['workflow']['message']);self.assertIn('Integrate both histories',todo['workflow']['message'])
        self.assertEqual(self.workflow.git('rev-parse','HEAD',cwd=self.context),local)
        self.assertFalse(Path(todo['workflow']['repositories'][0]['worktree']).exists())

    def test_interrupted_creation_retains_saved_base_and_worktree_on_retry(self):
        from workflow import Workflow
        self.um();local=self.context_commit('unpublished-context.txt');original=Workflow.git;failed=[]
        def interrupt(w,*args,**kwargs):
            result=original(w,*args,**kwargs)
            if args[:2]==('worktree','add') and w.options['repository']==str(self.context) and not failed:
                saved=self.store.snapshot()['data']['todos'][0]['workflow']['repositories'][0]
                self.assertEqual(saved['base'],local)  # durable before creation
                failed.append(True);raise OSError('Interrupted after worktree creation')
            return result
        with patch.object(Workflow,'git',interrupt):todo=self.run_stage('implement')
        self.assertEqual(todo['workflow']['phase'],'implementation_failed')
        item=todo['workflow']['repositories'][0];path=Path(item['worktree'])
        self.assertTrue(path.is_dir());self.context_commit('later-main.txt')
        (path/'retained.txt').write_text('Retained worker work')
        self.workflow.git('add','retained.txt',cwd=path);self.workflow.git('commit','-m','Retained work',cwd=path)
        with patch.object(self.workflow,'command',side_effect=self.agent):todo=self.run_stage('retry')
        self.assertEqual(todo['workflow']['phase'],'ready',todo['workflow']['message'])
        self.assertEqual(todo['workflow']['repositories'][0]['base'],local)
        self.assertEqual((path/'retained.txt').read_text(),'Retained worker work')
        self.assertFalse((path/'later-main.txt').exists())

    def test_git_execution_error_is_not_misreported_as_divergence(self):
        from workflow import Workflow
        from integration import GitFailure
        import subprocess
        self.um();original=Workflow.git
        def fail(w,*args,**kwargs):
            if args[:2]==('merge-base','--is-ancestor'):
                raise GitFailure(args,subprocess.CompletedProcess(args,128,'','object lookup failed'))
            return original(w,*args,**kwargs)
        with patch.object(Workflow,'git',fail):todo=self.run_stage('implement')
        self.assertIn('object lookup failed',todo['workflow']['message'])
        self.assertNotIn('diverged',todo['workflow']['message'])

    def test_context_pin_publication_resumes_without_new_pin_change(self):
        from workflow import Workflow
        self.um(pinned=True);self.changed={'context','project'};self.implement()
        original=Workflow.publish_checkpoint;calls=[]
        def lost(proxy,run):
            old=run.get('publication',{}).copy()
            result=original(proxy,run)
            if Path(run['repository'])==self.context:
                calls.append(run['commit'])
                if len(calls)==1:
                    run['publication']=old
                    raise ValueError('Lost context pin publication response')
            return result
        with patch.object(Workflow,'publish_checkpoint',lost):
            failed=self.run_stage('merge')
            self.assertEqual(failed['workflow']['phase'],'merge_failed')
            published=next(r['published_commit'] for r in failed['workflow']['repositories'] if r['role']=='project')
            completed=self.run_stage('merge')
        self.assertEqual(completed['status'],'closed',completed['workflow']['message'])
        context=next(r for r in completed['workflow']['repositories'] if r['role']=='context')
        self.assertEqual(calls,[context['commit'],context['commit']])
        self.assertEqual(context['publication']['commit'],context['commit'])
        self.assertEqual(next(r['published_commit'] for r in completed['workflow']['repositories'] if r['role']=='project'),published)
