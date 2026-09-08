// Disposable preview only. PLAYWRIGHT_MODULE can select an existing installation.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try {
  const context=await browser.newContext(), page=await context.newPage();
  const url=process.env.VIEW_PREVIEW_URL || 'http://127.0.0.1:18850';
  const snapshot=await (await context.request.get(url+'/api/state')).json();
  const fixture=structuredClone(snapshot);
  fixture.context={...fixture.context,project_id:'board-a',data:'/disposable/a/data.json',project_name:'um-unfertig'};
  fixture.data.todos=['open','started','closed'].map((status,i)=>({id:'T000'+(i+1),name:status+' task',description:'Fixture',status,date_closed:'2026-09-08T11:00:00Z',source_ideas:[],tags:['ui'],group:'Module',author:'SL',created_by:'Codex',priority:'normal',date_entered:'2026-09-08T10:00:00Z'}));
  fixture.data.ideas=[];
  const records=JSON.stringify(fixture.data);
  let current=fixture, sources=[];
  const mutations=[]; context.on('request',r=>{if (['PUT','POST','DELETE','PATCH'].includes(r.method()) && !r.url().endsWith('/api/processing/presence')) mutations.push(r.url());});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/api/state',r=>r.fulfill({json:current}));
  await context.route('**/api/aggregate',r=>r.fulfill({json:{sources}}));
  await page.goto(url);
  const ready=()=>page.waitForFunction(()=>typeof data!=='undefined' && !!data && !!viewKey);
  const values=()=>page.evaluate(()=>Object.fromEntries(Object.keys(viewDefaults).map(id=>{const e=document.getElementById(id);return [id,e.type==='checkbox'?e.checked:e.value];})));
  await ready();
  const defaults=await values();
  assert.equal(defaults['status-filter'],'active');
  assert.equal(await page.locator('#todos > .todo').count(),2);
  for(const width of [1280,390]) {
   await page.setViewportSize({width,height:900});
   await page.locator('#status-filter').selectOption('closed');
   await page.locator('#group-filter').selectOption('Module');
   await page.locator('#tag-filter').selectOption('ui');
   await page.locator('#sort').selectOption('name');
   await page.locator('#search').fill('closed task');
   await page.locator('#group-by').check();
   await page.locator('#show-processed').check();
   const expected=await values();
   await page.reload();await ready();assert.deepEqual(await values(),expected);
   assert.equal(await page.locator('#todos .todo.closed').count(),1);
   await page.screenshot({path:`/tmp/t0050-${width}.png`,fullPage:true});
   await page.locator('#search').fill('no match');
   await page.locator('#empty-action').click();
   assert.equal((await values())['status-filter'],'all');
   await page.reload();await ready();
   assert.equal((await values())['status-filter'],'all');
   assert.equal((await values()).search,'');
   assert.equal(await page.locator('#todos .todo').count(),3);
  }
  // Same origin, different board (including a switch without a browser reload).
  current=structuredClone(fixture); current.context.project_id='board-b'; current.context.data='/disposable/b/data.json';
  await page.evaluate(()=>load(true));assert.deepEqual(await values(),defaults);
  current=fixture; await page.evaluate(()=>load(true));assert.equal((await values())['status-filter'],'all');
  // A distinct tab has no view cache even if legacy global preferences exist.
  await page.evaluate(()=>localStorage.setItem('little-board.status-filter','closed'));
  const fresh=await context.newPage();await fresh.goto(url);await fresh.waitForFunction(()=>typeof data!=='undefined'&&!!data);
  assert.equal(await fresh.locator('#status-filter').inputValue(),'active');await fresh.close();
  // A rejected filter change must neither replace the restored value nor persist.
  await page.evaluate(()=>document.querySelector('.todo-editor').dataset.dirty='true');
  await page.locator('#status-filter').selectOption('open');
  assert.equal((await values())['status-filter'],'all');
  await page.evaluate(()=>delete document.querySelector('.todo-editor').dataset.dirty);
  await page.reload();await ready();assert.equal((await values())['status-filter'],'all');
  // Malformed envelopes and obsolete/mistyped fields recover to useful defaults.
  for(const saved of ['{', 'null', JSON.stringify({version:9,values:{}}),JSON.stringify({version:1,values:{'status-filter':'retired','sort':'removed','group-filter':'gone','tag-filter':'gone','group-by':'true','show-processed':null,search:4}})]) {
   await page.evaluate(saved=>sessionStorage.setItem(viewKey,saved),saved);await page.reload();await ready();assert.deepEqual(await values(),defaults);
  }
  await page.evaluate(()=>sessionStorage.setItem(viewKey,JSON.stringify({version:1,values:{'status-filter':'closed',search:'', 'group-by':false}})));
  await page.reload();await ready();assert.equal((await values())['status-filter'],'closed');
  // Hash links open a task initially, but do not override a saved reload view.
  await page.goto(url+'/#todo-T0001');await page.reload();await ready();assert.equal((await values())['status-filter'],'closed');
  // Aggregate choices restore after source arrival; same across HTTP/filesystem.
  current=structuredClone(fixture);current.context.mode='aggregation';current.context.project_id='inbox';current.context.data='/disposable/inbox/data.json';current.data={...fixture.data,todos:[]};
  sources=['a','b'].map(project_id=>({project_id,name:project_id,status:'reachable',transport:'http',url,revision:'1',data:fixture.data}));
  await page.goto(url);await ready();await page.waitForFunction(()=>document.querySelector('#project-filter').options.length===3);
  await page.locator('#project-filter').selectOption('b');
  await page.locator('#group-filter').selectOption(JSON.stringify(['b','Module']));
  await page.locator('#tag-filter').selectOption(JSON.stringify(['b','ui']));
  await page.locator('#status-filter').selectOption('closed');
  await page.locator('#group-by').check();
  const aggregateExpected=await values();
  for(const transport of ['http','filesystem']) {
   sources.forEach(s=>s.transport=transport);
   await page.reload();await ready();await page.waitForFunction(()=>document.querySelector('#project-filter').value==='b');
   assert.deepEqual(await values(),aggregateExpected);
   assert.equal(await page.locator('#todos .todo.closed').count(),1);
  }
  // Cache failures must not prevent normal filtering.
  await page.evaluate(()=>{Storage.prototype.setItem=()=>{throw Error('disabled');};Storage.prototype.getItem=()=>{throw Error('disabled');};});
  current=fixture;await page.evaluate(()=>load(true));assert.deepEqual(await values(),defaults);
  await page.locator('#status-filter').selectOption('closed');assert.equal(await page.locator('#todos .todo.closed').count(),1);
  assert.equal(JSON.stringify(fixture.data),records);assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
  console.log('PASS: desktop/narrow reload; all controls; clear filters; fresh tab; board switching; invalid cache; hash continuation; HTTP/filesystem aggregate restoration; disabled storage; unchanged fixture records.');
 } finally {await browser.close();}
})();
