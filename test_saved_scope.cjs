const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
test('editor Save uses owner route and replays the exact request after a lost response',async()=>{
  const source=fs.readFileSync(__dirname+'/app.js','utf8');
  const old={id:'T0001',category:'',name:'Task',description:'Text'};
  let fail=true;const requests=[];
  const context={compatibility:{read_only:false},busy:false,actor:()=> 'SL',saveState:()=>{},
    $$:()=>[],$:()=>({hidden:true}),data:{ideas:[],todos:[old]},draftRevisions:new Map(),
    revisions:{ideas:{},todos:{T0001:'revision'}},pendingRequest:null,crypto:{randomUUID:()=> 'stable-request'},
    token:'token',toast:()=>{},historyState:()=>{},refreshPublication:()=>{},
    fetch:async(url,options)=>{requests.push({url,body:options.body});if(fail)throw Error('Lost response');
      return {ok:true,json:async()=>({data:{ideas:[],todos:[{...old,category:'concept'}]},history:{pending:false},revisions:{},assigned:[]})};}
  };
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('async function save(next)'),source.indexOf('function options(')),context);
  const next={ideas:[],todos:[{...old,category:'concept'}]};
  assert.equal(await context.save(next),false);fail=false;
  assert.equal(await context.save(next),true);
  assert.equal(requests[0].url,'/api/editor/changes');
  assert.deepEqual(requests[0],requests[1]);
  assert.equal(JSON.parse(requests[0].body).changes[0].revision,'revision');
});
test('changed result offers ordinary Retry and active work stays disabled',()=>{
  const source=fs.readFileSync(__dirname+'/workflow.js','utf8');
  const context={};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function nextStep('),source.indexOf('  function implementationLabel(')),context);
  const todo={status:'started'};
  assert.deepEqual(Array.from(context.nextStep(todo,{phase:'implementation_failed',resume_action:'retry'})),['retry','Retry implementation',true]);
  assert.equal(context.nextStep(todo,{phase:'implementing',active:true})[2],false);
  assert.equal(context.nextStep(todo,{phase:'activity_unknown',activity_block:'Worker is still running'})[2],false);
});
