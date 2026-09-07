const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function setup() {
  const elements = new Map();
  const $ = id => { if (!elements.has(id)) elements.set(id,{value:'',innerHTML:'',checked:false,addEventListener(){},style:{}}); return elements.get(id); };
  $('#status-filter').value='all'; $('#sort').value='priority';
  const context = {$, renderIdeas(){},renderTodos(){},updateChoices(){},preference(){return '';},remember(){},setInterval(){},setTimeout(){},
    expanded:new Set(), options:(values,selected)=>values.map(v=>`<option ${v===selected?'selected':''}>${v}</option>`).join(''),
    boardContext:{mode:'aggregation',sources:[{project_id:'a'},{project_id:'b'}]},
    data:{ideas:[],todos:[]},compatibility:{read_only:false},unique:values=>[...new Set(values)],date:v=>v,
    escapeHTML:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),boardLocations:()=> 'Read PROCESS.md'};
  vm.createContext(context);
  const app=fs.readFileSync(__dirname+'/app.js','utf8');
  context.categoryDefinitions = JSON.parse(fs.readFileSync(__dirname+'/categories.json','utf8'));
  vm.runInContext(app.slice(app.indexOf('function categoryBrief('),app.indexOf('function categoryEditor(')),context);
  vm.runInContext(app.slice(app.indexOf('function todoSummary('),app.indexOf('function todoCard(')),context);
  vm.runInContext(fs.readFileSync(__dirname+'/priority.js','utf8'),context);
  vm.runInContext(fs.readFileSync(__dirname+'/aggregation.js','utf8'),context);
  vm.runInContext(`aggregateSources=['a','b'].map(project_id=>({project_id,name:project_id,url:'http://127.0.0.1:8766',status:'reachable',data:{ideas:[],todos:[{id:'T0001',name:project_id+' task',description:'<script>literal</script>',tags:['same'],group:'same',status:'open',priority:'normal',date_entered:'2026-01-01',author:'Human',created_by:'Agent'}]}}));`,context);
  return {context,$};
}
test('flat filters qualify groups/tags and duplicate IDs by source',()=>{
  const {context:c,$}=setup();c.updateChoices();
  assert.match($('#group-filter').innerHTML,/a \/ same/);assert.match($('#group-filter').innerHTML,/b \/ same/);
  $('#group-filter').value=JSON.stringify(['a','same']);c.renderTodos();
  assert.match($('#todos').innerHTML,/a task/);assert.doesNotMatch($('#todos').innerHTML,/b task/);
  $('#group-filter').value='';$('#tag-filter').value=JSON.stringify(['b','same']);c.renderTodos();
  assert.match($('#todos').innerHTML,/b task/);assert.doesNotMatch($('#todos').innerHTML,/a task/);
  assert.match($('#todos').innerHTML,/&lt;script>/);assert.doesNotMatch($('#todos').innerHTML,/<form/);
});
test('nested view keeps projects above equal groups and source open actions',()=>{
  const {context:c,$}=setup();$('#group-by').checked=true;c.renderTodos();
  assert.match($('#todos').innerHTML,/<summary>a<\/summary>.*<summary>same<\/summary>/);
  assert.match($('#todos').innerHTML,/<summary>b<\/summary>.*<summary>same<\/summary>/);
  assert.equal(($('#todos').innerHTML.match(/#todo-T0001/g)||[]).length,2);
  assert.equal(c.ownerLink({status:'stale'},'todo','T0001'),'<span class="route-warning">Instance unavailable</span>');
});
test('routing briefing carries actual selection, retry and preflight rules',()=>{
  const {context:c}=setup(); const text=c.aggregationBrief([{id:'SL_I0001',text:'Original',selected_project:'b'}]);
  for(const expected of ['selected_project','PROCESS.md','process_sha256','/api/routes','source_refs','stored request','No routing step pushes']) assert.ok(text.includes(expected),expected);
});
test('filesystem records retain details without a service link and briefings show transport',()=>{
  const {context:c,$}=setup();
  vm.runInContext("aggregateSources[0].transport='filesystem'; aggregateSources[0].url='';",c);
  c.renderTodos();
  assert.match($('#todos').innerHTML,/Filesystem record/);
  assert.equal(($('#todos').innerHTML.match(/#todo-T0001/g)||[]).length,1);
  const text=c.aggregationBrief([]);
  for (const expected of ['filesystem','TRANSPORTS.md','does not migrate','same request/receipt']) assert.ok(text.includes(expected),expected);
});
