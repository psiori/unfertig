const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const app = fs.readFileSync(__dirname+'/app.js','utf8');
const definitions = JSON.parse(fs.readFileSync(__dirname+'/categories.json','utf8'));
function setup() {
  const context = {agentAdvice:JSON.parse(fs.readFileSync(__dirname+'/agent_advice.json','utf8')),categoryDefinitions:definitions, escapeHTML:s=>String(s).replaceAll('<','&lt;'),
    boardContext:{process:'/p/PROCESS.md',data:'/p/data.json',todos:'/p/todos',repository:'/p'},
    data:{ideas:[]}, date:s=>s};
  vm.createContext(context);
  vm.runInContext(app.slice(app.indexOf('function categoryBrief('),app.indexOf("document.addEventListener('change'")),context);
  vm.runInContext(app.slice(app.indexOf('function boardLocations('),app.indexOf('let revisions')),context);
  vm.runInContext(app.slice(app.indexOf('function implementationBrief('),app.indexOf("$('#idea-form')")),context);
  return context;
}
test('all categories have identical intent in both briefings and editor help',()=>{
  const c=setup();
  assert.deepEqual(Object.keys(definitions.categories), ['ideation','research','concept','design','implementation','debugging','refactoring']);
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
