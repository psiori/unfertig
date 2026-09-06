// Run with node --test test_title.cjs. No browser dependencies required.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
test('project title uses text and resets cleanly to standalone', () => {
  const source = fs.readFileSync(__dirname + '/app.js', 'utf8');
  const titleFunction = source.slice(source.indexOf('function updateTitle('), source.indexOf('function boardLocations('));
  const label = {textContent: ''};
  Object.defineProperty(label, 'innerHTML', {set() {throw Error('unsafe HTML write');}});
  const brand = {setAttribute(key, value) {this[key] = value;}};
  const context = {document: {title: ''}, $: selector => selector === '#project-name' ? label : brand};
  vm.createContext(context);vm.runInContext(titleFunction, context);
  for (const name of ['Example', '<img src=x onerror=alert(1)> & ü', '']) {
    context.updateTitle({project_name: name});
    assert.equal(label.textContent, name ? ` · ${name}` : '');
    assert.equal(context.document.title, name ? `unfertig · ${name} · Ideas & todos` : 'unfertig · Ideas & todos');
    assert.equal(brand['aria-label'], name ? `unfertig · ${name} home` : 'unfertig home');
  }
  context.updateTitle(undefined);
  assert.equal(label.textContent, '');
});
