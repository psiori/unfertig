// Run against the declared disposable preview recipe, never a live board.
// Requires Playwright; PLAYWRIGHT_MODULE may select an existing installation.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
const browser=await chromium.launch({executablePath:process.env.CHROMIUM || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
try {
const page=await browser.newPage({viewport:{width:1280,height:900},hasTouch:true});
const errors=[]; page.on('pageerror',e=>errors.push(e.message));
const long='A long task title with a useful description '.repeat(25);
const path='/workspace/'+ 'unbroken_path_segment'.repeat(150);
const phases=['queued','implementing','ready','merge_queued','resolving_conflict','migration_required','restarting','done','resolution_blocked','historical'];
const todos=phases.map((phase,i)=>({id:'T'+String(i+1).padStart(4,'0'),name:i%2?long:'Short task',status:['done','historical'].includes(phase)?'closed':'started',date_closed:new Date().toISOString(),description:'Fixture',source_ideas:[],priority:'normal',author:'SL',tags:[],workflow:{phase}}));
todos[8].name=path;
const result={enabled:true,configured:true,active_count:1,max_workers:4,draining:true,queue_blocked_by:'T0009',runs:Object.fromEntries(todos.map((t,i)=>[t.id,{phase:phases[i],branch:'codex/fixture',message:i%2?'Waiting for T0009: missing requirement\n'+path+'\n'+'diagnostic line\n'.repeat(60):'Short progress',pr_url:'https://example.invalid/pull/'+i,...(i===8?{message:'Blocked: cannot resolve conflicting requirements. '+path,conflicted_paths:[path],git_diagnostics:{stderr:path}}:{})}]))};
await page.route('**/api/workflow',r=>r.fulfill({json:result}));
await page.goto(process.env.PIPELINE_PREVIEW_URL || 'http://127.0.0.1:18854');
await page.waitForFunction(()=>typeof data!=='undefined' && !!token);
await page.evaluate(todos=>{data.todos=todos;document.dispatchEvent(new Event('unfertig:todos-rendered'));},todos);
await page.waitForSelector('.pipeline-ticket');
assert.equal(await page.locator('.pipeline-ticket').count(),9);
assert.equal(await page.locator('.pipeline-details[open]').count(),0);
for(const width of [1280,768,375,320]) {
 await page.setViewportSize({width,height:900});
 const size=await page.locator('[data-integration-pipeline]').evaluate(e=>({scroll:e.scrollWidth,client:e.clientWidth}));
 assert.ok(size.scroll<=size.client,JSON.stringify({width,size}));
 const heights=await page.locator('.pipeline-ticket').evaluateAll(es=>es.map(e=>e.getBoundingClientRect().height));
 assert.ok(Math.max(...heights)<310,JSON.stringify({width,heights}));
 await page.locator('[data-integration-pipeline]').screenshot({path:`/tmp/t0054-${width}.png`});
}
const ticket=page.locator('[data-pipeline-ticket="T0002"]'), summary=ticket.locator('summary');
await summary.focus(); await page.keyboard.press('Enter');
assert.ok(await ticket.locator('details').evaluate(e=>e.open));
await page.keyboard.press('Tab');
assert.ok(await ticket.locator('[data-pipeline-reader]').evaluate(e=>e===document.activeElement));
await ticket.locator('[data-pipeline-reader]').evaluate(e=>e.scrollTop=180);
await page.evaluate(()=>window.savedPre=document.querySelector('[data-pipeline-ticket="T0002"] [data-pipeline-reader]'));
result.runs.T0002.message+='\nFresh diagnostic update';
await page.waitForFunction(()=>document.querySelector('[data-pipeline-ticket="T0002"] [data-pipeline-reader]').textContent.includes('Fresh diagnostic update'));
assert.ok(await ticket.locator('details').evaluate(e=>e.open));
assert.ok(await ticket.locator('[data-pipeline-reader]').evaluate(e=>e===window.savedPre && e===document.activeElement && e.scrollTop===180));
result.runs.T0002.phase='ready';
await page.waitForFunction(()=>document.querySelector('[data-pipeline-ticket="T0002"] .pipeline-phase').textContent==='ready');
assert.ok(await ticket.locator('[data-pipeline-reader]').evaluate(e=>e===window.savedPre && e===document.activeElement && e.scrollTop===180));
assert.ok(await ticket.locator('details').evaluate(e=>e.open));
await summary.focus(); await page.keyboard.press('Space');
assert.equal(await ticket.locator('details').evaluate(e=>e.open),false);
await summary.tap(); assert.ok(await ticket.locator('details').evaluate(e=>e.open));
const sizes=await ticket.locator('[data-pipeline-reader]').evaluate(e=>({scroll:e.scrollWidth,client:e.clientWidth}));
assert.ok(sizes.scroll<=sizes.client);
await ticket.screenshot({path:'/tmp/t0054-expanded.png'});
await summary.tap();assert.equal(await ticket.locator('details').evaluate(e=>e.open),false);
assert.deepEqual(errors,[]);
console.log('PASS: 9 visible stage fixtures; history excluded; 1280/768/375/320 widths; bounded rows; keyboard/touch open-close; full diagnostics; polling and stage moves retain reader, focus and scroll.');
} finally {await browser.close();}
})();
