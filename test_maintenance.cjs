const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.textContent='';}
  set innerHTML(value){this.parts={summary:new Element('summary'),p:new Element('p'),ul:new Element('ul')};}
  querySelector(name){return this.parts[name];}
  prepend(node){this.children.unshift(node);}
  append(node){this.children.push(node);}
  replaceChildren(){this.children=[];}
  addEventListener(name,handler){this.handlers[name]=handler;}
}
test('maintenance separates hook failure from task publication and sends an explicit retry',async()=>{
  const main=new Element('main');let poll,submitted,offline=false;
  const state={phase:'idle',pending:false,blockers:[],error:'',runtime_commit:'a'.repeat(40),update:{},
    hooks:[{todo:'T0001',id:'event',hook:'update',status:'failed',message:'Command failed'}]};
  const fetch=async(url,options)=>{
    if(offline)throw new Error('Disconnected');
    if(url==='/api/state')return {ok:true,json:async()=>({token:'fresh-token'})};
    if(options?.body){submitted={body:JSON.parse(options.body),token:options.headers['X-Board-Token']};state.hooks[0].status='pending';}
    return {ok:true,json:async()=>JSON.parse(JSON.stringify(state))};
  };
  vm.runInNewContext(fs.readFileSync(__dirname+'/maintenance.js','utf8'),{document:{body:main,querySelector:()=>main,createElement:tag=>new Element(tag)},fetch,AbortSignal,setInterval:f=>poll=f});
  await new Promise(setImmediate);
  const panel=main.children[0];assert.match(panel.parts.summary.textContent,/needs attention/);
  const button=panel.parts.ul.children[0].children[0];assert.equal(button.textContent,'Retry hook');
  await button.handlers.click();await new Promise(setImmediate);
  assert.deepEqual(submitted,{body:{action:'retry_hook',todo:'T0001',event:'event'},token:'fresh-token'});
  state.pending=true;state.phase='draining';state.blockers=['Task T0002 is running'];await poll();
  assert.match(panel.parts.p.textContent,/queued work is retained/);assert.match(panel.parts.p.textContent,/T0002/);
  assert.equal(panel.parts.ul.children[0].children.length,0);
  offline=true;await poll();assert.match(panel.parts.summary.textContent,/reconnecting/);
});

function fixture() {
  const main = new Element('main'); let poll, offline = false, calls = 0;
  let state = {phase:'idle', pending:false, blockers:[], error:'', runtime_commit:'a'.repeat(40), update:{}, hooks:[],
    currency:{state:'current', target_commit:'a'.repeat(40), checked_at:'2026-09-09T00:00:00Z', message:'Current'}};
  const fetch = async () => {calls++; if (offline) throw Error('Offline'); return {ok:true,json:async()=>structuredClone(state)};};
  vm.runInNewContext(fs.readFileSync(__dirname+'/maintenance.js','utf8'), {document:{body:main,querySelector:()=>main,createElement:tag=>new Element(tag)},fetch,AbortSignal,setInterval:f=>poll=f});
  return {panel:main.children[0],poll:()=>poll(),set:value=>state=value,get:()=>state,offline:value=>offline=value,calls:()=>calls};
}
test('healthy visibility transitions preserve polling and ignore successful history', async()=>{
  const f=fixture(); assert.equal(f.panel.hidden,true); await new Promise(setImmediate);
  assert.equal(f.panel.hidden,true); const healthy=structuredClone(f.get());
  for(const phase of ['installed','current','complete','idle']) {
    f.set({...healthy,update:{phase,message:'Historical success'},hooks:[{status:'complete'}]});await f.poll();assert.equal(f.panel.hidden,true);
  }
  for(const change of [
    {currency:{state:'outdated'}}, {currency:{state:'unknown'}}, {currency:null},
    {currency:{state:'current'}}, {currency:{...healthy.currency,target_commit:'b'.repeat(40)}},
    {pending:true,phase:'draining'}, {phase:'ready'}, {phase:'failed'}, {blockers:['Pending writer']},
    {error:'Failed status'}, {update:{phase:'failed'}}, {update:{phase:'updating'}},
    {update:{phase:'installed',blockers:['Unresolved']}}, {hooks:[{status:'pending'}]}, {hooks:[{status:'failed'}]},
    {hooks:null}
  ]) {
    f.set({...healthy,...change});await f.poll();assert.equal(f.panel.hidden,false,JSON.stringify(change));
    f.set(healthy);await f.poll();assert.equal(f.panel.hidden,true);
  }
  const before=f.calls(); await f.poll(); assert.equal(f.calls(),before+1);assert.equal(f.panel.hidden,true);
  f.offline(true);await f.poll();assert.equal(f.panel.hidden,false);assert.match(f.panel.parts.summary.textContent,/reconnecting/);
  f.offline(false);await f.poll();assert.equal(f.panel.hidden,true,'identical healthy response after outage must restore hidden state');
});

test('update uses the owner API, prevents repeated clicks, and keeps acceptance distinct from installation', async()=>{
  const main=new Element('main'); let poll, writes=0, submitted, finish, failure=false;
  const state={phase:'idle',pending:false,supported:true,blockers:[],error:'',runtime_commit:'a'.repeat(40),hooks:[],update:{},
    currency:{state:'outdated',target_commit:'b'.repeat(40),message:'Update available'},update_action:{available:true,request:{}}};
  const fetch=async(url,options)=>{
    if(url==='/api/state')return {ok:true,json:async()=>({token:'owner'})};
    if(options?.method==='PUT') {
      writes++;submitted={body:JSON.parse(options.body),token:options.headers['X-Board-Token']};
      await new Promise(resolve=>finish=resolve);
      if(failure)throw Error('Connection lost');
      state.update_action.request={id:'retained',state:'pending'};
    }
    return {ok:true,json:async()=>structuredClone(state)};
  };
  vm.runInNewContext(fs.readFileSync(__dirname+'/maintenance.js','utf8'),{document:{body:main,querySelector:()=>main,createElement:tag=>new Element(tag)},fetch,AbortSignal,setInterval:f=>poll=f});
  await new Promise(setImmediate);
  const panel=main.children[0],button=panel.parts.ul.children[0].children[0];
  assert.equal(button.textContent,'Update & restart');
  const action=button.handlers.click(); await new Promise(setImmediate);
  await button.handlers.click(); assert.equal(writes,1); assert.equal(button.disabled,true);
  assert.deepEqual(submitted,{body:{action:'update_restart',target_commit:'b'.repeat(40)},token:'owner'});
  finish();await action;await poll();
  assert.match(panel.parts.summary.textContent,/update requested/);
  assert.match(panel.parts.p.textContent,/not yet confirmed/);
  assert.equal(panel.parts.ul.children[0].children.length,0);
  state.pending=true;state.phase='draining';state.blockers=['Task T2 is running'];await poll();
  assert.match(panel.parts.p.textContent,/T2/);assert.match(panel.parts.p.textContent,/queued work is retained/);
  state.pending=false;state.phase='idle';state.blockers=[];
  state.update_action.request={id:'retained',state:'failed',message:'Git conflict'};
  state.update={phase:'failed',message:'Startup failed'};await poll();
  assert.match(panel.parts.p.textContent,/Git conflict/);assert.match(panel.parts.p.textContent,/Startup failed/);
  assert.match(panel.parts.ul.children[0].textContent,/retained/);
  state.update={phase:'installed'};state.update_action.request={state:'complete'};
  state.runtime_commit='b'.repeat(40);state.currency={state:'current',target_commit:'b'.repeat(40),checked_at:'2026-09-10T00:00:00Z'};
  await poll();assert.equal(panel.hidden,true);
  state.currency={state:'outdated',target_commit:'c'.repeat(40)};state.update_action.request={};
  await poll();const retry=panel.parts.ul.children[0].children[0];
  failure=true;const uncertain=retry.handlers.click();await new Promise(setImmediate);finish();await uncertain;
  assert.equal(retry.disabled,false);assert.match(panel.parts.p.textContent,/unconfirmed.*same request/);
  state.supported=false;await poll();assert.equal(panel.parts.ul.children[0].children.length,0);
  assert.match(panel.parts.ul.children[0].textContent,/supported update action/);
});
