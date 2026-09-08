const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
test('leading icon requires fresh verified local implementation activity',async()=>{
  const events={};let poll, now=1000, failure=false, pending=false, resolve;
  let result={enabled:true,busy:true,runs:{T0001:{phase:'implementing'}}};
  const makeIcon=()=>({dataset:{workflowIcon:'T0001'},classList:{toggle(_,value){this.running=value;}},setAttribute(_,value){this.label=value;}});
  let icon=makeIcon();
  const context={AbortSignal,Date:{now:()=>now},token:'token',data:{todos:[{id:'T0001',status:'started'}]},
    document:{querySelectorAll:s=>s==='[data-workflow-icon]'?[icon]:[],addEventListener:(n,f)=>events[n]=f},
    setInterval:f=>poll=f,setTimeout:()=>{},fetch:async()=>{
      if(pending)await new Promise(r=>resolve=r);
      if(failure==='network')throw Error('offline');
      return {ok:!failure,json:async()=>{if(failure==='json')throw Error('invalid');return result;}};
    }};
  vm.runInNewContext(fs.readFileSync(__dirname+'/workflow.js','utf8'),context);
  events['unfertig:todos-rendered']();assert.equal(icon.classList.running,false);
  await poll();assert.equal(icon.classList.running,true);assert.equal(icon.label,'started — Implementation running');
  icon=makeIcon();events['unfertig:todos-rendered']();assert.equal(icon.classList.running,true);
  for(const phase of ['ready','implementation_failed','interrupted','testing','tested','merging','restarting','done']){
    result.runs.T0001={phase};await poll();assert.equal(icon.classList.running,false,phase);
  }
  for(const extra of [{foreign:true},{resume_action:'retry'}]){
    result.runs.T0001={phase:'implementing',...extra};await poll();assert.equal(icon.classList.running,false);
  }
  result.runs.T0001={phase:'implementing'};
  for(const field of ['busy','enabled']){
    result[field]=false;await poll();assert.equal(icon.classList.running,false);result[field]=true;
  }
  for(const error of [true,'network','json']){
    await poll();assert.equal(icon.classList.running,true);
    failure=error;await poll();assert.equal(icon.classList.running,false);assert.equal(icon.label,'started');failure=false;
  }
  await poll();pending=true;const request=poll();now+=6001;
  await poll();assert.equal(icon.classList.running,false);resolve();await request;
  assert.equal(icon.classList.running,false,'late response does not renew old evidence');
  pending=false;await poll();assert.equal(icon.classList.running,true);
  result.runs={};await poll();assert.equal(icon.classList.running,false,'started alone is idle');
});
test('disabled or missing feature flag hides all workflow controls and blocks stale clicks',async()=>{
  const events={};let poll,result={enabled:false,runs:{},configured:true};const calls=[];
  const panel={hidden:true,innerHTML:'',dataset:{workflow:'T0001'},querySelector:()=>null};
  const context={document:{querySelectorAll:s=>s==='[data-workflow]'?[panel]:[],addEventListener:(name,fn)=>events[name]=fn},
    AbortSignal,token:'token',data:{todos:[{id:'T0001',status:'open'}]},compatibility:{read_only:false},history:{pending:false},
    hasDraft:()=>false,escapeHTML:s=>s,setInterval:fn=>poll=fn,setTimeout:()=>{},
    fetch:async(url)=>{calls.push(url);return {ok:true,json:async()=>result};}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/workflow.js','utf8'),context);
  await poll();assert.equal(panel.hidden,true);assert.equal(panel.innerHTML,'');
  result={enabled:true,runs:{},configured:true,automatic:false};await poll();
  assert.equal(panel.hidden,false);assert.match(panel.innerHTML,/Implement with Codex/);assert.match(panel.innerHTML,/Test branch/);assert.match(panel.innerHTML,/Merge & restart/);
  result={enabled:false,runs:{}};await poll();assert.equal(panel.hidden,true);assert.equal(panel.innerHTML,'');
  const before=calls.length;
  await events.click({target:{closest:()=>({dataset:{todo:'T0001',workflowAction:'implement'}})},preventDefault(){},stopPropagation(){}});
  assert.equal(calls.length,before);
  result={runs:{}};await poll();assert.equal(panel.hidden,true);
});

test('collapsed row follows workflow stages and respects execution guards',async()=>{
  const events={}; let poll, draft=false;
  let result={enabled:true,runs:{},configured:true,busy:false};
  const slot={hidden:true,innerHTML:'',dataset:{workflowNext:'T0001'}};
  const context={document:{querySelectorAll:s=>s==='[data-workflow-next]'?[slot]:[],addEventListener:(name,fn)=>events[name]=fn},
    AbortSignal,token:'token',data:{todos:[{id:'T0001',status:'open'}]},compatibility:{read_only:false},history:{pending:false},
    hasDraft:()=>draft,escapeHTML:s=>s,setInterval:fn=>poll=fn,setTimeout:()=>{},
    fetch:async()=>({ok:true,json:async()=>result})};
  vm.runInNewContext(fs.readFileSync(__dirname+'/workflow.js','utf8'),context);
  await poll(); assert.equal(slot.hidden,false); assert.match(slot.innerHTML,/data-workflow-action="implement"/);
  for(const [phase,action,label] of [['ready','test','Preview'],['tested','merge','Merge & restart'],['implementation_failed','retry','Retry implementation'],['test_failed','test','Preview'],['push_failed','merge','Merge & restart']]) {
    result.runs.T0001={phase}; await poll();
    assert.equal((slot.innerHTML.match(/<button/g)||[]).length,1);
    assert.match(slot.innerHTML,new RegExp(`data-workflow-action="${action}"`)); assert.ok(slot.innerHTML.includes(label)); assert.doesNotMatch(slot.innerHTML,/ disabled/);
  }
  draft=true; events.input(); assert.match(slot.innerHTML,/ disabled/);
  draft=false; events.change(); assert.doesNotMatch(slot.innerHTML,/ disabled/);
  for(const [object,key,value] of [[result,'configured',false],[result,'configured',false],[context.compatibility,'read_only',true],[context.history,'pending',true]]) {
    const previous=object[key];object[key]=value;await poll();assert.match(slot.innerHTML,/ disabled/);object[key]=previous;
  }
  for(const [phase,label] of [['implementing','Implementing…'],['testing','Preparing preview…'],['merging','Merging…'],['restarting','Restarting…']]) {
    result.runs.T0001={phase};await poll();assert.ok(slot.innerHTML.includes(label));assert.match(slot.innerHTML,/ disabled/);
  }
  for(const action of ['retry','test','merge']) {
    result.runs.T0001={phase:'interrupted',resume_action:action};await poll();
    assert.equal((slot.innerHTML.match(/<button/g)||[]).length,1);
    assert.match(slot.innerHTML,new RegExp(`data-workflow-action="${action}"`));
  }
  result.runs.T0001={phase:'ready',foreign:true}; await poll(); assert.match(slot.innerHTML,/ disabled/);
  result.runs.T0001={phase:'done'}; await poll(); assert.equal(slot.hidden,true);
  result.runs={}; result.enabled=false; await poll(); assert.equal(slot.hidden,true); assert.equal(slot.innerHTML,'');
});

test('collapsed next action and expanded optional preview retain confirmed direct merge',async()=>{
  const events={};let poll,confirmation='',approved=false,submitted,failure='',alerted='';
  const commit='a'.repeat(40),todo={id:'T0001',status:'started',name:'Task',description:'Scope'};
  const slot={dataset:{workflowNext:todo.id}},panel={dataset:{workflow:todo.id},querySelector:()=>null};
  let result={enabled:true,configured:true,runs:{T0001:{phase:'ready',commit,branch:'codex/task'}}};
  const context={document:{querySelectorAll:s=>s==='[data-workflow-next]'?[slot]:s==='[data-workflow]'?[panel]:[],addEventListener:(n,f)=>events[n]=f},
    AbortSignal,token:'token',data:{todos:[todo]},compatibility:{read_only:false},history:{pending:false},hasDraft:()=>false,
    escapeHTML:s=>s,setInterval:f=>poll=f,setTimeout:()=>{},load:async()=>{},alert:message=>alerted=message,
    confirm:s=>{confirmation=s;return approved;},fetch:async(url,options)=>{
      if(url==='/api/state')return {ok:true,json:async()=>({data:{todos:[todo]},token:'fresh',revisions:{todos:{T0001:'revision'}}})};
      if(options?.body) { submitted=JSON.parse(options.body);if(failure)return {ok:false,json:async()=>({error:failure})}; }
      return {ok:true,json:async()=>result};
    }};
  vm.runInNewContext(fs.readFileSync(__dirname+'/workflow.js','utf8'),context);
  for(const phase of ['ready','test_failed','tested','merge_failed','push_failed','restart_failed']){
    result.runs.T0001.phase=phase;await poll();
    assert.match(panel.innerHTML,/data-workflow-action="merge"[^>]* >Merge & restart/);
    assert.equal((slot.innerHTML.match(/<button/g)||[]).length,1);
    assert.match(slot.innerHTML,new RegExp(`data-workflow-action="${['ready','test_failed'].includes(phase)?'test':'merge'}"`));
    for(const surface of [slot,panel]) {
      assert.doesNotMatch(surface.innerHTML,/has not passed|<p class="muted"><\/p>|<span class="muted">/);
    }
  }
  result.runs.T0001.phase='ready';await poll();
  assert.match(slot.innerHTML,/data-workflow-action="test"/);
  assert.doesNotMatch(slot.innerHTML,/data-workflow-action="merge"/);
  const click=()=>events.click({target:{closest:()=>({dataset:{todo:todo.id,workflowAction:'merge'}})},preventDefault(){},stopPropagation(){}});
  await click();assert.equal(submitted,undefined);assert.ok(confirmation.includes(commit));assert.doesNotMatch(confirmation,/has not passed|\n\n$/);
  approved=true;await click();assert.equal(submitted.commit,commit);assert.equal(submitted.revision,'revision');
  assert.equal(result.runs.T0001.tested_commit,undefined,'direct merge does not invent test evidence');
  failure='Target checkout is dirty. Merge did not run.';await click();assert.equal(alerted,failure);
  result.runs.T0001.message=failure;await poll();assert.ok(panel.innerHTML.includes(failure));
  result.runs.T0001.phase='tested';result.runs.T0001.tested_commit=commit;await poll();
  assert.match(slot.innerHTML,/data-workflow-action="merge"/);
  assert.match(panel.innerHTML,/This commit passed Test branch/);
  assert.doesNotMatch(slot.innerHTML,/This commit/);
  assert.equal(submitted.tested_commit,undefined);
  result.runs.T0001.tested_commit='b'.repeat(40);await poll();assert.doesNotMatch(panel.innerHTML,/This commit|<p class="muted"><\/p>/);
});

test('parallel activity leaves another ticket actionable and pipeline links each PR',async()=>{
  let poll;const events={};
  const slot={dataset:{workflowNext:'T0002'}},row={};
  const result={enabled:true,configured:true,busy:true,active_count:1,max_workers:2,runs:{T0001:{phase:'implementing',active:true,pr_url:'https://github.com/test/code/pull/1'}}};
  const context={document:{querySelectorAll:s=>s==='[data-workflow-next]'?[slot]:s==='[data-integration-pipeline]'?[row]:[],addEventListener:(n,f)=>events[n]=f},
    AbortSignal,token:'token',data:{todos:[{id:'T0001',status:'started'},{id:'T0002',status:'open'}]},compatibility:{read_only:false},history:{pending:false},hasDraft:()=>false,
    escapeHTML:s=>s,setInterval:f=>poll=f,setTimeout:()=>{},fetch:async()=>({ok:true,json:async()=>result})};
  vm.runInNewContext(fs.readFileSync(__dirname+'/workflow.js','utf8'),context);
  await poll();assert.doesNotMatch(slot.innerHTML,/ disabled/);
  assert.match(row.innerHTML,/1\/2 workers/);assert.match(row.innerHTML,/github.com\/test\/code\/pull\/1/);
  result.runs.T0002={phase:'merge_queued',queued_at:'2026-09-08T00:00:00Z'};result.draining=true;
  await poll();assert.match(slot.innerHTML,/Queued for integration/);assert.match(row.innerHTML,/Draining/);
  result.enabled=false;await poll();assert.equal(row.hidden,true);
});
