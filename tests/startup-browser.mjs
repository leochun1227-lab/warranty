import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {gzipSync} from 'node:zlib';
import {buildDashboardStartup} from '../build_dashboard_startup.mjs';

const require=createRequire(import.meta.url);
let chromium;
try{({chromium}=require('playwright'));}catch{
  const packageDir=fs.readdirSync('node_modules/.pnpm').find(name=>name.startsWith('playwright-core@'));
  ({chromium}=require(path.resolve('node_modules/.pnpm',packageDir,'node_modules/playwright-core')));
}
const root=process.cwd();
const server=http.createServer((req,res)=>{
  const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\//,'')||'index.html';
  // These fixtures exercise the published-snapshot path, independently of a
  // developer's real-data localhost snapshot.
  if(name==='outputs/team_dashboard_startup.json'){res.writeHead(404);res.end();return;}
  const file=path.resolve(root,name);
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  const content=fs.readFileSync(file);
  res.setHeader('Content-Type',file.endsWith('.html')?'text/html':file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'application/json');
  res.setHeader('Content-Encoding','gzip');res.end(gzipSync(content));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true,channel:'msedge'});
const output=path.join(root,'tmp/startup-check');fs.mkdirSync(output,{recursive:true});
const row={id:'0001',status:'Waiting',dealer:'Dealer A',employee:'Kylie Clayton',created:'2026-08-01',amount:100};
const fixture={team:{generatedAt:'2026-10-07T00:00:00Z',minDate:'2026-08-01',defaultView:'all',claimOptions:[{key:'all',label:'All Claims'}],views:{all:{
  generatedAt:'2026-10-07T00:00:00Z',trend:[{date:'2026-08-01',critical:1},{date:'2026-08-31',critical:1},{date:'2026-09-01',critical:1},{date:'2026-09-30',critical:1}],summary:{criticalNow:1},
  logs:[],currentCriticalRows:[row],dailyCriticalRows:{'2026-09-30':[row]},periodSnapshots:{
    '2026-09-01|2026-09-30':{summary:{criticalNow:1,approvedAmount:300,approvedCostTickets:1,approvedTickets:1,unapprovedTickets:1},
      approvalTicketRows:[{id:'00011',decisionKey:'approved',amount:300,decisionDate:'2026-09-12'},{id:'00012',decisionKey:'unapproved',amount:0,decisionDate:'2026-09-13'}],
      employeeApprovalRows:[{name:'Kylie Clayton',approved:1,unapproved:1}]},
    '2026-08-01|2026-08-31':{summary:{criticalNow:1,approvedAmount:150,approvedCostTickets:1,approvedTickets:1,unapprovedTickets:0},
      approvalTicketRows:[{id:'00013',decisionKey:'approved',amount:150,decisionDate:'2026-08-12'}]}
  },topDealers:[{dealer:'Dealer A',criticalTickets:1}],statusMix:[{label:'Waiting',count:1}]
}}},employee:{views:{all:{stats:[]}}},employeeDirectory:{}};
const built=buildDashboardStartup(fixture);
const source={ctmTicketStatusMonitorV44:{analytics:{teamDashboard:fixture.team,team:fixture.team,employee:fixture.employee,meta:{generatedAt:fixture.team.generatedAt},teamStartup:null},employeeDirectory:{}}};
const metrics=page=>page.locator('.kpi, #teamAssignGrid, #employeeApprovalBars, #criticalLogRows, #speedRows, #dealerBars, #statusMix, #trendSideSummary, #modelFailure90Total, #ticketCount').allTextContents();
let versionDelay=0;
const contexts=[];
async function openContext(){
  const context=await browser.newContext();contexts.push(context);
  const requests=[];
  await context.route('https://**/*',async route=>{
    const url=new URL(route.request().url());
    if(!url.hostname.endsWith('firebasedatabase.app')){await route.abort();return;}
    const p=decodeURIComponent(url.pathname).replace(/^\//,'').replace(/\.json$/,'');requests.push(p);
    await new Promise(resolve=>setTimeout(resolve,p.endsWith('generatedAt')?versionDelay:100));
    let value=source;for(const key of p.split('/'))value=value?.[key];
    await route.fulfill({json:value??null});
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  return {context,page,requests,errors};
}
try{
  const baseline=await openContext();await baseline.page.goto(base+'/index.html');
  await baseline.page.waitForFunction(()=>document.documentElement.dataset.teamReadyMs,{timeout:15000});
  const expected=await metrics(baseline.page);
  assert.equal(await baseline.page.locator('#kpiApproved').textContent(),'$300');
  source.ctmTicketStatusMonitorV44.analytics.teamStartup={...built,page:JSON.stringify(built.page)};
  const fast=await openContext();await fast.page.goto(base+'/index.html');
  await fast.page.waitForFunction(()=>document.documentElement.dataset.teamLoadingHiddenMs,{timeout:10000});
  assert.deepEqual(await metrics(fast.page),expected,'prebuilt figures equal original calculation');
  assert.ok(!fast.requests.some(p=>/\/team\/views\//.test(p)),'first visit must not download detail history');
  const cold=await fast.page.evaluate(()=>Number(document.documentElement.dataset.teamLoadingHiddenMs));
  await fast.page.waitForFunction(async()=>!!(await WarrantyPageCache.getPageRecord('team-dashboard:render-snapshot-v20')));
  versionDelay=5000;fast.requests.length=0;
  await fast.page.reload({waitUntil:'domcontentloaded'});
  await fast.page.waitForFunction(()=>document.documentElement.dataset.teamLoadingHiddenMs,{timeout:2000});
  const warm=await fast.page.evaluate(()=>Number(document.documentElement.dataset.teamLoadingHiddenMs));
  assert.ok(warm<2000);assert.deepEqual(await metrics(fast.page),expected);
  assert.ok(!fast.requests.some(p=>p.endsWith('/teamStartup')),'slow version check cannot hold cached figures');
  await fast.page.screenshot({path:path.join(output,'dashboard.png'),fullPage:true});
  const updated=structuredClone(fixture);
  updated.team.generatedAt='2026-10-08T00:00:00Z';
  updated.team.views.all.summary.criticalNow=2;
  updated.team.views.all.trend.at(-1).critical=2;
  updated.team.views.all.dailyCriticalRows['2026-09-30'].push({...row,id:'0002'});
  updated.team.views.all.currentCriticalRows.push({...row,id:'0002'});
  const newer=buildDashboardStartup(updated);
  source.ctmTicketStatusMonitorV44.analytics.teamStartup={...newer,page:JSON.stringify(newer.page)};
  source.ctmTicketStatusMonitorV44.analytics.team=updated.team;
  source.ctmTicketStatusMonitorV44.analytics.meta.generatedAt=updated.team.generatedAt;
  versionDelay=200;
  await fast.page.reload({waitUntil:'domcontentloaded'});
  await fast.page.waitForFunction(()=>document.getElementById('kpiCritical').textContent==='2',{timeout:10000});
  assert.equal(await fast.page.locator('body').evaluate(el=>el.classList.contains('calculating')),false);
  assert.match(await fast.page.locator('#warrantyDataUpdatedBadge').textContent(),/refreshed/);
  versionDelay=0;
  const model=await openContext();await model.page.goto(base+'/analysis.html');
  await model.page.waitForFunction(()=>document.documentElement.dataset.modelReadyMs,{timeout:10000});
  const modelCold=await model.page.evaluate(()=>Number(document.documentElement.dataset.modelReadyMs));
  const before=await metrics(model.page);
  await model.page.selectOption('#modelPeriodSelect','2026-09');
  await model.page.selectOption('#modelPeriodSelect','all');
  assert.deepEqual(await metrics(model.page),before,'switching periods preserves totals');
  const details=[];model.page.on('request',request=>{if(request.url().includes('model_mtm_details/'))details.push(request.url());});
  const download=model.page.waitForEvent('download');await model.page.click('#exportTicketDetailsBtn');await download;
  assert.equal(details.length,1,'export loads just one immutable slice');
  await model.page.screenshot({path:path.join(output,'model.png'),fullPage:true});
  const cacheChecks=await model.page.evaluate(async()=>{
    const cache=WarrantyPageCache,applied=[];
    await cache.setPage('test:snapshot','old',{generatedAt:'old',count:1});
    let resolveVersion;
    const version=new Promise(resolve=>{resolveVersion=resolve;});
    await cache.loadSnapshot({key:'test:snapshot',readVersion:()=>version,fetchValue:async()=>({generatedAt:'new',count:2}),
      versionOf:value=>value.generatedAt,apply:(value,info)=>applied.push([value.count,info.mode])});
    const immediate=applied.slice();resolveVersion('new');
    await new Promise(resolve=>setTimeout(resolve,50));
    await cache.loadSnapshot({key:'test:snapshot',readVersion:async()=>{throw Error('offline');},fetchValue:()=>{throw Error('must not download');},
      versionOf:value=>value.generatedAt,apply:(value,info)=>applied.push([value.count,info.mode])});
    await new Promise(resolve=>setTimeout(resolve,50));
    return {immediate,applied,badge:document.getElementById('warrantyDataUpdatedBadge').textContent};
  });
  assert.deepEqual(cacheChecks.immediate,[[1,'checking']]);
  assert.deepEqual(cacheChecks.applied,[[1,'checking'],[2,'fresh'],[2,'checking']]);
  assert.match(cacheChecks.badge,/refresh unavailable/);
  assert.deepEqual([...baseline.errors,...fast.errors,...model.errors],[]);
  const report={dashboardColdMs:cold,dashboardWarmWith5sVersionMs:warm,modelColdMs:modelCold,modelSummaryBytes:fs.statSync('outputs/analysis_model_mtm_summary.json').size,
    modelSummaryGzipBytes:gzipSync(fs.readFileSync('outputs/analysis_model_mtm_summary.json')).length,
    dashboardSnapshotBytes:Buffer.byteLength(JSON.stringify(built)),note:'Local browser; synthetic dashboard data, actual generated model data; Firebase responses mocked.'};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{
  for(const context of contexts)await context.close();
  await browser.close();await new Promise(resolve=>server.close(resolve));
}
