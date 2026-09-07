const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
test('disabled or missing feature flag hides all workflow controls and blocks stale clicks',async()=>{
  const events={};let poll,result={enabled:false,runs:{},configured:true};const calls=[];
  const panel={hidden:true,innerHTML:'',dataset:{workflow:'T0001'},querySelector:()=>null};
  const context={document:{querySelectorAll:()=>[panel],addEventListener:(name,fn)=>events[name]=fn},
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
