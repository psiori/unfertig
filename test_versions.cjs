const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/app.js','utf8');
test('compatibility notice disables editors for future minor and clears on update', () => {
  const notice = {}, editor = {};
  const context = {busy:false, $:()=>notice, $$:()=>[editor]};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('let compatibility ='),source.indexOf('function updateTitle')),context);
  vm.runInContext('compatibility={read_only:true,warnings:["Newer minor"]}; compatibilityState();',context);
  assert.equal(editor.disabled,true); assert.equal(notice.hidden,false);
  assert.match(notice.textContent,/Read-only/);
  vm.runInContext('compatibility={read_only:false,warnings:[]}; compatibilityState();',context);
  assert.equal(editor.disabled,false); assert.equal(notice.hidden,true);
  context.busy=true; context.compatibilityState(); assert.equal(editor.disabled,true);
});
