const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/worker-capacity.js','utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
  const events={}, timers=[]; let now=1000, failure=false, saveError=false, submitted;
  let status={capacity_state:'ready',active_count:0,max_workers:4,worker_settings:{editable:true,revision:'original'}};
  const input={value:'',addEventListener:(n,f)=>events[n]=f};
  const dot={dataset:{},setAttribute(k,v){this[k]=v;}},label={},button={},message={};
  const form={querySelector:s=>({'input':input,'.live-dot':dot,'[data-capacity-label]':label,'button':button}[s]),reportValidity:()=>true,addEventListener:(n,f)=>events[n]=f};
  const context={AbortSignal,Date:{now:()=>now},document:{querySelector:s=>s==='#worker-capacity'?form:message},setInterval:f=>timers.push(f),fetch:async(url,options)=>{
    if(url==='/api/state')return {ok:true,json:async()=>({token:'token'})};
    if(url==='/api/workflow/settings'){
      submitted=JSON.parse(options.body);
      if(saveError)return {ok:false,json:async()=>({error:'Configuration changed'})};
      status={...status,max_workers:submitted.max_workers,worker_settings:{editable:true,revision:'saved'}};
    }else if(failure)throw Error('offline');
    return {ok:true,json:async()=>status};
  }};
  vm.runInNewContext(source,context);
  return {events,timers,input,dot,label,button,message,form,setStatus:s=>status={...status,...s},fail:v=>failure=v,saveError:v=>saveError=v,advance:v=>now+=v,submitted:()=>submitted};
}
test('all capacity colors, stale/offline state, dirty drafts and explicit keyboard save',async()=>{
  const h=harness();await flush();
  assert.equal(h.input.value,4);
  for(const state of ['ready','occupied','full','inactive','unknown','unavailable']){
    h.setStatus({capacity_state:state});await h.timers[1]();
    assert.equal(h.dot.dataset.state,state);assert.match(h.label.textContent,/WORKERS/);
  }
  h.setStatus({capacity_state:'ready'});await h.timers[1]();h.advance(6001);h.timers[0]();
  assert.equal(h.dot.dataset.state,'unknown');assert.equal(h.input.disabled,true);
  await h.timers[1]();h.input.value='2';h.events.input();
  h.setStatus({max_workers:6,worker_settings:{editable:true,revision:'concurrent'}});await h.timers[1]();
  assert.equal(h.input.value,'2','polling preserves draft');
  h.saveError(true);await h.events.submit({preventDefault(){}});
  assert.deepEqual(h.submitted(),{max_workers:2,revision:'original'});
  assert.match(h.message.textContent,/draft is retained/);assert.equal(h.input.value,'2');
  h.events.keydown({key:'Escape'});assert.equal(h.input.value,6);assert.equal(h.button.hidden,true);
  h.input.value='3';h.events.input();h.saveError(false);await h.events.submit({preventDefault(){}});
  assert.deepEqual(h.submitted(),{max_workers:3,revision:'concurrent'});
  assert.match(h.message.textContent,/Saved/);assert.equal(h.input.value,3);
  h.fail(true);await h.timers[1]();assert.equal(h.dot.dataset.state,'unknown');
});
test('header order and native keyboard/narrow width controls',()=>{
  const html=fs.readFileSync(__dirname+'/index.html','utf8');
  assert.ok(html.indexOf('LOCAL & YOURS')<html.indexOf('id="worker-capacity"'));
  assert.ok(html.indexOf('id="worker-capacity"')<html.indexOf('class="identity"'));
  assert.match(html,/type="number" min="1" max="8" step="1" required/);
  assert.match(html,/aria-label="Maximum concurrent workers"/);
  assert.match(html,/id="worker-capacity-message"[^>]*role="status"/);
  const css=fs.readFileSync(__dirname+'/style.css','utf8');
  assert.match(css,/@media\(max-width:600px\).*\.masthead\{flex-wrap:wrap\}/s);
});
