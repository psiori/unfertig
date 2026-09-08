const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

function harness(storage=new Map()) {
  let click,poll,approved=true,fail=false,readOnly=false,drafts=new Set(),puts=[],confirms=[];
  const button={addEventListener:(_,fn)=>click=fn},feedback={};
  const action=id=>({id,action:'merge',revision:'saved-'+id,commit:'a'.repeat(40),request_id:'request-'+id});
  const entries=['T0001','T0002'].map(id=>({id,name:'Saved '+id,action:action(id),repositories:[{id:'context',repository:'/um',branch:'codex/'+id,commit:'a'.repeat(40),changed:true,pr_url:'https://github.com/test/repo/pull/'+id}]}));
  let review={repository:'/um/code',board:'/um/board/data.json',entries,excluded:[{id:'T0003',reason:'Changed PR head'}],queue_blocked_by:'T0004'};
  let outcomes=[{id:'T0001',status:'accepted',message:'Queued'},{id:'T0002',status:'rejected',message:'Scope changed after review'}];
  const context={token:'token',busy:false,compatibility:{read_only:false},history:{pending:false},
    // Only one displayed todo: review must use the server's unfiltered set.
    data:{todos:[{id:'T0001'}]},
    document:{querySelector:s=>s==='#workflow-merge-all'?button:s==='#workflow-merge-all-state'?feedback:[...drafts].some(id=>s.includes('"'+id+'"'))?{}:null,addEventListener(){}},
    sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    setInterval:fn=>poll=fn,setTimeout(){},load:async()=>{},confirm:message=>{confirms.push(message);return approved;},
    fetch:async(url,options)=>{
      if(url==='/api/workflow')return {ok:true,json:async()=>({enabled:!readOnly,repository:'/um/code'})};
      if(url==='/api/workflow/merge-review')return {ok:true,json:async()=>review};
      if(url==='/api/state')return {ok:true,json:async()=>({token:'fresh-session'})};
      assert.equal(url,'/api/workflow/merge-batch'); puts.push(options);
      if(fail)throw Error('Service restarted before response');
      return {ok:true,json:async()=>({outcomes})};
    }};
  vm.runInNewContext(fs.readFileSync(__dirname+'/bulk-merge.js','utf8'),context);
  return {button,feedback,storage,context,puts,confirms,poll:()=>poll(),click:()=>click(),entries,
    set approved(v){approved=v;},set fail(v){fail=v;},set disabled(v){readOnly=v;},set drafts(v){drafts=new Set(v);},set review(v){review=v;},set outcomes(v){outcomes=v;}};
}

test('bulk review uses complete owner set, confirms exact commits and reports partial rejection',async()=>{
  const h=harness();await h.poll();h.approved=false;await h.click();
  assert.equal(h.puts.length,0);assert.match(h.confirms[0],/T0002/);assert.match(h.confirms[0],/a{40}/);
  assert.match(h.confirms[0],/Changed PR head/);assert.match(h.confirms[0],/Queue paused by T0004/);
  assert.match(h.confirms[0],/Combined tests, migration checks/);
  h.approved=true;await h.click();
  const body=JSON.parse(h.puts[0].body);assert.equal(body.entries.length,2);assert.equal(body.board,'/um/board/data.json');
  assert.equal(h.puts[0].headers['X-Board-Token'],'fresh-session');
  assert.match(h.feedback.textContent,/T0001: accepted/);assert.match(h.feedback.textContent,/T0002: rejected/);
  assert.equal(h.storage.size,0);assert.equal(body.entries[0].tested_commit,undefined);
});

test('uncertain requests survive reload and retry the identical body without reconfirming new heads',async()=>{
  const h=harness();await h.poll();h.fail=true;await h.click();
  assert.equal(h.storage.size,1);assert.match(h.feedback.textContent,/Outcome may be partial/);
  const old=h.puts[0].body;
  const next=harness(h.storage);await next.poll();assert.match(next.button.textContent,/Retry reviewed/);
  await next.click();assert.equal(next.puts[0].body,old);assert.equal(next.confirms.length,0);assert.equal(next.storage.size,0);
});

test('drafts, empty batches and disabled workflow cannot enqueue hidden or unsaved selections',async()=>{
  const h=harness();await h.poll();h.drafts=['T0002'];await h.click();
  assert.equal(JSON.parse(h.puts[0].body).entries.length,1);assert.match(h.confirms[0],/Unsaved todo/);
  h.review={repository:'/um/code',entries:[],excluded:[{id:'T0001',reason:'Already queued'}]};await h.click();
  assert.match(h.feedback.textContent,/No eligible/);assert.equal(h.puts.length,1);
  h.disabled=true;await h.poll();assert.equal(h.button.hidden,true);await h.click();assert.equal(h.puts.length,1);
  h.disabled=false;h.context.compatibility.read_only=true;await h.poll();assert.equal(h.button.disabled,true);
});

test('simultaneous clicks cannot create a second batch and unknown per-entry outcomes retain recovery',async()=>{
  const h=harness();await h.poll();h.outcomes=[{id:'T0001',status:'unknown',message:'Storage response lost'}];
  await Promise.all([h.click(),h.click()]);assert.equal(h.puts.length,1);assert.equal(h.confirms.length,1);
  assert.equal(h.storage.size,1);assert.match(h.button.textContent,/Retry reviewed/);
});

test('bulk control is adjacent to publication and loads its served script',()=>{
  const html=fs.readFileSync(__dirname+'/index.html','utf8');
  assert.match(html,/id="publication-push"[^]*?<\/button><button id="workflow-merge-all"/);
  assert.match(html,/<script src="\/bulk-merge.js" defer><\/script>/);
});
