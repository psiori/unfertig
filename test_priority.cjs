const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/priority.js','utf8');
function setup(){
  let id=0;
  const context={structuredClone,crypto:{randomUUID:()=>`request-${++id}`}};
  vm.createContext(context);vm.runInContext(source.slice(0,source.indexOf('let priorityDrafts')),context);
  return new (vm.runInContext('PriorityDrafts',context))();
}
const snapshot={todo:{id:'T0001',priority:'normal',description:'Preserve'},revision:'old',preflight:{project_id:'a'}};
test('priority noop has no request; unknown outcome retries identical request after restore',async()=>{
  const drafts=setup();assert.equal(drafts.begin('a',snapshot,'normal','Editor'),null);
  const entry=drafts.begin('a',snapshot,'high','Editor');const body=JSON.stringify(entry.original);
  await drafts.send('a',async()=>{throw Error('Disconnected');});
  assert.equal(entry.status,'uncertain');assert.equal(JSON.stringify(entry.original),body);
  const restored=setup();restored.entries=new Map(JSON.parse(JSON.stringify([...drafts.entries])));
  await restored.send('a',async retry=>{assert.equal(retry.request_id,entry.request_id);assert.equal(retry.revision,'old');return {todo:{priority:'high'},history:{pending:false}};});
  assert.equal(restored.entries.size,0);
});
test('conflicts and Git failures retain desired value without claiming success',async()=>{
  const drafts=setup();const entry=drafts.begin('a',snapshot,'urgent','Editor');
  await drafts.send('a',async()=>{const error=Error('Changed');error.status=409;throw error;});
  assert.equal(entry.status,'conflict');assert.equal(entry.priority,'urgent');assert.equal(entry.original.priority,'normal');
  await drafts.send('a',async()=>({todo:{priority:'urgent'},history:{pending:true}}));
  assert.equal(entry.status,'history');assert.equal(drafts.entries.size,1);
});
test('recovered receipt does not claim a subsequently changed priority is the requested value',async()=>{
  const drafts=setup();const entry=drafts.begin('a',snapshot,'urgent','Editor');
  await drafts.send('a',async()=>({todo:{priority:'low'},history:{pending:false}}));
  assert.equal(entry.status,'conflict');assert.equal(drafts.entries.size,1);
});
test('briefings use explicit child paths and both local and foreign originals',()=>{
  const app=fs.readFileSync(__dirname+'/app.js','utf8');
  const context={boardContext:{process:'/inbox/PROCESS.md',data:'/inbox/data.json',todos:'/inbox/todos',repository:'/inbox'},data:{ideas:[]},date:v=>v};
  vm.createContext(context);
  context.categoryDefinitions = JSON.parse(fs.readFileSync(__dirname+'/categories.json','utf8'));
  vm.runInContext(app.slice(app.indexOf('function categoryBrief('),app.indexOf('function categoryEditor(')),context);
  vm.runInContext(app.slice(app.indexOf('function boardLocations('),app.indexOf('let revisions')),context);
  vm.runInContext(app.slice(app.indexOf('function implementationBrief('),app.indexOf("$('#idea-form')")),context);
  const child={process:'/child/PROCESS.md',data:'/child/data.json',todos:'/child/todos',repository:'/child'};
  const todo={id:'T0001',name:'Task',priority:'high',status:'open',tags:[],source_ideas:['I0001'],source_refs:[{project_id:'foreign',idea:{id:'I0001',text:'Foreign original',author:'Foreign'}}]};
  for(const fn of [context.implementationBrief,context.humanBrief]){
    const text=fn(todo,{ideas:[{id:'I0001',text:'Child original',author:'Child'}]},child);
    assert.match(text,/\/child\/PROCESS.md/);assert.match(text,/Child original/);assert.match(text,/foreign:I0001/);assert.match(text,/Foreign original/);assert.doesNotMatch(text,/\/inbox\//);
  }
});
