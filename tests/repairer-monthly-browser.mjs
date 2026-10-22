import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const pkg=fs.readdirSync('node_modules/.pnpm').find(n=>n.startsWith('playwright-core@'));
const {chromium}=require(path.resolve('node_modules/.pnpm',pkg,'node_modules/playwright-core'));

const root=process.cwd(),summary=JSON.parse(fs.readFileSync('outputs/repairers_2026/repairers_2026_fast.json','utf8'));
const server=http.createServer((req,res)=>{
  const file=path.resolve(root,decodeURIComponent(new URL(req.url,'http://localhost').pathname).slice(1));
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.html')?'text/html':file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'application/json');
  res.end(fs.readFileSync(file));
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try{
  browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1600,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('https://**/*',route=>{
    const p=new URL(route.request().url()).pathname;
    if(p.endsWith('/repairers/fast/meta.json'))return route.fulfill({json:summary.meta});
    if(p.endsWith('/repairers/fast.json'))return route.fulfill({json:summary});
    return route.fulfill({json:null});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/repairs.html`);
  await page.waitForFunction(()=>document.querySelectorAll('#roShopTable tbody tr').length>0);
  await page.waitForFunction(()=>!document.getElementById('calcFloat')?.classList.contains('active'));
  assert.equal(await page.evaluate(()=>selectedRepairYear),2026);
  assert.equal(await page.locator('#roShopMonthly').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#roShopTable thead th').count(),14);
  await fs.promises.mkdir('tmp/repairer-monthly',{recursive:true});
  await page.screenshot({path:'tmp/repairer-monthly/desktop.png',fullPage:true});
  await page.locator('#roShopAll').click();
  await page.waitForFunction(()=>document.querySelectorAll('#roShopTable thead th').length===2);
  assert.equal(await page.locator('#roShopAll').getAttribute('aria-pressed'),'true');
  await page.locator('#roShopMonthly').click();
  await page.locator('#roShopMetric').selectOption('avg_confirmed_cost');
  await page.waitForFunction(()=>document.getElementById('roShopCaption').textContent.includes('Avg Cost'));
  await page.locator('#repairPeriodSelect').selectOption('2026-02');
  await page.waitForFunction(()=>document.querySelectorAll('#roShopTable thead th').length===3);
  assert.equal(await page.locator('#roShopTable thead th').nth(1).textContent(),'Feb');
  await page.locator('#repairPeriodSelect').selectOption('total');
  await page.locator('#roShopMetric').selectOption('confirmed_cost');
  await page.locator('#roMapToggle').click();
  await page.locator('#roStateBars button').first().click();
  await page.waitForFunction(()=>document.getElementById('roStateScope').textContent!=='(All states)');
  const filteredState=await page.locator('#roStateScope').textContent();
  await page.locator('#roAllStates').click();
  await page.waitForFunction(()=>document.getElementById('roStateScope').textContent==='(All states)');
  // Deterministic detail fixtures isolate month drill-down from source availability.
  await page.evaluate(()=>{
    ensureRepairDetailsLoaded=async()=>[];
    repairInfo=()=>({repairId:'fixture',repairName:'Fixture',state:'QLD'});
    overviewRepairMatches=()=>true;
    repairCostTicketEntries=()=>[
      {id:'JAN',ticket:{TicketStatusText:'Approved',cost:100},day:'2026-01-10'},
      {id:'FEB',ticket:{TicketStatusText:'Approved',cost:200},day:'2026-02-10'}
    ];
    repairEntryDateKey=entry=>entry.day;
    normalizedChassis=()=>'';
    openPoRepairCostFromTicket=t=>t.cost;
  });
  await page.locator('#roShopTable button[data-month="2026-01"]').first().click();
  await page.waitForFunction(()=>document.querySelector('#roDetailBody tbody'));
  assert.equal(await page.locator('#roDetailBody tbody tr').count(),1);
  assert.match(await page.locator('#roDetailBody').textContent(),/JAN/);
  assert.doesNotMatch(await page.locator('#roDetailBody').textContent(),/FEB/);
  await page.locator('#roDetailClose').click();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'tmp/repairer-monthly/mobile.png',fullPage:true});
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
  assert.equal(overflow,false,'horizontal scrolling stays inside the monthly table');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({checks:'2026 default, monthly default, All, metric, month filter, state filter, month drill-down, mobile overflow',filteredState,errors}));
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
