const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const app = fs.readFileSync(__dirname + '/app.js', 'utf8');
function setup(saved = {}) {
  const elements = new Map(), messages = [], requests = [];
  const $ = key => {
    if (!elements.has(key)) elements.set(key, {value:'', addEventListener(type, fn){this[type] = fn;}, focus(){this.focused = true;}});
    return elements.get(key);
  };
  const context = {$, $$:()=>[], preference:key=>saved[key] || '', remember:(key,value)=>saved[key]=value,
    toast:message=>messages.push(message), compatibility:{read_only:false}, busy:false,
    saveState(){}, refreshPublication(){}, historyState(){}, structuredClone, crypto:require('node:crypto'),
    data:{ideas:[],todos:[]}, revisions:{ideas:{},todos:{}}, draftRevisions:new Map(), pendingRequest:null,
    token:'token', notice(){}, showCopy(){},
    fetch:async(url, options)=>{requests.push(JSON.parse(options.body)); throw Error('Lost response');}};
  vm.createContext(context);
  vm.runInContext(app.slice(app.indexOf('// Do not infer initials'), app.indexOf("$('#shortcut')")), context);
  vm.runInContext(app.slice(app.indexOf('function actor()'), app.indexOf('function nextId(')), context);
  vm.runInContext(app.slice(app.indexOf('async function save('), app.indexOf('function options(')), context);
  return {context, $, saved, messages, requests};
}
test('saved initials win; old author is retained without inferring a mapping', () => {
  const first = setup({author:'Sascha Lange'});
  assert.equal(first.context.actor(), null);
  assert.equal(first.$('#initials').focused, true);
  assert.equal(first.saved.author, 'Sascha Lange');
  first.$('#initials').value = ' sl2 ';
  assert.equal(first.context.actor(), 'SL2');
  const reload = setup(first.saved);
  assert.equal(reload.$('#initials').value, 'SL2');
  assert.equal(reload.context.actor(), 'SL2');
  assert.equal(reload.saved.author, 'Sascha Lange');
});
test('empty and invalid identity block a save without changing the draft or sending a request', async () => {
  const {context, $, requests} = setup();
  const draft = {ideas:[{id:'I0001',author:'Original',text:'Keep this draft'}],todos:[]};
  for (const value of ['', ' ', '1SL', 'S-L', 'S L', 'Ä', 'ABCDEFGHIJKLM']) {
    $('#initials').value = value;
    assert.equal(await context.save(draft), false);
    assert.equal(context.busy, false);
  }
  assert.equal(requests.length, 0);
  assert.equal(draft.ideas[0].text, 'Keep this draft');
});
test('new saves use initials for actor and prefix; unknown outcomes retain exact identity and request', async () => {
  const {context, $, requests} = setup({initials:'sl',author:'Different full name'});
  const draft = {ideas:[{id:'I0001',author:'Original requester',text:'Unchanged'}],todos:[]};
  await context.save(draft);
  assert.equal(requests[0].actor, 'SL');
  assert.equal(requests[0].initials, 'SL');
  assert.equal(requests[0].changes[0].record.author, 'Original requester');
  $('#initials').value = 'AB';
  await context.save(draft);
  assert.equal(requests.length, 1);
  $('#initials').value = 'sl';
  await context.save(draft);
  assert.deepEqual(requests[1], requests[0]);
});
