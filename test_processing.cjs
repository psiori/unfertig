const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
test('input is batched, drafts are flagged, and pagehide sends a keepalive close', async () => {
  const events={}, windows={}, elements={}; const calls=[]; let pulse, draft=false;
  const context={crypto:{randomUUID:()=> 'tab-one'}, document:{hidden:false,
    querySelector:s=>elements[s] ||= {}, addEventListener:(name,fn)=>events[name]=fn},
    window:{addEventListener:(name,fn)=>windows[name]=fn}, token:'token', compatibility:{read_only:false},history:{pending:false},
    data:{ideas:[{id:'I0001'}],todos:[]},queueMicrotask:fn=>fn(),hasDraft:()=>draft,load:()=>{},setTimeout:()=>{},setInterval:fn=>pulse=fn,
    fetch:async(url,request)=>{calls.push({url,...request});return {ok:true,json:async()=>({enabled:true,available:true,status:'idle',automatic:true,working_directory:'/project',directory_source:'configured',idle_seconds:600,system_identified:true})};}};
  elements['#process-run']={addEventListener:()=>{}};
  elements['.capture-mark']={classList:{toggle:()=>{}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/processing.js','utf8'),context);
  for(let n=0;n<100;n++) events.input();
  assert.equal(calls.length,0);
  draft=true;await pulse();
  assert.equal(calls.length,1);assert.equal(JSON.parse(calls[0].body).active,true);assert.equal(JSON.parse(calls[0].body).draft,true);
  await pulse();assert.equal(JSON.parse(calls[1].body).active,false);
  events.keydown();await pulse();assert.equal(JSON.parse(calls[2].body).active,true);
  await windows.pagehide();assert.equal(calls[3].keepalive,true);assert.equal(JSON.parse(calls[3].body).closed,true);
  assert.equal(elements['#process-run'].title,'Working directory: /project');
  assert.equal(elements['#process-run'].disabled,true);
  draft=false; events.input();
  assert.equal(elements['#process-run'].disabled,false);
  context.data.ideas=[]; events['unfertig:ideas-rendered']();
  assert.equal(elements['#process-run'].disabled,true);
  assert.match(elements['#processing-state'].textContent,/All caught up/);
  assert.equal(calls.length,4);
});

test('scratchpad activity follows confirmed manual and automatic runs across refreshes and stops', async () => {
  const events={}, elements={}; let pulse, click, spinning=false, draft=false, loads=0;
  let result={enabled:true,available:true,status:'idle',run_id:'run',working_directory:'/temporary-board',message:'Run output'};
  let respond;
  const context={crypto:{randomUUID:()=> 'activity-tab'},document:{hidden:false,
    querySelector:s=>elements[s] ||= {},addEventListener:(name,fn)=>events[name]=fn},
    window:{addEventListener:()=>{}},token:'token',compatibility:{read_only:false},history:{pending:false},
    data:{ideas:[{id:'I0001'}],todos:[]},queueMicrotask:fn=>fn(),hasDraft:()=>draft,load:()=>loads++,setTimeout:()=>{},setInterval:fn=>pulse=fn,
    fetch:async url=>url.endsWith('/start') ? new Promise(resolve=>respond=resolve) : {ok:true,json:async()=>result}};
  elements['#process-run']={addEventListener:(name,fn)=>click=fn};
  elements['.capture-mark']={classList:{toggle:(name,value)=>{assert.equal(name,'is-processing');spinning=value;}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/processing.js','utf8'),context);
  await pulse(); assert.equal(spinning,false);
  const request=click();
  assert.equal(elements['#process-run'].disabled,true); assert.equal(spinning,false,'launch request alone is not running');
  result={...result,status:'running'};
  respond({ok:true,json:async()=>result}); await request;
  assert.equal(spinning,true); assert.equal(elements['#process-run'].disabled,true);
  assert.match(elements['#processing-state'].textContent,/turning saved ideas/);
  events['unfertig:ideas-rendered'](); assert.equal(spinning,true);
  for (const status of ['completed','failed','needs_attention','interrupted','idle']) {
    result={...result,status:'running'}; await pulse(); // automatic run detected by presence
    assert.equal(spinning,true);
    result={...result,status,message:`Result: ${status}`}; await pulse();
    assert.equal(spinning,false,status);
    assert.match(elements['#processing-details'].textContent,new RegExp(`Result: ${status}`));
    events['unfertig:ideas-rendered'](); assert.equal(spinning,false);
  }
  assert.equal(loads,5);
  for (const guard of ['draft','read_only','history','empty','disabled','unavailable']) {
    draft=guard==='draft'; context.compatibility.read_only=guard==='read_only';
    context.history.pending=guard==='history'; context.data.ideas=guard==='empty'?[]:[{id:'I0001'}];
    result={...result,enabled:guard!=='disabled',available:guard!=='unavailable'};
    await pulse(); assert.equal(elements['#process-run'].disabled,true,guard); assert.equal(spinning,false,guard);
  }
});
