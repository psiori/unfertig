// Run against the declared disposable preview: NODE_PATH=<playwright install> node browser_header.cjs <url> <screenshots-dir>
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless:true,args:['--no-sandbox']});
  const output = process.argv[3]; if (output) fs.mkdirSync(output,{recursive:true});
  try {
    for (const width of [1280,768,375,320]) {
      const page = await browser.newPage({viewport:{width,height:1000},hasTouch:true});
      const errors=[]; page.on('pageerror',e=>errors.push(e.message));
      let enabled=true, active=1;
      await page.route('**/api/workflow',route=>route.fulfill({json:{enabled,configured:true,runs:{},active_count:active,max_workers:4}}));
      await page.goto(process.argv[2]);
      const button=page.getByRole('button',{name:'Instance settings',exact:true});
      await page.locator('.pipeline-stages').waitFor();
      assert.equal(await page.locator('.masthead #open-settings').count(),0);
      assert.equal(await page.locator('.pipeline-header #open-settings').count(),1);
      await page.evaluate(()=>{document.querySelector('#history-state').textContent='Automatic local Git history · push only when requested';});
      for (const name of [' · Example',' · '+ 'LongProject'.repeat(25)]) {
        await page.locator('#project-name').evaluate((el,name)=>el.textContent=name,name);
        const bounds=await page.evaluate(()=>{
          const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
          return {overflow:document.documentElement.scrollWidth>innerWidth,mark:rect('.brand-mark'),history:rect('.history-banner'),button:rect('#open-settings'),header:rect('.pipeline-header'),text:rect('.brand-text')};
        });
        assert.equal(bounds.overflow,false,`${width}: page overflow`);
        assert.ok(bounds.mark.right<=bounds.text.x);
        assert.ok(bounds.mark.y<=bounds.text.y+4,`${width}: logo stays near wordmark top`);
        if (width>=768 && name===' · Example') assert.ok(Math.abs(bounds.mark.bottom-bounds.history.bottom)<=5,`${width}: bottom alignment`);
        assert.equal(bounds.button.right,bounds.header.right);
        assert.ok(bounds.button.width>=44 && bounds.button.height>=44);
        if(name===' · Example' && output) {
          await page.locator('.masthead').screenshot({path:`${output}/header-${width}.png`});
          await page.locator('.integration-pipeline').screenshot({path:`${output}/pipeline-${width}.png`});
        }
      }
      await button.focus(); await page.keyboard.press('Enter');
      await page.locator('#settings-dialog[open]').waitFor();
      await page.locator('#setting-project_name').waitFor();
      await page.keyboard.press('Escape');
      assert.ok(await button.evaluate(el=>el===document.activeElement));
      active=2; await page.waitForFunction(()=>document.querySelector('.pipeline-heading')?.textContent.includes('2/4'));
      assert.ok(await button.evaluate(el=>el===document.activeElement),'poll retains focus');
      await button.tap(); await page.locator('#settings-dialog[open]').waitFor();
      await page.getByRole('button',{name:'Close settings',exact:true}).click();
      enabled=false; await page.waitForFunction(()=>document.querySelector('[data-integration-pipeline]').hidden);
      assert.ok(await button.isVisible(),'settings available with workflow disabled');
      await button.click(); await page.locator('#settings-dialog[open]').waitFor();
      await page.keyboard.press('Escape');
      assert.deepEqual(errors,[]);
      console.log(`PASS ${width}px: layout, polling focus, keyboard, pointer, touch, disabled workflow`);
      await page.close();
    }
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
