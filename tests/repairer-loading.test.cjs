const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname,'..','repairs.html'),'utf8');
const cacheCode = html.slice(html.indexOf('const REPAIR_COST_RULE_CACHE_SCHEMA='),html.indexOf('const $=id=>'));
const countsCode = html.slice(html.indexOf('function loadClaimTrendUnapprovedCounts()'),html.indexOf('let repairerNameRuleMapPromise'));
const analysis = version => ({meta:{cache_generated_at:version,repairer_name_rule_mapping_sha256:'map'},periods:{total:{summary:{total_tickets:12},repairers:[],states:[]}}});
function app({cache=new Map(),read=async()=>{throw Error('Unexpected read');},readJson=async()=>{throw Error('Unexpected tickets');}}={}){
  const writes=[],requests=[];
  const context=vm.createContext({console,Date,URLSearchParams,
    selectedRepairYear:2026,
    clean:v=>v==null?'':String(v).trim(),
    REMOTE_ANALYSIS_JSON:'https://example.test/fast.json',ANALYSIS_JSON:'local/fast.json',
    withTimeoutPromise:p=>p,
    window:{WarrantyPageCache:{
      getPage:async(key,version)=>cache.get(key+'|'+version),
      setPage:async(key,version,value)=>{writes.push({key,version,value});cache.set(key+'|'+version,value);return true;}
    }},
    readStaticJson:async url=>{requests.push(url);return read(url);},
    readJson,
    document:{documentElement:{dataset:{}}},
    ClaimTrendTicketMetrics:{approvedMonthly:()=>[{month:'2026-09',approvedIn:7,approvedPre:2}],unapprovedMonthly:()=>[{month:'2026-09',unapprovedClosedIn:3,unapprovedClosedPre:1}]}
  });
  vm.runInContext(cacheCode+'\n'+countsCode,context);
  return {cache,writes,requests,context,run:s=>vm.runInContext(s,context)};
}

test('matching live summary version restores cached data without a full download',async()=>{
  const data=analysis('v2'),a=app({read:async()=>data.meta});
  const version=a.run('repairAnalysisVersion') (data.meta);
  a.cache.set(a.run('REPAIR_ANALYSIS_CACHE_KEY')+'|'+version,data);
  assert.equal(await a.run('loadRepairAnalysisJson()'),data);
  assert.deepEqual(a.requests,['https://example.test/fast/meta.json']);
  assert.equal(a.run('repairAnalysisSource'),'cached');
});

test('changed versions download fresh data, keep the old cache isolated and deduplicate callers',async()=>{
  const current=analysis('v2'),old=analysis('v1');
  const a=app({read:async url=>url.endsWith('/meta.json')?current.meta:current});
  a.cache.set(a.run('REPAIR_ANALYSIS_CACHE_KEY')+'|'+a.run('repairAnalysisVersion')(old.meta),old);
  const results=await Promise.all([a.run('loadRepairAnalysisJson()'),a.run('loadRepairAnalysisJson()')]);
  assert.equal(results[0],current);assert.equal(results[1],current);
  assert.equal(a.requests.filter(x=>x==='https://example.test/fast.json').length,1);
  assert.equal(a.writes.length,1);
  assert.equal(a.run('repairAnalysisSource'),'fresh');
});

test('unverified cache and stale local fallback cannot be presented as current',async()=>{
  const current=analysis('v2'),old=analysis('v1');
  const a=app({read:async url=>{
    if(url.endsWith('/meta.json'))return current.meta;
    if(url.startsWith('local/'))return old;
    throw Error('offline');
  }});
  await assert.rejects(a.run('loadRepairAnalysisJson()'),/older than the live source/);
  assert.equal(a.writes.length,0);
  assert.equal(a.run('repairAnalysisFetchPromise'),null,'failure remains retryable');
});

test('a publication changing during the summary download is not cached',async()=>{
  let metaReads=0;
  const a=app({read:async url=>url.endsWith('/meta.json')?analysis(++metaReads===1?'v1':'v2').meta:analysis('v1')});
  await assert.rejects(a.run('loadRepairAnalysisJson()'),/changed while loading/);
  assert.equal(a.writes.length,0);
});

test('counts restore against the ticket source version without fetching raw tickets',async()=>{
  let rawReads=0;
  const a=app({readJson:async p=>{if(p.endsWith('ticketCoreSyncAt'))return 'sync-2';rawReads++;throw Error('Unexpected raw fetch');}});
  const saved={loaded:true,monthly:[{month:'2026-09',approvedIn:7,approvedPre:2}],closedMonthly:[]};
  a.cache.set(a.run('REPAIR_COUNTS_CACHE_KEY')+'|sync-2',saved);
  assert.equal(await a.run('loadClaimTrendUnapprovedCounts()'),saved);
  assert.equal(rawReads,0);
});

test('fresh counts use the unchanged shared ticket rules and persist only after matching source checks',async()=>{
  let rawReads=0;
  const a=app({readJson:async p=>{
    if(p.endsWith('ticketCoreSyncAt'))return 'sync-2';
    rawReads++;return {t1:{TicketID:'1'}};
  }});
  await Promise.all([a.run('loadClaimTrendUnapprovedCounts()'),a.run('loadClaimTrendUnapprovedCounts()')]);
  assert.equal(rawReads,1);
  assert.equal(a.writes.length,1);
  assert.equal(a.writes[0].value.monthly[0].approvedIn,7);
  assert.equal(a.writes[0].value.closedMonthly[0].unapprovedClosedIn,3);
});

test('failed counts and changing ticket versions do not become cached zero totals',async()=>{
  let versions=0;
  const a=app({readJson:async p=>p.endsWith('ticketCoreSyncAt')?`sync-${++versions}`:{t1:{}}});
  assert.equal((await a.run('loadClaimTrendUnapprovedCounts()')).loaded,false);
  assert.equal(a.writes.length,0);
  assert.equal(a.run('claimTrendCountsPromise'),null);
});

test('concurrent counts and detail consumers share one raw ticket request and retry failures',async()=>{
  let requests=0;
  const a=app({readJson:async()=>{if(++requests===1)throw Error('temporary');return {t1:{}};}});
  await assert.rejects(a.run('loadRepairRawTickets()'),/temporary/);
  const [first,second]=await Promise.all([a.run('loadRepairRawTickets()'),a.run('loadRepairRawTickets()')]);
  assert.equal(first,second);
  assert.equal(requests,2);
});

test('normalized detail cache requires matching versions of all inputs and preserves ticket fields',async()=>{
  const versions={ticketCoreSyncAt:'core',ticketRolesSyncAt:'roles',ticketSoSyncAt:'so'};
  const a=app({read:async()=>analysis('light-2').meta,readJson:async p=>versions[p.split('/').pop()]});
  let csvTag='csv-1';
  Object.assign(a.context,{
    REMOTE_ANALYSIS_DETAILS_JSON:'https://example.test/light.json',ANALYSIS_TICKET_BASE_CSV:'local/base.csv',
    fetch:async()=>({ok:true,headers:{get:k=>k==='etag'?csvTag:null}})
  });
  a.run('repairNameRuleSourceVersion="mapping-1"; let allRepairTickets=[],repairDetailsLoaded=false;');
  const initial=await a.run('readRepairDetailSourceVersion()');
  for(const key of Object.keys(versions)){
    const original=versions[key];versions[key]+='-new';
    assert.notEqual(await a.run('readRepairDetailSourceVersion()'),initial,key);
    versions[key]=original;
  }
  csvTag='csv-2';assert.notEqual(await a.run('readRepairDetailSourceVersion()'),initial);
  csvTag='csv-1';a.run('repairNameRuleSourceVersion="mapping-2"');
  assert.notEqual(await a.run('readRepairDetailSourceVersion()'),initial);
  a.run('repairNameRuleSourceVersion="mapping-1"');
  const saved=[{id:'000123',ticket:{SerialID:'000099',AmountIncludingTax:123.45},roles:{repairer:'x'}}];
  a.cache.set(a.run('REPAIR_DETAILS_CACHE_KEY')+'|'+initial,saved);
  assert.equal(await a.run('restoreRepairDetails()'),true);
  assert.equal(a.run('allRepairTickets'),saved);
  assert.equal(a.run('repairDetailsLoaded'),true);
  assert.equal(a.context.document.documentElement.dataset.repairDetailsSource,'cached');
});

test('missing detail source validators disable cache reuse',async()=>{
  const a=app({read:async()=>analysis('light-2').meta,readJson:async()=>null});
  Object.assign(a.context,{
    REMOTE_ANALYSIS_DETAILS_JSON:'https://example.test/light.json',ANALYSIS_TICKET_BASE_CSV:'local/base.csv',
    fetch:async()=>({ok:true,headers:{get:()=>null}})
  });
  a.run('repairNameRuleSourceVersion="mapping-1"');
  assert.equal(await a.run('readRepairDetailSourceVersion()'),'');
  assert.equal(await a.run('restoreRepairDetails()'),false);
});

test('name resolution is reused within a source and invalidates when naming rules change',()=>{
  const context=vm.createContext({WeakMap});
  const start=html.indexOf('const repairInfoCache=new WeakMap();');
  vm.runInContext('let repairerNameRuleMap={name:"first"}, calls=0; function resolveRepairInfo(entry){calls++;return {repairName:repairerNameRuleMap.name,id:entry.id};}\n'+html.slice(start,html.indexOf('function resolveRepairInfo(entry)',start)),context);
  vm.runInContext('const entry={id:"000123"}; repairInfo(entry); repairInfo(entry);',context);
  assert.equal(vm.runInContext('calls',context),1);
  vm.runInContext('repairerNameRuleMap={name:"updated"};',context);
  assert.equal(vm.runInContext('repairInfo(entry).repairName',context),'updated');
  assert.equal(vm.runInContext('calls',context),2);
});

test('summary renders without waiting for ticket counts, and period initialization precedes rendering',async()=>{
  const a=app();
  const events=[];let finishCounts;
  const pending=new Promise(resolve=>{finishCounts=resolve;});
  const elements=new Map();
  Object.assign(a.context,{
    refreshRepairClaimCounts:()=>{events.push('counts started');return pending;},
    loadRepairerNameRuleMap:async()=>({}),
    showCalcFloat(){},hideCalcFloat(){},showRepairYearNotice(){},renderWeekPanel(){},
    requestChassisRepeatDetailRefresh(){},
    performance:{now:()=>123},
    $:id=>{if(!elements.has(id))elements.set(id,{style:{}});return elements.get(id);},
    applySelectedRepairPeriod:()=>events.push('period'),renderAll:()=>events.push('summary'),renderAll_v2:()=>events.push('panels')
  });
  a.run('let repairInitialLoading=false, repairerNameRuleMap, precomputedRepairPeriods=null, repairDetailsLoaded=false, allRepairTickets=[],tickets=[];');
  a.run('restoreRepairDetails=async()=>false; loadRepairAnalysisJson=async()=>({}); async function loadWeeklyAnalysis(){precomputedRepairPeriods={total:{summary:{total_tickets:12}}}}');
  const start=html.indexOf('async function load(){',html.indexOf('async function ensureRepairDetailsLoaded'));
  a.run(html.slice(start,html.indexOf('\nif($("repairSelect"))',start)));
  const loaded=a.run('load()');
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(events,['counts started','period','summary','panels']);
  assert.equal(a.context.document.documentElement.dataset.repairOverviewReadyMs,'123');
  finishCounts();await loaded;
});
