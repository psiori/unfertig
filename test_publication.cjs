const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/app.js', 'utf8');
function harness() {
  const nodes = new Map();
  const badge = {dataset:{publicationKind:'todos', publicationId:'T0001'}};
  const context = {
    revision:'board-1', token:'token', busy:false, pendingRequest:null, compatibility:{read_only:false},
    $: id => {if (!nodes.has(id)) nodes.set(id, {classList:{toggle() {}}}); return nodes.get(id);},
    $$: () => [badge], escapeHTML: s => s,
    hasDraft: () => false, toast: message => context.message = message,
    window: {confirm: () => true}, fetch: async () => {throw Error('Unexpected network access');},
    load: async () => {},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('let publication ='), source.indexOf('const expanded =')), context);
  vm.runInContext(`publication = {available:true, board_revision:'board-1', ahead:1, can_push:true, records:{todos:{T0001:'local'}}, message:'Ready', confirmation:'reviewed', branch:'main', remote:'origin', target:'refs/heads/main'};`, context);
  return {context, nodes, badge};
}
test('badges reflect saved revision and stale status disables Push', () => {
  const {context, nodes, badge} = harness();
  context.publicationState();
  assert.equal(badge.textContent, 'Local only');
  assert.equal(nodes.get('#publication-push').disabled, false);
  assert.equal(nodes.get('#publication-push').hidden, false);
  vm.runInContext('publication.ahead = 0;', context); context.publicationState();
  assert.equal(nodes.get('#publication-push').hidden, true);
  assert.equal(nodes.get('#publication-refresh').disabled, false);
  vm.runInContext('publication.ahead = 1;', context);
  context.revision = 'new-board'; context.publicationState();
  assert.equal(badge.textContent, 'Remote unknown');
  assert.equal(nodes.get('#publication-push').disabled, true);
  assert.equal(nodes.get('#publication-push').hidden, true);
});
test('drafts and cancelled confirmation prevent a push request', async () => {
  const {context} = harness();
  context.hasDraft = () => true;
  await context.publicationAction(true);
  assert.match(context.message, /drafts/);
  context.hasDraft = () => false; context.window.confirm = () => false;
  await context.publicationAction(true);
  assert.equal(context.busy, false);
});
test('a confirmed global push sends reviewed digest and displays uncertain outcome', async () => {
  const {context, nodes} = harness();
  const requests = [];
  context.fetch = async (url, options) => {
    requests.push({url, options});
    if (options) throw Error('Connection lost');
    return {ok:true, json:async () => ({board_revision:'board-1', records:{}, ahead:1, can_push:false, message:'Check remote'})};
  };
  await context.publicationAction(true);
  assert.equal(requests[0].url, '/api/publication/push');
  assert.deepEqual(JSON.parse(requests[0].options.body), {confirmation:'reviewed'});
  assert.match(nodes.get('#publication-state').textContent, /remote may already have accepted/);
  assert.equal(context.busy, false);
});
