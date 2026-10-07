const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const os=require('node:os');
const path=require('node:path');
require('../dashboard-render-cache.js');

test('render snapshots reuse results only for the same selection and never affect exports',()=>{
  const cache=globalThis.DashboardRenderCache.create();let calls=0;
  const rows=cache.wrap('rows',()=>{calls++;return [{id:'0001',amount:0}];});
  cache.render('all|Sep',()=>{rows()[0].amount=99;assert.equal(rows()[0].amount,0);});
  assert.equal(calls,1);
  const saved=JSON.parse(JSON.stringify(cache.snapshot()));
  const next=globalThis.DashboardRenderCache.create();next.restore(saved);
  const restored=next.wrap('rows',()=>{throw Error('unnecessary calculation');});
  next.render('all|Sep',()=>assert.equal(restored()[0].id,'0001'));
  rows();assert.equal(calls,2,'outside render, detail/export reads the real source');
  cache.render('field|Sep',()=>rows());assert.equal(calls,3);
  cache.clear();assert.equal(cache.matches('field|Sep'),false);
});

test('model split preserves every chart and exact export rows without mutating the input',async()=>{
  const {writeModelCacheAssets}=await import('../model-cache-assets.mjs');
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'warranty-model-test-'));
  try{
    const slice={scope:'ALL',periodKey:'total',summary:[{series:'SRC',cost:123.45}],detailAll:{tickets:1},totals:{ticketCount:1},detailRows:[{id:'0001',chassis:'00002',cost:0}]};
    const original={generatedAt:'2026-10-07T00:00:00Z',periods:{total:{scopes:{ALL:slice}}}};
    const summary=writeModelCacheAssets(original,dir),light=summary.periods.total.scopes.ALL;
    assert.equal(light.detailRows,undefined);
    assert.deepEqual(light.summary,slice.summary);assert.deepEqual(light.totals,slice.totals);
    const rows=JSON.parse(fs.readFileSync(path.join(dir,'model_mtm_details',light.detailKey+'.json')));
    assert.deepEqual(rows.rows,slice.detailRows);
    assert.equal(rows.generatedAt,summary.generatedAt);
    assert.equal(original.periods.total.scopes.ALL.detailRows.length,1);
    const second=writeModelCacheAssets({...original,generatedAt:'2026-10-08T00:00:00Z'},dir);
    assert.notEqual(second.periods.total.scopes.ALL.detailKey,light.detailKey);
    assert.ok(fs.existsSync(path.join(dir,'model_mtm_details',light.detailKey+'.json')),'old exports remain valid');
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});

test('server startup uses page calculations and excludes bulk history from the transfer',async()=>{
  const {buildDashboardStartup}=await import('../build_dashboard_startup.mjs');
  const row={id:'0001',status:'Waiting',dealer:'Dealer A',employee:'Kylie Clayton',created:'2026-08-01'};
  const input={team:{generatedAt:'2026-10-07T00:00:00Z',minDate:'2026-09-01',views:{all:{
    trend:[{date:'2026-09-01',critical:1},{date:'2026-09-30',critical:1}],summary:{criticalNow:1},
    logs:[],currentCriticalRows:[row],dailyCriticalRows:{'2026-09-30':[row]},periodSnapshots:{}
  }}},employee:{views:{all:{stats:[]}}}};
  const result=buildDashboardStartup(input);
  assert.equal(result.schema,'team-startup-v1');
  assert.equal(result.page.team.views.all.dailyCriticalRows,undefined);
  assert.equal(result.page.team.views.all.logs,undefined);
  assert.equal(result.page.renderSnapshot.values['["criticalTotal",[]]'],1);
  assert.equal(result.page.renderSnapshot.values['["status",[]]'][0].count,1);
  assert.ok(Buffer.byteLength(JSON.stringify(result))<100000);
});

test('concurrent version requests share only an in-flight fetch, not a stale TTL',async()=>{
  let calls=0,resolve;
  const context=vm.createContext({window:{},AbortController,setTimeout,clearTimeout,
    fetch:()=>{calls++;return new Promise(r=>resolve=()=>r({ok:true,json:async()=>String(calls)}));}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../browser-page-cache.js'),'utf8'),context);
  const cache=context.window.WarrantyPageCache;
  const a=cache.fetchVersion(),b=cache.fetchVersion();assert.equal(calls,1);
  resolve();assert.deepEqual(await Promise.all([a,b]),['1','1']);
  const c=cache.fetchVersion();assert.equal(calls,2);resolve();assert.equal(await c,'2');
});
