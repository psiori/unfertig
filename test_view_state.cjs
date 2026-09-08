const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const app=fs.readFileSync(__dirname+'/app.js','utf8');
function setup(storage=new Map()) {
 const choices={'status-filter':['active','all','open','started','closed'],sort:['priority','newest','oldest','name'],'group-filter':['','Module'],'tag-filter':['','ui'],'project-filter':['','a']};
 const elements=new Map(Object.entries({'search':'','status-filter':'active','group-filter':'','tag-filter':'','project-filter':'',sort:'priority','group-by':false,'show-processed':false}).map(([id,value])=>[id,{value,checked:false,type:typeof value==='boolean'?'checkbox':'select-one',options:(choices[id]||[]).map(value=>({value}))}]));
 const c={boardContext:{project_id:'a',data:'/a/data.json'},viewValues:new Map(),document:{dispatchEvent(){}},Event:class{},$:id=>elements.get(id.slice(1)),sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)}};
 vm.createContext(c);vm.runInContext(app.slice(app.indexOf('const viewDefaults ='),app.indexOf('function actor()')),c);
 const run=s=>vm.runInContext(s,c);
 const restore=()=>{c.activateViewState();for(const id of ['group-filter','tag-filter','project-filter'])c.restoreViewChoice(id);};
 return {c,elements,storage,run,restore};
}
test('closed, all, search and false values survive tab reload with board isolation',()=>{
 const a=setup();a.restore();a.elements.get('status-filter').value='closed';a.elements.get('search').value=' ü & text ';a.elements.get('group-filter').value='Module';a.c.rememberViewState();
 const b=setup(a.storage);b.restore();assert.equal(b.elements.get('status-filter').value,'closed');assert.equal(b.elements.get('search').value,' ü & text ');assert.equal(b.elements.get('group-filter').value,'Module');assert.equal(b.elements.get('group-by').checked,false);
 b.c.boardContext={project_id:'b',data:'/b/data.json'};b.restore();assert.equal(b.elements.get('status-filter').value,'active');assert.equal(b.elements.get('search').value,'');
 b.c.boardContext={project_id:'a',data:'/a/data.json'};b.restore();assert.equal(b.elements.get('status-filter').value,'closed');
 b.elements.get('status-filter').value='all';b.c.rememberViewState();const d=setup(b.storage);d.restore();assert.equal(d.elements.get('status-filter').value,'all');
 const fresh=setup();fresh.restore();assert.equal(fresh.elements.get('status-filter').value,'active');
});
test('invalid cache and removed choices default while explicit compatible fields remain',()=>{
 for(const raw of ['{','null','[]','{"version":2,"values":{}}','{"version":1,"values":[]}']) {
  const a=setup();a.restore();a.storage.set(a.run('viewKey'),raw);const b=setup(a.storage);b.restore();assert.equal(b.elements.get('status-filter').value,'active');
 }
 const a=setup();a.restore();a.storage.set(a.run('viewKey'),JSON.stringify({version:1,values:{'status-filter':'closed',sort:'obsolete','group-filter':'removed','group-by':'true',search:17}}));
 const b=setup(a.storage);b.restore();assert.equal(b.elements.get('status-filter').value,'closed');assert.equal(b.elements.get('sort').value,'priority');assert.equal(b.elements.get('group-filter').value,'');assert.equal(b.elements.get('group-by').checked,false);assert.equal(b.elements.get('search').value,'');
});
test('asynchronous choices remain pending through intermediate saves; storage may be unavailable',()=>{
 const a=setup();a.restore();a.elements.get('group-filter').value='Module';a.c.rememberViewState();const b=setup(a.storage);b.c.activateViewState();b.c.restoreViewChoice('group-filter',false);b.c.rememberViewState();assert.equal(JSON.parse(b.storage.get(b.run('viewKey'))).values['group-filter'],'Module');b.c.restoreViewChoice('group-filter');assert.equal(b.elements.get('group-filter').value,'Module');
 b.c.sessionStorage={getItem(){throw Error('disabled');},setItem(){throw Error('full');}};b.c.boardContext={project_id:'x',data:'/x'};assert.doesNotThrow(()=>{b.restore();b.c.rememberViewState();});assert.equal(b.elements.get('status-filter').value,'active');
});
