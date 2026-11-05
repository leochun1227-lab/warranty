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
  assert.equal(await page.locator('#overviewExportBtn').count(),0);
  // Isolate the workbook/UI checks from live approval history availability.
  await page.evaluate(()=>{
    loadApprovalExportRecords=async()=>({asOf:'2026-11-05',records:[
      {id:'fixture-2025',decisionKey:'approved',date:'2025-01-06',amount:125.5,dealer:'204642',repairer:{repairName:'Fixture',state:'QLD'}},
      {id:'fixture-2026',decisionKey:'approved',date:'2026-01-06',amount:225.5,dealer:'204642',repairer:{repairName:'Fixture',state:'QLD'}},
    ]});
  });
  async function checkRankingExport(){
    const visibleNames=await page.locator('#roShopTable tbody tr th').allTextContents();
    const pending=page.waitForEvent('download');
    await page.locator('#roShopExport').click();
    const download=await pending;
    assert.match(download.suggestedFilename(),/^Monthly_Approval_Amounts_2025_2026_.*\.xls$/);
    const xml=await fs.promises.readFile(await download.path(),'utf8');
    const exported=await page.evaluate(xml=>{
      const doc=new DOMParser().parseFromString(xml,'text/xml');
      if(doc.getElementsByTagName('parsererror').length)throw Error('Invalid workbook XML');
      return [...doc.getElementsByTagName('Worksheet')].map(sheet=>({
        name:sheet.getAttribute('ss:Name'),rows:[...sheet.getElementsByTagName('Row')].map(row=>[...row.getElementsByTagName('Data')].map(cell=>({value:cell.textContent,type:cell.getAttribute('ss:Type')}))),
      }));
    },xml);
    assert.deepEqual(exported.map(s=>s.name),['Monthly Approvals','Top 8 Repairers']);
    assert.deepEqual(exported[0].rows.slice(6,9).map(r=>r[0].value),['Green RV','Leisure Lion','Caravan Hub']);
    assert.equal(Number(exported[0].rows[6][1].value),125.5);
    for(const sheet of exported){
      assert.equal(sheet.rows.filter(row=>row[0]?.value==='2025').length,1);
      assert.equal(sheet.rows.filter(row=>row[0]?.value==='2026').length,1);
    }
    const sheet=exported[1],header=sheet.rows.findIndex(row=>row[0]?.value==='2026')+1;
    assert.deepEqual(sheet.rows.slice(6,6+visibleNames.length).map(r=>r[0].value),visibleNames);
    assert.deepEqual(sheet.rows.slice(header+1,header+1+visibleNames.length).map(r=>r[0].value),visibleNames);
    const expected=await page.evaluate(names=>{
      return names.map(name=>Object.entries(precomputedRepairPeriods).filter(([month])=>/^2026-/.test(month)).sort(([a],[b])=>a.localeCompare(b)).map(([month,payload])=>{
        const rows=repairersToWorkingRows(payload.repairers);
        return rows.filter(row=>repairerDisplayNameFromRow(row)===name).reduce((sum,row)=>sum+Number(row.confirmed_cost||0),0);
      }));
    },visibleNames);
    sheet.rows.slice(header+1,header+1+visibleNames.length).forEach((row,i)=>{
      row.slice(3).forEach((cell,j)=>{
        assert.equal(cell.type,'Number');
        assert.ok(Math.abs(Number(cell.value)-expected[i][j])<0.0051);
      });
    });
  }
  await checkRankingExport();
  assert.equal(await page.evaluate(()=>selectedRepairYear),2026);
  assert.equal(await page.locator('#roShopMonthly').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#roShopTable thead th').count(),14);
  await fs.promises.mkdir('tmp/repairer-monthly',{recursive:true});
  await page.screenshot({path:'tmp/repairer-monthly/desktop.png',fullPage:true});
  await page.locator('#roShopAll').click();
  await page.waitForFunction(()=>document.querySelectorAll('#roShopTable thead th').length===2);
  assert.equal(await page.locator('#roShopAll').getAttribute('aria-pressed'),'true');
  await checkRankingExport();
  await page.locator('#roShopMonthly').click();
  await page.locator('#roShopMetric').selectOption('avg_confirmed_cost');
  await page.waitForFunction(()=>document.getElementById('roShopCaption').textContent.includes('Avg Cost'));
  await page.locator('#repairPeriodSelect').selectOption('2026-02');
  await page.waitForFunction(()=>document.querySelectorAll('#roShopTable thead th').length===3);
  assert.equal(await page.locator('#roShopTable thead th').nth(1).textContent(),'Feb');
  await checkRankingExport();
  await page.locator('#repairPeriodSelect').selectOption('total');
  await page.locator('#roShopMetric').selectOption('confirmed_cost');
  await page.locator('#roMapToggle').click();
  await page.locator('#roStateBars button').first().click();
  await page.waitForFunction(()=>document.getElementById('roStateScope').textContent!=='(All states)');
  const filteredState=await page.locator('#roStateScope').textContent();
  await page.locator('#roShopMetric').selectOption('ticket_count');
  await checkRankingExport();
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
  console.log(JSON.stringify({checks:'2026 default, monthly default, All, metric, month/state filters, two export sheets with 2025/2026 sections, three dealer groups, selected Top 8, monthly amounts reconcile, month drill-down, mobile overflow',filteredState,errors}));
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
