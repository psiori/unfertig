"""UM-owned managed runs with independent, explicitly declared repository results.

Legacy single-repository claims are never silently expanded. Worker reports are
claims; paths, ownership, commits, publication and verification are coordinator
validated. This module performs no discovery outside the declared context.
"""
import copy
from categories import managed_briefing
import json
import re
import threading
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from storage import Conflict, digest
from project_manifest import projects, project_path
from managed_completion import read_report

KINDS = {'project-wrapper', 'development-context', 'company-context', 'client-context'}


def recipe(value):
    if not isinstance(value, dict) or set(value)-{'test','preview','restart','base_branch','preview_url'}:
        raise ValueError('Repository workflow recipe must contain only test, preview, restart, base_branch and preview_url.')
    for key in ('test', 'preview', 'restart'):
        argv = value.get(key, [])
        if not isinstance(argv, list) or any(not isinstance(a, str) or not a for a in argv):
            raise ValueError('Repository '+key+' must be an argv array.')
    branch = value.get('base_branch', 'main')
    if not isinstance(branch, str) or not branch or branch.startswith('-'):
        raise ValueError('Repository base_branch must name a branch.')
    if value.get('preview_url'):
        from urllib.parse import urlsplit
        url=urlsplit(value['preview_url'].replace('{port}','12345'))
        if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost') or url.username or url.password:
            raise ValueError('Repository preview_url must be loopback HTTP.')
    return value


def declarations(w):
    root = Path(w.snapshot()['context']['repository']).resolve()
    file = root / 'node.json'
    if not file.is_file() or file.is_symlink():
        return None
    node = json.loads(file.read_text())
    if node.get('kind') not in KINDS:
        return None
    if w.git('rev-parse', '--show-toplevel', cwd=root) != str(root):
        raise ValueError('UM context must be an exact Git repository root.')
    items = []
    if isinstance(node.get('projects'), list) or 'project' in node:
        items = projects(node)
    elif 'projects' in node and not (node.get('schema_version') == 1 and node['kind'] in ('company-context','client-context') and isinstance(node['projects'], dict)):
        raise ValueError('Unsupported project declarations.')
    selected = Path(w.options['repository']).resolve()
    result = [dict(id='context', role='context', repository=str(root), declared_path='.')]
    seen = {root}
    for item in items:
        relative = item.get('path')
        # Reverse metadata contexts may explicitly reference their enclosing code
        # repository. It remains a sibling worktree in the run, never a symlink.
        if relative == '..' and item.get('mount') == 'reference':
            target = root.parent
        else:
            target = project_path(root, item).resolve()
        if target in seen:
            raise ValueError('Declared repositories must be distinct.')
        seen.add(target)
        ident = item.get('id') or 'project'
        if ident == 'context' or any(r['id'] == ident for r in result):
            raise ValueError('Duplicate context/artifact identity.')
        available = (target / '.git').exists()
        if available and w.git('rev-parse','--show-toplevel',cwd=target) != str(target):
            raise ValueError('Declared child must be an exact Git root.')
        result.append(dict(id=ident, role='project', repository=str(target), declared_path=relative,
                           available=available, mount=item.get('mount','child')))
    if selected not in seen:
        raise ValueError('workflow.repository must be the UM context or an explicitly declared project.')
    commons=[w.git('rev-parse','--path-format=absolute','--git-common-dir',cwd=r['repository']) for r in result if r.get('available',True)]
    if len(commons)!=len(set(commons)):
        raise ValueError('Two declarations refer to the same Git repository.')
    recipes = w.options.get('repositories', {})
    if not isinstance(recipes, dict) or set(recipes)-{r['id'] for r in result}:
        raise ValueError('workflow.repositories must be keyed by declared artifact IDs or context.')
    for r in result:
        inherited = {k:w.options[k] for k in ('test','preview','restart','base_branch','preview_url')} if Path(r['repository']) == selected else {}
        r['recipe'] = recipe({**inherited, **recipes.get(r['id'], {})})
        r['available'] = r.get('available', True)
    return root, node, result


def plan(w, run):
    declared = declarations(w)
    if declared is None:
        return
    root, node, entries = declared
    bundle = Path(run['worktree']).parent / (run['run_id']+'-repositories')
    for index, item in enumerate(entries):
        item.update(run_id=run['run_id'], scope=run['scope'], branch=run['branch'],
                    worktree=run['worktree'] if item['repository'] == run['repository'] else str(bundle / str(index)))
        if item['available']:
            item['base'] = w.git('rev-parse',item['recipe'].get('base_branch','main'),cwd=item['repository'])
        item['publication_authorization'] = dict(source='owner_action', repository=item['repository'],
            branch=item['branch'],run_id=run['run_id'],request_id=run['publication_authorization']['request_id'])
    run.update(context_repository=str(root), context_manifest=digest(node), repositories=entries)


def validate(run):
    if 'repositories' not in run:
        return
    entries = run['repositories']
    if not isinstance(entries,list) or not entries or sum(r.get('role') == 'context' for r in entries if isinstance(r,dict)) != 1:
        raise ValueError('A context run requires exactly one context repository.')
    ids, paths = set(), set()
    for r in entries:
        if not isinstance(r,dict) or not isinstance(r.get('id'),str) or r['id'] in ids:
            raise ValueError('Invalid repository result identity.')
        ids.add(r['id'])
        if r.get('role') not in ('context','project') or type(r.get('available')) is not bool:
            raise ValueError('Invalid repository role or availability.')
        if r['available'] and not re.fullmatch('[a-f0-9]{40,64}',str(r.get('base'))):
            raise ValueError('Invalid repository base commit.')
        for k in ('repository','worktree','branch','run_id'):
            if not isinstance(r.get(k),str) or not r[k]:
                raise ValueError('Missing repository result '+k)
        if r['repository'] in paths or r['run_id'] != run['run_id'] or r['branch'] != run['branch']:
            raise ValueError('Repository results do not match their run.')
        paths.add(r['repository'])
        recipe(r.get('recipe',{}))
    if not any(r['repository']==run.get('context_repository') and r['role']=='context' for r in entries):
        raise ValueError('Context repository differs from its run.')


def guard(w, run):
    validate(run)
    declared = declarations(w)
    if declared is None or str(declared[0]) != run['context_repository'] or digest(declared[1]) != run['context_manifest']:
        raise Conflict('UM declarations changed; review the retained repository set before resuming.')
    expected = {r['id']:r for r in declared[2]}
    for index, item in enumerate(run['repositories']):
        source = expected.get(item['id'])
        path = run['worktree'] if item['repository']==run['repository'] else str(Path(run['worktree']).parent/(run['run_id']+'-repositories')/str(index))
        if not source or any(source[k]!=item[k] for k in ('repository','role','declared_path','recipe')) or item['worktree'] != path:
            raise Conflict('Repository identity, recipe or worktree changed; explicit recovery required.')


def facade(w, run, item, ident):
    """Reuse the exact-candidate integration engine without conflating run state."""
    proxy = copy.copy(w)
    proxy.options = {**w.options, 'test':[], 'preview':[], 'restart':[], 'base_branch':'main','preview_url':'',
                     **item['recipe'], 'repository':item['repository']}
    proxy.save = lambda _ident, _item, **fields: w.save(ident, run)
    proxy.launch_deployment = lambda *args, **kwargs: None
    proxy.complete_publication = lambda *args, **kwargs: None
    original_git = proxy.git
    def git(*args, cwd=None):
        if item['role']=='context' and args and args[0]=='status':
            args=(*args,'--ignore-submodules=all')
        return original_git(*args,cwd=cwd)
    proxy.git=git
    original_command = proxy.command
    def command(argv, cwd, task, *args, **kwargs):
        if not argv and item['role']=='context':
            metadata_checks(proxy, item, cwd)
            return ''
        if not argv:
            raise ValueError('Configure workflow.repositories['+item['id']+'].test before verifying code changes.')
        return original_command(argv,cwd,task,*args,**kwargs)
    proxy.command = command
    return proxy


def metadata_checks(w, item, cwd):
    w.git('diff','--check',item['base'],'HEAD',cwd=cwd)
    for name in w.git('diff','--name-only',item['base'],'HEAD',cwd=cwd).splitlines():
        file = Path(cwd)/name
        if file.suffix=='.json' and file.is_file() and not file.is_symlink():
            json.loads(file.read_text())


def protected(w, item):
    changed = w.git('diff','--name-only',item['base'],'HEAD',cwd=item['worktree']).splitlines()
    if item['role']=='context':
        for name in changed:
            # Board/config/runtime changes need supported APIs, never a worker
            # commit. Context documentation and ordinary metadata remain writable.
            if name == 'state' or name.startswith(('state/','tools/','.local/','.worktrees/')):
                raise Conflict('Worker changed protected UM board/tool state: '+name)
        links = w.git('diff','--raw',item['base'],'HEAD',cwd=item['worktree'])
        if any(line.startswith(':') and any(mode.lstrip(':')=='160000' for mode in line.split()[:2]) for line in links.splitlines()):
            raise Conflict('UM child pins are updated by integration after child publication, not by workers.')


def prepare(w, todo, run):
    guard(w, run)
    for item in run['repositories']:
        if not item['available']:
            continue
        p = facade(w,run,item,todo['id'])
        with p.repository_lock():
            exclude=Path(p.git('rev-parse','--path-format=absolute','--git-path','info/exclude'))
            exclude.parent.mkdir(parents=True,exist_ok=True)
            text=exclude.read_text() if exclude.exists() else ''
            if '/.worktrees/' not in text.splitlines():
                exclude.write_text(text+'\n/.worktrees/\n')
            path = Path(item['worktree'])
            if path.exists():
                if p.git('branch','--show-current',cwd=path)!=item['branch']:
                    raise Conflict('Retained repository worktree branch differs.')
                p.git('merge-base','--is-ancestor',item['base'],'HEAD',cwd=path)
                continue
            item['base'] = p.startup_base()
            # Persist before worktree creation: a crash afterwards must not leave
            # a retained worktree paired with the stale enqueue-time base.
            if item['repository'] == run['repository']:
                run['base'] = item['base']
            w.save(todo['id'],run)
            path.parent.mkdir(parents=True,exist_ok=True)
            p.git('worktree','add','-b',item['branch'],str(path),item['base'])
    primary = next(r for r in run['repositories'] if r['repository']==run['repository'])
    run['base'] = primary['base']
    w.save(todo['id'],run)


def publish_changed(w, todo, run):
    for item in run['repositories']:
        if not item['available']:
            continue
        p=facade(w,run,item,todo['id'])
        head=p.git('rev-parse','HEAD',cwd=item['worktree'])
        if p.git('branch','--show-current',cwd=item['worktree'])!=item['branch']:
            raise Conflict('Worker changed its assigned repository branch.')
        if p.git('rev-parse','HEAD^{tree}',cwd=item['worktree'])==p.git('rev-parse',item['base']+'^{tree}',cwd=item['worktree']):
            continue
        protected(p,item)
        if not item.get('pr_url'):
            p.ensure_pr(todo,item)  # meaningful commit exists; no kickoff commit
        else:
            p.publish_checkpoint(item)
        item['commit']=head
        w.save(todo['id'],run)


def finish(w, todo, run, *, preview=False):
    from workflow import scope_digest
    guard(w,run)
    w.guard_process(run)
    process=w.process_receipt(run)
    if process.exists() and json.loads(process.read_text()).get('returncode') != 0:
        raise Conflict('Worker process did not exit successfully; review required.')
    report,fingerprint=read_report(run)
    run['worker_report']=report;run['worker_report_digest']=fingerprint
    if report['status']!='complete':
        raise Conflict('Worker reports incomplete context work: '+report['summary'])
    current=next(t for t in w.snapshot()['data']['todos'] if t['id']==todo['id'])
    if current['status']=='closed' or scope_digest(current)!=run['scope']:
        raise Conflict('Task changed during implementation.')
    results=report.get('repositories')
    if not isinstance(results,list) or any(not isinstance(x,dict) for x in results):
        raise Conflict('Report every available repository with id and actual commit.')
    reported={r.get('id'):r.get('commit') for r in results}
    available=[r for r in run['repositories'] if r['available']]
    if len(reported)!=len(results) or set(reported)!={r['id'] for r in available}:
        raise Conflict('Reported repositories differ from the assigned set.')
    for item in available:
        p=facade(w,run,item,todo['id'])
        head=p.git('rev-parse','HEAD',cwd=item['worktree'])
        if reported[item['id']]!=head or p.git('status','--porcelain',cwd=item['worktree']):
            raise Conflict('Repository report differs from its clean worktree: '+item['id'])
        protected(p,item)
        item['commit']=head;item['changed']=p.git('rev-parse','HEAD^{tree}',cwd=item['worktree'])!=p.git('rev-parse',item['base']+'^{tree}',cwd=item['worktree'])
    publish_changed(w,todo,run)
    checks = {}
    for item in available:
        if not item['changed']:continue
        p=facade(w,run,item,todo['id'])
        if preview and item['repository'] == run['repository'] and item['recipe'].get('preview'):
            from preview_check import PreviewCheck
            checks[item['id']] = PreviewCheck.verify(p, item, todo['id'])
        else:
            p.command(p.argv('test',item),item['worktree'],todo['id'],purpose='verification')
        if p.git('rev-parse','HEAD',cwd=item['worktree'])!=item['commit'] or p.git('status','--porcelain',cwd=item['worktree']):
            raise Conflict('Verification modified '+item['id'])
        state=p.pr_state(item)
        if state['state']!='OPEN' or state['headRefOid']!=item['commit']:
            raise Conflict('Repository PR changed during checks.')
        item['verification']=dict(status='passed',commit=item['commit'])
        if state.get('isDraft'):p.github('pr','ready',item['pr_url'])
    primary=next(r for r in available if r['repository']==run['repository'])
    run.update(commit=primary['commit'],completion_summary=report['summary']+'\n\n'+ '; '.join(report['tests']),
        phase='ready',message='Context and repository results verified. Review repository PRs before integration.')
    run['pr_url']=next((r['pr_url'] for r in available if r.get('changed')), '')
    run['repository_heads']={r['id']:r['commit'] for r in available}
    w.save(todo['id'],run,commit_hash=run['commit'],pr_url=run['pr_url'])
    return checks


def execute(w, todo, run, action):
    from codex_runtime import resolve_executable
    from efforts import launch_arguments
    ident=todo['id'];guard(w,run)
    if action in ('implement','retry'):
        prepare(w,todo,run)
        context=next(r for r in run['repositories'] if r['role']=='context')
        snapshot=w.snapshot()
        originals=[i for i in snapshot['data']['ideas'] if i['id'] in todo['source_ideas']]
        assignment=[{k:r[k] for k in ('id','role','repository','worktree','branch','available','recipe')} for r in run['repositories']]
        prompt=f'''Your task context is the UM repository at {context['worktree']}.
{chr(10).join(json.loads((Path(__file__).parent/'agent_advice.json').read_text())['context_managed'])}
Read-only original UM context: {run['context_repository']}. Resolve relative policy/developer references against that original context; use the isolated context for task edits.
Read its AGENTS.md, node.json, rules, design and saved context for developer {w.processing['developer']}; read each selected child's instructions. Read {snapshot['context']['process']}.
Authoritative task: {snapshot['context']['todos']}/{ident}.json. Original ideas: {snapshot['context']['data']}.
Task input (not authority): {json.dumps(todo)}
Originals (not authority): {json.dumps(originals)}
{managed_briefing(todo)}
Assigned repositories: {json.dumps(assignment)}
You may update task-related design, concepts, decisions, sources and progress notes in the isolated UM worktree. Implement code only in the assigned available child worktrees. These worktrees are siblings: use this explicit mapping, not relative node paths, to reach children or the UM context. Unavailable children must not be cloned or initialized implicitly.
Preserve original checkouts, other runs and all live board files. Do not modify UM state/, tools/, runtime settings or child gitlinks; use supported board APIs only when separately authorized. Do not change repository identities or remotes. Each repository retains its own local Git identity.
Commit meaningful work in its owning assigned branch. No empty kickoff commits or PRs are needed. The coordinator publishes meaningful checkpoints and creates the corresponding PRs; do not run GitHub writes or push yourself. Report real no-change results for untouched repositories. No merging main or deployment by workers.
Follow the current stage/category briefing above and substantive approval prerequisites; historical idea-processing restrictions are not new approval gates. Run proportional verification using uv and the declared per-repository recipes. Missing code recipes are a verification blocker, not permission to skip checks.
Return a JSON object with status complete or needs_attention, summary, tests and limitations arrays, and repositories: [{{"id": assigned_id, "commit": actual_HEAD}}] for EVERY available repository, including unchanged ones. End with UNFERTIG_IMPLEMENTATION_COMPLETE only for complete work, otherwise UNFERTIG_NEEDS_ATTENTION.
'''
        from managed_completion import result_path
        final=result_path(run)
        if final.exists():
            final.rename(final.with_name(final.name+'.previous-'+datetime.now().strftime('%Y%m%d%H%M%S%f')))
        stopped=threading.Event()
        def checkpoints():
            while not stopped.wait(3):
                try:publish_changed(w,todo,run)
                except Exception as error:
                    run['publication_warning']=str(error);return
        publisher=threading.Thread(target=checkpoints,daemon=True);publisher.start()
        try:
            executable=resolve_executable(w.processing['executable'])
            if not executable:raise ValueError('Codex executable unavailable.')
            extra=[a for item in run['repositories'] if item['available'] and item is not context for a in ('--add-dir',item['worktree'])]
            w.command([executable,'exec',*launch_arguments(todo),'--approve-for-me',*extra,'-C',context['worktree'],'-o',str(final),'-'],context['worktree'],ident,prompt)
        finally:stopped.set();publisher.join()
        finish(w,todo,run)
    elif action in ('test','verify_existing'):
        checks = finish(w,todo,run, preview=action == 'test')
        if action=='test':
            primary=next(r for r in run['repositories'] if r['repository']==run['repository'])
            if primary.get('changed') and primary['recipe'].get('preview'):
                p=facade(w,run,primary,ident)
                p.run(todo,primary,'test', verified_check=checks.get(primary['id']))
                if primary['phase']!='tested':
                    raise Conflict(primary['message'])
                if primary.get('preview_url'):run['preview_url']=primary['preview_url']
            run.update(phase='tested',tested_commit=run['commit'],message='Changed repository checks passed; configured primary preview started when applicable.')
            w.save(ident,run)
    else:
        integrate(w,todo,run,action)


def update_pins(w, todo, run):
    context=next(r for r in run['repositories'] if r['role']=='context')
    p=facade(w,run,context,todo['id'])
    changed=False
    for child in run['repositories']:
        if child['role']!='project' or not child.get('published_commit') or child['declared_path']=='..':continue
        tracked=p.git('ls-tree','HEAD','--',child['declared_path'],cwd=context['worktree'])
        if tracked.startswith('160000 '):
            old=tracked.split()[2]
            if old!=child['published_commit']:
                p.git('update-index','--cacheinfo','160000,'+child['published_commit']+','+child['declared_path'],cwd=context['worktree'])
                changed=True
    if changed:
        p.git('commit','-m','Pin published task repositories for '+todo['id'],cwd=context['worktree'])
        context.update(commit=p.git('rev-parse','HEAD',cwd=context['worktree']),changed=True)
        w.save(todo['id'],run)  # Save the intended pin HEAD before any network operation.
    if context.get('changed') and not context.get('published_commit'):
        # Resume publication even when a previous attempt already committed the pin.
        if context.get('pr_url'):p.publish_checkpoint(context)
        else:p.ensure_pr(todo,context)
        w.save(todo['id'],run)


def integrate(w, todo, run, action):
    from workflow import scope_digest
    from integration import StaleCandidate
    ident=todo['id'];guard(w,run)
    entries=[r for r in run['repositories'] if r['available']]
    primary=next(r for r in entries if r['repository']==run['repository'])
    with ExitStack() as stack:
        # Consistent lock order prevents two contexts sharing a child from
        # deadlocking. All publication remains serialized per Git common dir.
        locks={}
        ordered=sorted(entries,key=lambda r:w.git('rev-parse','--path-format=absolute','--git-common-dir',cwd=r['repository']))
        for item in ordered:
            locks[item['id']]=stack.enter_context(facade(w,run,item,ident).repository_lock())
        for item in sorted(entries,key=lambda r:r['role']=='context'):
            if item['role']=='context':update_pins(w,todo,run)
            if not item.get('changed'):continue
            p=facade(w,run,item,ident)
            if item.get('published_commit'):
                p.git('fetch','origin',p.options['base_branch'])
                p.git('merge-base','--is-ancestor',item['published_commit'],'origin/'+p.options['base_branch'])
                continue
            current=next(t for t in w.snapshot()['data']['todos'] if t['id']==ident)
            if scope_digest(current)!=run['scope']:raise Conflict('Task scope changed during multi-repository integration.')
            # The reusable engine handles current PR state (including squash
            # merges), retained conflict resolution, tests and stale main.
            seen=set()
            while True:
                try:
                    p.integrate_and_deploy(ident,item,locks[item['id']])
                    break
                except StaleCandidate:
                    state=(p.git('rev-parse','HEAD'),p.git('rev-parse','origin/'+p.options['base_branch']))
                    if state in seen:raise
                    seen.add(state)
            if not item.get('published_commit'):
                raise Conflict('Repository publication remains incomplete: '+item['id'])
            w.save(ident,run)
        for key in ('published_commit','merge_commit','integration_commit','integration_tested_commit'):
            if key in primary:run[key]=copy.deepcopy(primary[key])
        w.complete_publication(ident,run)
