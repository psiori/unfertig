const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
test('disabled or missing feature flag hides all workflow controls and blocks stale clicks',async()=>{
  const events={};let poll,result={enabled:false,runs:{},configured:true};const calls=[];
  const panel={hidden:true,innerHTML:'',dataset:{workflow:'T0001'},querySelector:()=>null};
  const context={document:{querySelectorAll:s=>s==='[data-workflow]'?[panel]:[],addEventListener:(name,fn)=>events[name]=fn},
    token:'token',data:{todos:[{id:'T0001',status:'open'}]},compatibility:{read_only:false},history:{pending:false},
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
    token:'token',data:{todos:[{id:'T0001',status:'open'}]},compatibility:{read_only:false},history:{pending:false},
    hasDraft:()=>draft,escapeHTML:s=>s,setInterval:fn=>poll=fn,setTimeout:()=>{},
    fetch:async()=>({ok:true,json:async()=>result})};
  vm.runInNewContext(fs.readFileSync(__dirname+'/workflow.js','utf8'),context);
  await poll(); assert.equal(slot.hidden,false); assert.match(slot.innerHTML,/data-workflow-action="implement"/);
  for(const [phase,action,label] of [['ready','test','Preview'],['tested','merge','Merge & restart'],['implementation_failed','retry','Retry implementation'],['test_failed','test','Preview'],['push_failed','merge','Merge & restart']]) {
    result.runs.T0001={phase}; await poll();
    assert.match(slot.innerHTML,new RegExp(`data-workflow-action="${action}"`)); assert.ok(slot.innerHTML.includes(label)); assert.doesNotMatch(slot.innerHTML,/ disabled/);
  }
  draft=true; events.input(); assert.match(slot.innerHTML,/ disabled/);
  draft=false; events.change(); assert.doesNotMatch(slot.innerHTML,/ disabled/);
  result.runs.T0001={phase:'implementing'}; await poll(); assert.match(slot.innerHTML,/Implementing…/); assert.match(slot.innerHTML,/ disabled/);
  result.runs.T0001={phase:'ready',foreign:true}; await poll(); assert.match(slot.innerHTML,/ disabled/);
  result.runs.T0001={phase:'done'}; await poll(); assert.equal(slot.hidden,true);
  result.runs={}; result.enabled=false; await poll(); assert.equal(slot.hidden,true); assert.equal(slot.innerHTML,'');
});
