const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/app.js', 'utf8');

// Run the actual submit, save and draft-restoration paths with a controlled server.
function setup() {
  const record = id => ({id, name:id, description:'Saved text', completion_summary:'',
    priority:'normal', execution_profile:'auto', group:'', category:'implementation',
    status:'open', pr_url:'', commit_url:'', commit_hash:'', depends_on:[], tags:[]});
  const makeForm = todo => ({dataset:{id:todo.id}, elements:Object.assign([], ...Object.entries(todo)
    .map(([name, value]) => ({[name]:{name, value:Array.isArray(value) ? value.join(', ') : value}}))),
    querySelector:() => ({textContent:''})});
  let forms = new Map(['T0001','T0002','T0003'].map(id => [id, makeForm(record(id))]));
  // HTMLFormControlsCollection is both iterable and accessible by field name.
  for (const form of forms.values()) form.elements.push(...Object.values(form.elements));
  const form = forms.get('T0001');
  form.dataset.dirty = 'true'; form.elements.description.value = 'My edited text';
  forms.get('T0002').dataset.dirty = 'true';
  forms.get('T0002').elements.description.value = 'Other unsaved draft';
  let submit, resolveRequest, rejectRequest, renderCount = 0;
  const request = new Promise((resolve, reject) => { resolveRequest = resolve; rejectRequest = reject; });
  const noticeElement = {hidden:false};
  const messages = [], copies = [], requests = [];
  const c = {compatibility:{read_only:false}, busy:false, actor:() => 'SL',
    data:{ideas:[], todos:['T0001','T0002','T0003'].map(record)},
    expanded:new Set(['T0001','T0002']), draftRevisions:new Map([['T0001','r1'],['T0002','r2']]),
    revisions:{ideas:{}, todos:{T0001:'r1', T0002:'r2', T0003:'r3'}},
    priorityDrafts:{entries:new Map()}, pendingRequest:null, token:'token',
    crypto:{randomUUID:() => 'request-id'}, structuredClone, now:() => '2026-09-10T10:00:00Z',
    tags:value => value.split(',').map(item => item.trim()).filter(Boolean),
    FormData:class { constructor(form) { return form.elements.map(el => [el.name, el.value]); } },
    $:selector => selector === '#todos' ? {addEventListener:(_, handler) => { submit = handler; }}
      : selector === '#notice' ? noticeElement
      : forms.get(selector.match(/data-id="([^"]+)"/)?.[1]),
    $$:selector => selector.includes('data-dirty') ? [...forms.values()].filter(form => form.dataset.dirty === 'true')
      : [...forms.values()].flatMap(form => [...form.elements]),
    saveState:(...args) => messages.push(args), toast:message => messages.push([message]),
    notice:message => { noticeElement.hidden = false; messages.push([message]); },
    showCopy:async (...args) => copies.push(args), historyState:() => {}, refreshPublication:() => {},
    categoryBrief:() => '',
    render:() => {
      renderCount++;
      forms = new Map(c.data.todos.map(todo => {
        const form = makeForm(todo); form.elements.push(...Object.values(form.elements));
        return [todo.id, form];
      }));
    },
    fetch:(url, options) => { requests.push({url, body:options.body}); return request; }
  };
  vm.createContext(c);
  vm.runInContext(source.slice(source.indexOf('async function save(next)'), source.indexOf('function options(')), c);
  vm.runInContext(source.slice(source.indexOf("$('#todos').addEventListener('submit'"), source.indexOf('function safeViewChange()')), c);
  return {c, form, messages, copies, requests, forms:() => forms, renders:() => renderCount,
    submit:() => submit({target:form, preventDefault() {}}), reject:rejectRequest,
    respond:(status=200) => resolveRequest({ok:status === 200, status, json:async () => {
      const next = structuredClone(c.data);
      next.todos[0].description = 'My edited text';
      return {data:next, revision:'new', revisions:{ideas:{}, todos:{T0001:'new',T0002:'r2',T0003:'r3'}},
        context:{}, history:{pending:false}, assigned:[], error:'Save rejected ' + status};
    }})};
}

test('successful editor save collapses only its row after acknowledgement and preserves other drafts', async () => {
  const h = setup(), pending = h.submit();
  assert.equal(h.c.busy, true);
  assert.equal(h.c.expanded.has('T0001'), true);
  assert.equal(h.form.elements.description.disabled, true);
  assert.equal(h.form.elements.description.value, 'My edited text');
  assert.equal(h.renders(), 0);
  h.respond(); await pending;
  assert.deepEqual([...h.c.expanded], ['T0002']);
  assert.equal(h.renders(), 1);
  assert.equal(h.c.data.todos[0].description, 'My edited text');
  assert.equal(h.c.draftRevisions.has('T0001'), false);
  assert.equal(h.c.draftRevisions.get('T0002'), 'r2');
  assert.equal(h.forms().get('T0002').elements.description.value, 'Other unsaved draft');
  assert.equal(h.forms().get('T0002').dataset.dirty, 'true');
  assert.equal(h.requests[0].url, '/api/editor/changes');
  assert.equal(JSON.parse(h.requests[0].body).changes.length, 1);
});

for (const failure of [400, 403, 409, 500, 'lost response']) {
  test(`${failure} leaves the editor expanded with draft and recovery feedback`, async () => {
    const h = setup(), pending = h.submit();
    if (typeof failure === 'number') h.respond(failure); else h.reject(Error(failure));
    await pending;
    assert.deepEqual([...h.c.expanded], ['T0001','T0002']);
    assert.equal(h.renders(), 0);
    assert.equal(h.form.dataset.dirty, 'true');
    assert.equal(h.form.elements.description.value, 'My edited text');
    assert.equal(h.form.elements.description.disabled, false);
    assert.equal(h.c.data.todos[0].description, 'Saved text');
    assert.equal(h.c.draftRevisions.get('T0001'), 'r1');
    assert.ok(h.messages.some(([message, error]) => message === 'Not saved' && error));
    assert.ok(h.messages.some(([message]) => message.includes(String(failure))));
    assert.equal(h.c.pendingRequest !== null, failure === 500 || failure === 'lost response');
    assert.equal(h.copies.length, failure === 409 ? 1 : 0);
  });
}

test('local validation leaves the editor and draft available without a request', async () => {
  const h = setup(); h.form.elements.status.value = 'closed';
  await h.submit();
  assert.equal(h.requests.length, 0);
  assert.equal(h.renders(), 0);
  assert.equal(h.c.expanded.has('T0001'), true);
  assert.equal(h.form.dataset.dirty, 'true');
  assert.equal(h.form.elements.description.value, 'My edited text');
  assert.ok(h.messages.some(([message]) => message.includes('Add a completion summary')));
});
