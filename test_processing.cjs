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
