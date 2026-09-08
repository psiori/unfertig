const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.textContent='';}
  set innerHTML(value){this.parts={summary:new Element('summary'),p:new Element('p'),ul:new Element('ul')};}
  querySelector(name){return this.parts[name];}
  prepend(node){this.children.unshift(node);}
  append(node){this.children.push(node);}
  replaceChildren(){this.children=[];}
  addEventListener(name,handler){this.handlers[name]=handler;}
}
test('maintenance separates hook failure from task publication and sends an explicit retry',async()=>{
  const main=new Element('main');let poll,submitted,offline=false;
  const state={phase:'idle',pending:false,blockers:[],error:'',runtime_commit:'a'.repeat(40),update:{},
    hooks:[{todo:'T0001',id:'event',hook:'update',status:'failed',message:'Command failed'}]};
  const fetch=async(url,options)=>{
    if(offline)throw new Error('Disconnected');
    if(url==='/api/state')return {ok:true,json:async()=>({token:'fresh-token'})};
    if(options?.body){submitted={body:JSON.parse(options.body),token:options.headers['X-Board-Token']};state.hooks[0].status='pending';}
    return {ok:true,json:async()=>JSON.parse(JSON.stringify(state))};
  };
  vm.runInNewContext(fs.readFileSync(__dirname+'/maintenance.js','utf8'),{document:{body:main,querySelector:()=>main,createElement:tag=>new Element(tag)},fetch,AbortSignal,setInterval:f=>poll=f});
  await new Promise(setImmediate);
  const panel=main.children[0];assert.match(panel.parts.summary.textContent,/hook failed/);
  const button=panel.parts.ul.children[0].children[0];assert.equal(button.textContent,'Retry hook');
  await button.handlers.click();await new Promise(setImmediate);
  assert.deepEqual(submitted,{body:{action:'retry_hook',todo:'T0001',event:'event'},token:'fresh-token'});
  state.pending=true;state.phase='draining';state.blockers=['Task T0002 is running'];await poll();
  assert.match(panel.parts.p.textContent,/queued work is retained/);assert.match(panel.parts.p.textContent,/T0002/);
  assert.equal(panel.parts.ul.children[0].children.length,0);
  offline=true;await poll();assert.match(panel.parts.summary.textContent,/reconnecting/);
});
