// Disposable preview only; intercept status without changing real maintenance.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try {
  for (const width of [1280,390]) {
   const context=await browser.newContext({viewport:{width,height:900}}), page=await context.newPage();
   const healthy={phase:'idle',pending:false,blockers:[],error:'',runtime_commit:'a'.repeat(40),hooks:[],update:{phase:'installed',message:'Historical installation'},currency:{state:'current',target_commit:'a'.repeat(40),checked_at:'2026-09-09T00:00:00Z',message:'Current'}};
   let state=structuredClone(healthy), offline=false, release;
   let first=new Promise(resolve=>release=resolve), requests=0, retries=0;
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await context.route('**/api/maintenance',async route=>{
    if(route.request().method()==='PUT') {retries++;state.hooks=[];return route.fulfill({json:state});}
    requests++;await first;
    if(offline)return route.abort('failed');
    return route.fulfill({json:state});
   });
   const url=process.env.MAINTENANCE_PREVIEW_URL || 'http://127.0.0.1:8898';
   await page.goto(url,{waitUntil:'domcontentloaded'});
   const panel=page.locator('.instance-maintenance');
   await panel.waitFor({state:'attached'});
   assert.equal(await panel.evaluate(el=>el.getBoundingClientRect().height),0,'no initial empty shell');
   release();first=null;
   await page.waitForTimeout(100);
   assert.equal(await panel.isHidden(),true);
   const top=await page.locator('main > section').first().evaluate(el=>el.getBoundingClientRect().top);
   const visible=async yes=>{await page.waitForFunction(yes=>document.querySelector('.instance-maintenance').hidden!==yes,yes);};
   state={...healthy,currency:{state:'outdated',message:'Update available'}};await visible(true);
   await panel.locator('summary').click();assert.match(await panel.innerText(),/Update available/);
   await page.screenshot({path:`/tmp/t0059-${width}-attention.png`,fullPage:true});
   state={...healthy,hooks:[{status:'complete',todo:'T1',hook:'update'}]};await visible(false);
   assert.equal(await panel.evaluate(el=>el.getBoundingClientRect().height),0);
   assert.equal(await page.locator('main > section').first().evaluate(el=>el.getBoundingClientRect().top),top,'no leftover gap');
   const before=requests;await page.waitForFunction(()=>true);await page.waitForTimeout(3100);assert.ok(requests>before,'hidden panel keeps polling');
   offline=true;await visible(true);assert.match(await panel.innerText(),/reconnecting/);
   offline=false;state=healthy;await visible(false);
   state={...healthy,pending:true,phase:'draining',blockers:['Task T1 is running']};await visible(true);
   assert.match(await panel.innerText(),/queued work is retained/);
   state={...healthy,hooks:[{status:'failed',todo:'T1',id:'event',hook:'update',message:'Failure'}]};
   await page.getByRole('button',{name:'Retry hook'}).waitFor();await page.getByRole('button',{name:'Retry hook'}).click();await visible(false);assert.equal(retries,1);
   state={...healthy,currency:null};await visible(true);assert.match(await panel.innerText(),/unknown|unavailable/);
   state=healthy;await visible(false);await page.screenshot({path:`/tmp/t0059-${width}-healthy.png`,fullPage:true});
   assert.deepEqual(errors,[]);await context.close();
  }
  console.log('PASS: desktop/narrow initial zero footprint, current/outdated/recovery, historical success, hidden polling, outage recovery, drain blockers and hook retry.');
 } finally {await browser.close();}
})();
