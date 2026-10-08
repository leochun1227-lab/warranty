import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {gzipSync} from 'node:zlib';
import {buildDeliveryStartup} from '../build_delivery_startup.mjs';
const require=createRequire(import.meta.url);
const packageDir=fs.readdirSync('node_modules/.pnpm').find(n=>n.startsWith('playwright-core@'));
const {chromium}=require(path.resolve('node_modules/.pnpm',packageDir,'node_modules/playwright-core'));
const version='2026-10-07T00:00:00Z';
const ticket=(id,issued)=>({ticket:{TicketID:id,'Sales Order':'SO'+id,'SO Created Date':'2026-08-01',TicketStatus:'YA',
  'Sales Order Details':[{Material:'M1','Order Qty':1,'Item Rejection Status':'Not Rejected','First Issue Date':issued?'2026-08-05':''}]}});
const input={historyVersion:version,ticketVersion:version,history:{day:{asOf:'2026-10-07',generatedAt:version,
  costReport:{months:[{key:'2026-08',label:'Aug 2026',cost:300,soCount:2}]}}},tickets:{'0001':ticket('0001',true),'0002':ticket('0002',false)}};
const initial=buildDeliveryStartup(input);
assert.equal(initial.page.currentSummary.awaitingNow,2);
assert.equal(initial.page.currentSummary.materialStats.openCount,1);
assert.equal(initial.page.currentSummary.issuedLeadSeriesMonth.find(r=>r.partsIssued)?.avgDaysToCompleteIssue,4);
assert.equal(initial.page.currentTickets.length,0);
let published=null,staticSnapshot=null,delay=0,offline=false,failTickets=false,current=input;
const root=process.cwd();
const server=http.createServer((req,res)=>{
  const name=new URL(req.url,'http://localhost').pathname.slice(1);
  const file=path.resolve(root,name);
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',name.endsWith('.html')?'text/html':name.endsWith('.js')?'application/javascript':'text/css');
  res.setHeader('Content-Encoding','gzip');res.end(gzipSync(fs.readFileSync(file)));
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const url=`http://127.0.0.1:${server.address().port}/delivery_flow.html`;
const browser=await chromium.launch({channel:'msedge',headless:true});
const contexts=[];
async function open(baselineMode=false){
  const context=await browser.newContext();contexts.push(context);
  const requests=[],errors=[];
  await context.route('**/*',async route=>{
    const pathname=new URL(route.request().url()).pathname;requests.push(pathname);
    if(baselineMode&&pathname.endsWith('/delivery_flow.html')){
      const html=fs.readFileSync('delivery_flow.html','utf8').replace('  loadDeliveryPage();',`(async()=>{
        state.history=await loadHistory();state.currentTickets=await loadCurrentTickets();
        state.currentSummary=buildCurrentTicketsSummary(state.currentTickets,state.history.at(-1));
        render();setPageLoading(false);
      })();`);
      return route.fulfill({contentType:'text/html',body:html});
    }
    if(pathname.endsWith('/delivery_flow_startup.json')){
      if(offline)return route.abort();
      return staticSnapshot?route.fulfill({json:staticSnapshot}):route.fulfill({status:404,body:''});
    }
    if(!route.request().url().includes('firebasedatabase.app'))return route.continue();
    if(offline)return route.abort();
    if(failTickets&&pathname.endsWith('/tickets.json'))return route.abort();
    if(delay)await new Promise(r=>setTimeout(r,delay));
    let value=null;
    if(pathname.endsWith('/latestSyncAt.json'))value=current.historyVersion;
    if(pathname.endsWith('/ticketSoSyncAt.json'))value=current.ticketVersion;
    if(pathname.endsWith('/startup.json'))value=published&&{...published,page:JSON.stringify(published.page)};
    if(pathname.endsWith('/daily.json'))value=current.history;
    if(pathname.endsWith('/tickets.json'))value=current.tickets;
    return route.fulfill({json:value});
  });
  // Avoid mixing the repository's unrelated legacy summary with the fixture.
  await context.route('**/delivery_flow_current_summary.json',r=>r.fulfill({json:null}));
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  return {context,page,requests,errors};
}
const metrics=p=>p.locator('#kpis,#flowTable,#costSummary,#agingGrid,#issuedBadge,#leadBadge').allTextContents();
const ready=p=>p.waitForFunction(()=>!document.body.classList.contains('is-loading'));
const persisted=(p,v)=>p.waitForFunction(async version=>(await WarrantyPageCache.getPageRecord('delivery-flow-v12'))?.version===version,v);
try{
  const baseline=await open(true);await baseline.page.goto(url);await ready(baseline.page);
  assert.ok(baseline.requests.some(p=>p.endsWith('/tickets.json')));
  const expected=await metrics(baseline.page);
  const charts=await baseline.page.locator('canvas').evaluateAll(nodes=>nodes.map(c=>c.toDataURL()));
  staticSnapshot=initial;
  const fast=await open();await fast.page.goto(url);await ready(fast.page);await persisted(fast.page,initial.sourceVersion);
  assert.deepEqual(await metrics(fast.page),expected);
  assert.deepEqual(await fast.page.locator('canvas').evaluateAll(nodes=>nodes.map(c=>c.toDataURL())),charts,'chart pixels must match full calculations');
  const cold=await fast.page.evaluate(()=>Number(document.documentElement.dataset.deliveryReadyMs));
  assert.ok(cold<2000);
  assert.ok(!fast.requests.some(p=>/\/(tickets|daily)\.json$/.test(p)));
  delay=4000;fast.requests.length=0;await fast.page.reload({waitUntil:'domcontentloaded'});await ready(fast.page);
  const warm=await fast.page.evaluate(()=>Number(document.documentElement.dataset.deliveryReadyMs));
  assert.ok(warm<2000);assert.deepEqual(await metrics(fast.page),expected);
  await fast.page.waitForFunction(()=>document.body.textContent.includes(' - refreshed'));
  delay=0;
  current=structuredClone(input);current.historyVersion=current.ticketVersion='2026-10-08T00:00:00Z';
  current.tickets['0003']=ticket('0003',false);published=buildDeliveryStartup(current);
  await fast.page.reload();await persisted(fast.page,published.sourceVersion);
  assert.equal(await fast.page.locator('#kpis .val').first().textContent(),'3');
  assert.ok(!fast.requests.some(p=>/\/(tickets|daily)\.json$/.test(p)),'new publication uses compact snapshot');
  await fast.page.getByRole('button',{name:'Month',exact:true}).click();
  assert.equal(await fast.page.locator('[data-gran="month"]').getAttribute('class'),'on');
  const downloadPromise=fast.page.waitForEvent('download');
  await fast.page.locator('[data-export-kind="awaiting"]').click();
  const download=await downloadPromise;
  assert.ok(fast.requests.some(p=>p.endsWith('/tickets.json')),'export downloads details on demand');
  const exported=fs.readFileSync(await download.path(),'utf8');
  for(const id of ['0001','0002','0003'])assert.ok(exported.includes(id),'export preserves '+id);
  offline=true;await fast.page.reload();await ready(fast.page);
  assert.equal(await fast.page.locator('#kpis .val').first().textContent(),'3','offline retains latest snapshot');
  await fast.page.waitForFunction(()=>document.body.textContent.includes('refresh unavailable'));
  offline=false;failTickets=true;published=null;current.historyVersion='2026-10-09T00:00:00Z';
  fast.requests.length=0;
  await fast.page.reload();await ready(fast.page);
  await fast.page.waitForFunction(()=>document.body.textContent.includes('refresh unavailable'));
  assert.equal(await fast.page.locator('#kpis .val').first().textContent(),'3');
  const retained=await fast.page.evaluate(async()=>(await WarrantyPageCache.getPageRecord('delivery-flow-v12')).version);
  assert.equal(retained,'2026-10-08T00:00:00Z|2026-10-08T00:00:00Z','failed new generation must not relabel old cache');
  assert.ok(!fast.requests.some(p=>/\/(tickets|daily)\.json$/.test(p)),'missing new snapshot cannot trigger a bulk download');
  staticSnapshot=null;
  const empty=await open();await empty.page.goto(url);await ready(empty.page);
  assert.ok((await empty.page.locator('#snapshotPill').textContent()).includes('unavailable'));
  assert.ok(!empty.requests.some(p=>/\/(tickets|daily)\.json$/.test(p)),'a first visit without any snapshot cannot download tickets');
  assert.deepEqual(fast.errors,[]);assert.deepEqual(baseline.errors,[]);
  console.log(JSON.stringify({coldMs:cold,warmWith4sNetworkMs:warm,checks:'metrics, exact chart pixels, publication refresh, offline cache, export IDs',fixture:'synthetic'}));
}finally{await browser.close();await new Promise(r=>server.close(r));}
