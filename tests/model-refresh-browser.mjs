import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const pkg=fs.readdirSync('node_modules/.pnpm').find(n=>n.startsWith('playwright-core@'));
const {chromium}=require(path.resolve('node_modules/.pnpm',pkg,'node_modules/playwright-core'));
const deployed=JSON.parse(fs.readFileSync('outputs/analysis_model_mtm_summary.json','utf8'));
let published=null,version=null,offline=false;
const server=http.createServer((req,res)=>{
  const file=path.resolve(new URL(req.url,'http://localhost').pathname.slice(1));
  if(!file.startsWith(process.cwd()+path.sep)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
  res.setHeader('Content-Type',file.endsWith('.html')?'text/html':file.endsWith('.js')?'application/javascript':file.endsWith('.json')?'application/json':'text/css');
  let body=fs.readFileSync(file);
  if(file.endsWith('analysis.html'))body=body.toString().replace(/const IS_LOCAL_CONTEXT = .*?;/,'const IS_LOCAL_CONTEXT = false;');
  res.end(body);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const context=await browser.newContext(),page=await context.newPage(),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('https://**/*',async route=>{
    const url=new URL(route.request().url());requests.push(url.pathname);
    if(!url.hostname.endsWith('firebasedatabase.app')||offline)return route.abort();
    let value=null;
    if(url.pathname.endsWith('/modelMtmSummary/generatedAt.json'))value=version;
    if(url.pathname.endsWith('/modelMtmSummary.json'))value=published;
    return route.fulfill({json:value});
  });
  const ready=()=>page.waitForFunction(()=>document.documentElement.dataset.modelReadyMs);
  const badge=text=>page.waitForFunction(t=>document.getElementById('warrantyDataUpdatedBadge')?.textContent.includes(t),text);
  const saved=v=>page.waitForFunction(async version=>(await WarrantyPageCache.getPageRecord('model-summary:v1'))?.version===version,v);
  await page.goto(`http://127.0.0.1:${server.address().port}/analysis.html`);await ready();await badge('awaiting server update');
  assert.equal(await page.evaluate(async()=>await WarrantyPageCache.getPageRecord('model-summary:v1')),null,'unpublished static fallback is not confirmed as current');
  version='2026-10-08T10:00:00Z';published={...deployed,generatedAt:version};
  await page.reload();await ready();await saved(version);await badge('refreshed');
  version='2026-10-09T10:00:00Z';
  // Both the remote body and deployed fallback lag the independently read version.
  await page.reload();await ready();await badge('refresh unavailable');
  assert.equal(await page.evaluate(async()=>(await WarrantyPageCache.getPageRecord('model-summary:v1')).version),published.generatedAt);
  published={...deployed,generatedAt:version};
  await page.reload();await ready();await saved(version);await badge('refreshed');
  offline=true;await page.reload();await ready();await badge('refresh unavailable');
  assert.equal(await page.evaluate(async()=>(await WarrantyPageCache.getPageRecord('model-summary:v1')).version),version);
  assert.ok(!requests.some(p=>p.endsWith('/tickets.json')||p.endsWith('/modelMtmCache.json')),'summary refresh never downloads bulk details');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({checks:'missing publication, new publication, mismatched generation, offline cache, no bulk downloads'}));
}finally{await browser.close();await new Promise(r=>server.close(r));}
