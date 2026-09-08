const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const app = fs.readFileSync(__dirname+'/app.js','utf8');
const definitions = JSON.parse(fs.readFileSync(__dirname+'/categories.json','utf8'));
function setup() {
  const context = {agentAdvice:JSON.parse(fs.readFileSync(__dirname+'/agent_advice.json','utf8')),categoryDefinitions:definitions, escapeHTML:s=>String(s).replaceAll('<','&lt;'),
    boardContext:{process:'/p/PROCESS.md',data:'/p/data.json',todos:'/p/todos',repository:'/p'},
    data:{ideas:[]}, date:s=>s};
  context.effortDefinitions = JSON.parse(fs.readFileSync(__dirname+'/efforts.json','utf8'));
  vm.createContext(context);
  vm.runInContext(app.slice(app.indexOf('function effortValue('),app.indexOf("document.addEventListener('change'")),context);
  vm.runInContext(app.slice(app.indexOf('function boardLocations('),app.indexOf('let revisions')),context);
  vm.runInContext(app.slice(app.indexOf('function implementationBrief('),app.indexOf("$('#idea-form')")),context);
  return context;
}
test('all categories have identical intent in both briefings and editor help',()=>{
  const c=setup();
  assert.deepEqual(Object.keys(definitions.categories), ['ideation','research','concept','design','implementation','debugging','refactoring','bugfix']);
  for (const [category,d] of Object.entries(definitions.categories)) {
    const todo={id:'T0001',name:'Task',description:'Approval required',category,tags:[],source_ideas:[]};
    for (const render of [c.implementationBrief,c.humanBrief]) {
      const text=render(todo);
      for (const value of Object.values(d)) assert.ok(text.includes(value));
      assert.ok(text.includes(definitions.boundary));
      assert.ok(text.includes('Approval required'));
    }
    assert.ok(c.categoryEditor(todo).includes(`value="${category}" selected`));
    assert.ok(c.categoryEditor(todo).includes(d.completion));
  }
  assert.match(c.categoryEditor(),/Unclassified/);
  assert.match(c.categoryBrief({category:'future-type'}),/future-type/);
});
test('four manual profiles, Automatic, unsupported values and both saved briefings',()=>{
  const c=setup(), efforts=JSON.parse(fs.readFileSync(__dirname+'/efforts.json','utf8'));
  const todo={id:'T0001',name:'Task',description:'Bounded change',tags:[],source_ideas:[]};
  assert.match(c.effortEditor(todo), /value="auto" selected/);
  assert.equal(Object.keys(efforts.profiles).length, 4);
  for(const [execution_profile,p] of Object.entries(efforts.profiles)){
    todo.execution_profile=execution_profile;
    assert.ok(c.effortEditor(todo).includes(`value="${execution_profile}" selected`));
    for(const render of [c.implementationBrief,c.humanBrief]) assert.ok(render(todo).includes(`Agent effort: ${p.label}`));
  }
  for(const execution_profile of ['',null,'future','__proto__']){
    todo.execution_profile=execution_profile;
    assert.match(c.effortEditor(todo),/Unsupported:/);
    for(const render of [c.implementationBrief,c.humanBrief]) assert.throws(()=>render(todo),/Unsupported execution profile/);
  }
  for(const effort of ['',null,'future']) assert.throws(()=>c.effortBrief({effort}),/Unsupported effort/);
});
test('automatic browser routing matches the shared acceptance cases',()=>{
  const c=setup();
  for(const [todo,role,expected] of JSON.parse(fs.readFileSync(__dirname+'/test_profile_cases.json','utf8')))
    assert.equal(c.executionProfile(todo,role).profile,expected,JSON.stringify(todo));
});

test('completion summaries and exact preflight locations appear in both handoffs',()=>{
  const c=setup();
  const todo={id:'T0018',name:'Reporting',description:'Keep requirements',completion_summary:'Verified outcome <safe>',tags:[],source_ideas:[]};
  for (const render of [c.implementationBrief,c.humanBrief]) {
    const text=render(todo);
    for (const value of ['/p/PROCESS.md','/p/todos/T0018.json','/p/data.json','Keep requirements','COMPLETION SUMMARY','Verified outcome <safe>','stop dependent work','Never push','no empty commit','Never delete a pre-existing branch']) assert.ok(text.includes(value),value);
  }
  assert.match(app,/name="completion_summary"/);
  assert.match(app,/escapeHTML\(todo.completion_summary/);
  assert.match(fs.readFileSync(__dirname+'/aggregation.js','utf8'),/escapeHTML\(t.completion_summary/);
});

test('saved completion text is rendered safely in the task editor',()=>{
  const c=setup(); Object.assign(c,{expanded:new Set(),todoSummary:()=>'',field:()=>'',options:()=>''});
  vm.runInContext(app.slice(app.indexOf('function todoCard('),app.indexOf('function renderTodos(')),c);
  const html=c.todoCard({id:'T0018',status:'closed',tags:[],source_ideas:[],completion_summary:'Verified <script> outcome',description:'Requirements remain separate'});
  assert.match(html,/name="completion_summary"[^>]*>Verified &lt;script> outcome<\/textarea>/);
  assert.match(html,/name="description"[^>]*>Requirements remain separate<\/textarea>/);
});
