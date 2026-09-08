const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(__dirname + '/app.js', 'utf8');
function setup() {
  const c = {boardContext:{process:'/temporary/app/PROCESS.md', data:'/temporary/board/data.json',
    todos:'/temporary/board/todos', repository:'/temporary'}, data:{ideas:[], todos:[]}};
  c.effortDefinitions = JSON.parse(fs.readFileSync(__dirname + '/efforts.json', 'utf8'));
  vm.createContext(c);
  vm.runInContext(app.slice(app.indexOf('function effortValue('), app.indexOf("document.addEventListener('change'")), c);
  vm.runInContext(app.slice(app.indexOf('function boardLocations('), app.indexOf('let revisions')), c);
  vm.runInContext(app.slice(app.indexOf('function processBrief('), app.indexOf('function implementationBrief(')), c);
  return c;
}
test('copied scope stays live across new ideas, processed ideas, and an empty board', () => {
  const c = setup();
  const empty = c.processBrief();
  c.data.ideas.push({id:'I9991',text:'Private original body',author:'Requester'});
  const original = JSON.stringify(c.data);
  const copied = c.processBrief();
  assert.equal(copied, empty);
  assert.ok(copied.includes(c.effortProcessingGuidance()));
  assert.equal(JSON.stringify(c.data), original);
  c.data.ideas.push({id:'I9992',text:'Added after copying'});
  c.data.todos.push({source_ideas:['I9991']});
  assert.equal(c.processBrief(), copied);
  c.data.todos.push({source_ideas:['I9992']});
  assert.equal(c.processBrief(), copied);
  for (const value of Object.values(c.boardContext)) assert.ok(copied.includes(value));
  for (const pattern of [/all pending ideas/, /added since/, /no todo.*source_ideas/, /Nothing to process/,
    /planning only/, /preserve original ideas and attribution/, /overlap/, /created_by/,
    /current revisions/, /identical request body and ID/, /source-already-processed conflict/, /never use allow_shared_sources/]) {
    assert.match(copied, pattern);
  }
  assert.doesNotMatch(copied, /I999[12]|Private original body|Added after copying/);
});
test('only the explicitly all-pending control copies a processing briefing', () => {
  const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
  const aggregation = fs.readFileSync(__dirname + '/aggregation.js', 'utf8');
  assert.match(html, /id="process"[^>]*>Copy all-pending briefing/);
  assert.match(app, /showCopy\(processBrief\(\), 'All pending ideas/);
  assert.doesNotMatch(app + aggregation, /data-process=|dataset\.process/);
  assert.match(app, /data-make=.*Create manually/);
});
test('aggregation dispatch receives no cached idea selection', () => {
  const c = setup(); c.boardContext.mode = 'aggregation';
  c.aggregationBrief = (...args) => {assert.equal(args.length, 0); return 'live inbox';};
  assert.equal(c.processBrief(), 'live inbox');
});
