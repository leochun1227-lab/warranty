const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
function between(a,b){return html.slice(html.indexOf(a),html.indexOf(b,html.indexOf(a)));}
test('deployed Team snapshot is available on the production hostname',async()=>{
  const requests=[],snapshot={schema:'team-startup-v1',generatedAt:'team-v1',sourceVersion:'meta-v1',page:{renderSnapshot:{values:{}},team:{views:{all:{}}}}};
  const context=vm.createContext({location:{hostname:'warranty-l9jw.onrender.com'},AbortSignal,
    fetch:async(url)=>{requests.push(url);return {ok:true,json:async()=>snapshot};}
  });
  vm.runInContext(between('async function readLocalTeamStartup(){','function claimExistsInTeam'),context);
  const record=await vm.runInContext('readLocalTeamStartup()',context);
  assert.equal(record.version,'meta-v1');
  assert.equal(record.value,snapshot.page);
  assert.deepEqual(requests,['outputs/team_dashboard_startup.json']);
});
function app({saved=new Map(),read=async()=>[],confirmed='v1'}={}){
  const requests=[],writes=[],view={};
  const context=vm.createContext({console:{warn(){}},document:{documentElement:{dataset:{}}},
    withTimeout:p=>p,teamPageVersion:async()=>confirmed,
    window:{WarrantyPageCache:{getPage:async(key,version)=>saved.get(key)?.version===version?saved.get(key).value:null,
      setLargePage:async(key,version,value)=>{writes.push({key,version,value});saved.set(key,{version,value});return true;}}},
    readJson:async p=>{requests.push(p);return read(p);},
    currentView:()=>view,fullTeamViewPath:()=>'/team/all',arrayFromMaybe:v=>Array.isArray(v)?v:Object.values(v||{}),
  });
  vm.runInContext(between('let dashboardSourceVersion=','async function teamPageVersion()'),context);
  vm.runInContext(between('async function ensureFullTeamViewDetails()','async function ensureCurrentPeriodSnapshotDetails()'),context);
  vm.runInContext('dashboardSourceVersion="v1";',context);
  return {context,view,saved,requests,writes,run:s=>vm.runInContext(s,context)};
}
test('concurrent dashboard refreshes fetch each detail source only once',async()=>{
  const a=app({read:async p=>p.endsWith('/dailyCriticalRows')?{'2026-09-10':[{id:'0001'}]}:[{id:'0001'}]});
  await Promise.all([a.run('ensureFullTeamViewDetails()'),a.run('ensureFullTeamViewDetails()')]);
  assert.equal(a.requests.length,4);
  assert.equal(a.writes.length,1);
  assert.equal(a.view.__fullExportDetailsLoaded,true);
  assert.equal(a.view.dailyCriticalRows['2026-09-10'][0].id,'0001');
  const next=app({saved:a.saved});
  await next.run('ensureFullTeamViewDetails()');
  assert.equal(next.requests.length,0);
  assert.deepEqual(next.view,a.view);
  assert.equal(next.context.document.documentElement.dataset.teamDetailsSource,'cached');
});
test('a new dashboard version cannot reuse old detail data',async()=>{
  const a=app();await a.run('ensureFullTeamViewDetails()');
  const next=app({saved:a.saved,confirmed:'v2'});next.run('dashboardSourceVersion="v2"');
  await next.run('ensureFullTeamViewDetails()');
  assert.equal(next.requests.length,4);
  assert.equal(next.writes[0].version,'v2');
});
test('failed detail downloads do not mark incomplete sources loaded and can retry',async()=>{
  let fail=true;
  const a=app({read:async p=>{if(fail&&p.endsWith('/logs'))throw Error('offline');return [];}});
  await assert.rejects(a.run('ensureFullTeamViewDetails()'),/offline/);
  assert.equal(a.view.__fullExportDetailsLoaded,undefined);
  assert.equal(a.writes.length,0);
  fail=false;await a.run('ensureFullTeamViewDetails()');
  assert.equal(a.view.__fullExportDetailsLoaded,true);
});
test('mid-download version changes cannot be persisted as current',async()=>{
  const a=app({confirmed:'v2'});
  await assert.rejects(a.run('ensureFullTeamViewDetails()'),/changed while loading/);
  assert.equal(a.writes.length,0);
  assert.equal(a.view.__fullExportDetailsLoaded,undefined);
});
test('dashboard startup verifies the version before restoring cached figures',async()=>{
  for(const version of ['same','new','']){
    const a=app(),events=[];
    Object.assign(a.context,{
      showCalcFloat(){},hideCalcFloat(){},
      teamPageVersion:async()=>version,
      MONITOR_ROOT:'/monitor',readJson:async()=> 'team-generation',
      readTeamPageCacheRecord:async()=>({version:'same',value:{team:{generatedAt:'team-generation'}}}),
      readLocalTeamStartup:async()=>null,
      restoreTeamPageState:()=>{events.push('restore');return true;},
      loadEmployeeStatusMappingRemote:async()=>{},
      renderAllWithLoading:async()=>events.push('render'),
      loadFreshTeamDashboard:async v=>events.push('fresh:'+v)
    });
    a.run(between('async function load(){','\nwindow.addEventListener("hashchange"'));
    await a.run('load()');
    assert.deepEqual(events,version==='same'?['restore','render']:['fresh:'+version]);
  }
});

test('cached dashboard checks team generation even after observing the new meta before data publication',async()=>{
  const cached={version:'old-meta',value:{team:{generatedAt:'old-team'},renderSnapshot:{}}};
  let liveTeam='old-team';
  async function visit(){
    const a=app(),events=[];
    Object.assign(a.context,{
      MONITOR_ROOT:'/monitor',showCalcFloat(){},hideCalcFloat(){},
      teamPageVersion:async()=> 'new-meta',
      readJson:async()=>liveTeam,
      readTeamPageCacheRecord:async()=>cached,readLocalTeamStartup:async()=>null,
      restoreTeamPageState:()=>true,
      renderAllWithLoading:async()=>events.push('render'),
      saveTeamPageState:version=>{cached.version=version;events.push('save');},
      loadFreshTeamDashboard:async(version,quiet)=>events.push(['fresh',version,quiet]),
    });
    a.context.window.WarrantyPageCache.showBadge=(version,mode)=>events.push(['badge',version,mode]);
    a.run(between('async function load(){','\nwindow.addEventListener("hashchange"'));
    await a.run('load()');
    return events;
  }
  const duringPublication=await visit();
  assert.equal(cached.version,'new-meta');
  assert.ok(duringPublication.includes('save'));
  liveTeam='new-team';
  const afterPublication=await visit();
  assert.deepEqual(afterPublication.at(-1),['fresh','new-meta',true]);
  assert.equal(afterPublication[0],'render','cached figures remain immediately visible');
  assert.ok(!afterPublication.includes('save'),'old figures cannot be saved as current');
  liveTeam='';
  const offline=await visit();
  assert.deepEqual(offline.at(-1),['badge','old-team','offline']);
  assert.ok(!offline.includes('save'));
});
