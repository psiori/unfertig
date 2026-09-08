const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(__dirname+'/instance-settings.js','utf8');

class Element {
  constructor() { this.events = {}; this.children = []; this.value = ''; this.hidden = true; }
  get value() { return this._value; }
  set value(value) { this._value = String(value); }
  addEventListener(name, callback) { this.events[name] = callback; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  reportValidity() { return true; }
  showModal() { this.open = true; }
  close() { this.open = false; this.events.close?.(); }
  focus() { this.focused = true; }
  async emit(name) { await this.events[name]?.({preventDefault(){}}); }
}
function harness(storage = new Map()) {
  const nodes = {}, calls = [];
  let failed = false, saveError = '', token = 'first';
  let snapshot = {editable:true,revision:'one',layer:'local',
    fields:{project_name:{label:'Board title',type:'text',help:'Title',maxLength:120},max_workers:{label:'Workers',type:'number',min:1,max:8,help:'Next dispatch'},idle_seconds:{label:'Idle',type:'number',min:60,max:86400,help:'Idle'}},
    values:{project_name:'Original',max_workers:4,idle_seconds:600},saved_values:{project_name:'Original',max_workers:4,idle_seconds:600}};
  const node = id => nodes[id] ||= new Element();
  const context = {AbortSignal,location:{origin:'http://localhost'},document:{getElementById:node,createElement:()=>new Element()},
    sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    fetch:async (url, options={})=>{
      calls.push({url,...options});
      if(failed)throw Error('offline');
      if(url==='/api/state')return {ok:true,json:async()=>({token,context:{project_id:'test'}})};
      if(options.method==='PUT'){
        const body=JSON.parse(options.body);
        if(saveError || body.revision!==snapshot.revision)return {ok:false,json:async()=>({error:saveError || 'Configuration changed'})};
        snapshot={...snapshot,revision:'saved',values:{...snapshot.values,...body.changes},saved_values:{...snapshot.saved_values,...body.changes}};
      }
      return {ok:true,json:async()=>structuredClone(snapshot)};
    }};
  vm.runInNewContext(source,context);
  return {node,calls,storage,open:()=>node('open-settings').emit('click'),
    input:key=>node('settings-fields').children.flatMap(label=>label.children).find(input=>input.name===key),
    async edit(key,value){const input=this.input(key);input.value=value;await input.emit('input');},
    snapshot:value=>snapshot={...snapshot,...value},fail:value=>failed=value,error:value=>saveError=value,token:value=>token=value};
}
test('allowlisted changed fields save; local layer, immediate values and accessible help',async()=>{
  const h=harness();await h.open();
  assert.match(h.node('settings-layer').textContent,/durable local preferences/);
  assert.equal(h.node('settings-save').disabled,true);
  assert.equal(h.input('max_workers').min,1);
  assert.equal(h.input('max_workers')['aria-describedby'],'setting-max_workers-help');
  await h.edit('project_name','<img onerror=unsafe>');
  await h.edit('max_workers','2');
  await h.node('settings-form').emit('submit');
  const body=JSON.parse(h.calls.find(call=>call.method==='PUT').body);
  assert.deepEqual(body,{revision:'one',changes:{project_name:'<img onerror=unsafe>',max_workers:2}});
  assert.match(h.node('settings-message').textContent,/Saved/);
  assert.equal(h.storage.size,0);
  assert.equal(h.node('settings-save').disabled,true);
});
test('concurrent edits retain draft, compare, merge only intended fields and explicitly save',async()=>{
  const h=harness();await h.open();await h.edit('max_workers','3');
  h.snapshot({revision:'two',saved_values:{project_name:'Other title',max_workers:6,idle_seconds:600}});
  await h.node('settings-form').emit('submit');
  assert.match(h.node('settings-message').textContent,/draft is retained/);
  assert.equal(h.input('max_workers').value,'3');
  await h.node('settings-refresh').emit('click');
  assert.equal(h.node('settings-comparison').hidden,false);
  assert.equal(h.node('settings-save').disabled,true);
  await h.node('settings-keep').emit('click');
  assert.equal(h.input('project_name').value,'Other title');
  assert.equal(h.input('max_workers').value,'3');
  await h.node('settings-form').emit('submit');
  assert.deepEqual(JSON.parse(h.calls.filter(call=>call.method==='PUT').at(-1).body),{revision:'two',changes:{max_workers:3}});
});
test('offline and validation errors, close/reopen and tab reload preserve drafts; renew token',async()=>{
  const h=harness();await h.open();await h.edit('idle_seconds','120');
  h.fail(true);await h.node('settings-form').emit('submit');
  assert.equal(h.input('idle_seconds').value,'120');
  await h.node('close-settings').emit('click');
  assert.equal(h.node('open-settings').focused,true);
  h.fail(false);h.token('second');await h.open();
  assert.equal(h.input('idle_seconds').value,'120');
  await h.node('settings-dialog').emit('cancel');
  assert.equal(h.node('settings-dialog').open,false);
  assert.equal(h.node('open-settings').focused,true);
  await h.open();
  await h.node('settings-keep').emit('click');
  h.error('Validation failed');await h.node('settings-form').emit('submit');
  assert.equal(h.calls.filter(call=>call.method==='PUT').at(-1).headers['X-Board-Token'],'second');
  assert.match(h.node('settings-message').textContent,/Validation failed/);
  const reloaded=harness(h.storage);await reloaded.open();
  assert.equal(reloaded.input('idle_seconds').value,'120');
  assert.equal(reloaded.node('settings-comparison').hidden,false);
  await reloaded.node('settings-use').emit('click');
  assert.equal(reloaded.input('idle_seconds').value,'600');
  assert.equal(reloaded.storage.size,0);
});
test('unsupported source stays readable and cannot submit; keyboard dialog and narrow layout',async()=>{
  const h=harness();h.snapshot({editable:false,error:'Read-only'});await h.open();
  assert.equal(h.input('max_workers').disabled,true);
  assert.equal(h.node('settings-save').disabled,true);
  assert.equal(h.node('settings-message').textContent,'Read-only');
  const html=fs.readFileSync(__dirname+'/index.html','utf8');
  assert.match(html,/id="open-settings"[^>]*aria-label="Instance settings"/);
  assert.match(html,/<dialog id="settings-dialog" aria-labelledby="settings-title">/);
  assert.match(html,/id="settings-message" role="status" aria-live="polite"/);
  assert.match(fs.readFileSync(__dirname+'/style.css','utf8'),/@media\(max-width:560px\)\{#settings-fields\{grid-template-columns:1fr\}/);
});
