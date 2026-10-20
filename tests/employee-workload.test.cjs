const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
const name='Kylie Clayton';

function fixture(){
  const rows=[
    {id:'001',employee:name,status:'Waiting',created:'2026-10-18'},
    {id:'002',employee:name,status:'In Review',created:'2026-09-01'},
    {id:'003',employee:'Mark Bertoncini',status:'Waiting',created:'2026-10-19'}
  ];
  const snapshot={summary:{criticalNow:3,approvedTickets:2,unapprovedTickets:1},employeeApprovalRows:[{name,approved:2,unapproved:1}]};
  const view={trend:[{date:'2026-09-30',critical:1},{date:'2026-10-20',critical:3}],summary:snapshot.summary,
    logs:[],currentCriticalRows:rows,dailyCriticalRows:{'2026-09-30':[rows[1]],'2026-10-20':rows},
    periodSnapshots:{'2026-10-01|2026-10-20':snapshot,'2026-09-01|2026-09-30':{employeeApprovalRows:[{name,approved:4,unapproved:0}]}},
    employeeApprovalRows:snapshot.employeeApprovalRows};
  return {team:{generatedAt:'2026-10-20T07:00:00Z',minDate:'2026-09-01',views:{all:view}},employee:{views:{all:{stats:[
    {name,criticalTickets:2,removedDaily:{'2026-09-10':4,'2026-10-15':1},criticalTrendDaily:{'2026-09-30':1,'2026-10-01':1,'2026-10-20':2,'2026-10-21':9},
      dailySnapshots:{'2026-10-20':{critical:2,handled:999,approved:999}},statusCounts:{'Wrong latest status':99},durationCounts:{'60+days':99}},
    {name:'Mark Bertoncini',criticalTickets:1,criticalTrendDaily:{'2026-10-20':1}}
  ]}}}};
}

async function app(){
  const input=fixture();
  const {buildDashboardStartup}=await import('../build_dashboard_startup.mjs');
  const cached=buildDashboardStartup(input).page;
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id)){
      const classes=new Set();
      elements.set(id,{value:'',style:{},classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),contains:v=>classes.has(v)},querySelectorAll:()=>[]});
    }
    return elements.get(id);
  };
  const c=vm.createContext({console:{warn(){}},location:{hash:''},document:{getElementById:element},localStorage:{getItem:()=>null,setItem(){}},cached,input});
  c.window=c;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','dashboard-render-cache.js'),'utf8'),c);
  vm.runInContext(html.slice(html.indexOf('let calcFloatHideTimer='),html.indexOf('\nwindow.addEventListener("hashchange"')),c);
  const run=source=>vm.runInContext(source,c);
  run('team=cached.team;employeeAnalytics=cached.employeeAnalytics;employeeStatusMapping=normalizeEmployeeDirectory({});activeClaim="all";activeMonth=cached.activeMonth;activePeriod=cached.activePeriod;dashboardRenderCache.restore(cached.renderSnapshot);');
  return {c,run,element};
}

test('both workload metrics open with the chart counts from compact startup data',async()=>{
  const a=await app();
  for(const metric of ['currentCritical','totalRemoved']){
    a.run(`teamAssignMetric=${JSON.stringify(metric)}`);
    const chart=a.run('selectedEmployeeWorkloadRows().find(row=>row.name==="Kylie Clayton")');
    const detail=a.run('employeeSummary("Kylie Clayton",employeeBounds())');
    assert.equal(detail.critical,2);
    assert.equal(detail.handled,3,'selected-period decisions, not cumulative handled=999');
    assert.equal(detail.critical,chart.total);
    assert.equal(detail.handled,chart.removed);
    assert.equal(detail.approved,2);
    assert.equal(detail.unapproved,1);
  }
});

test('prior-period summary excludes later decisions and cumulative handled totals',async()=>{
  const a=await app();
  const summary=a.run('employeeSummary("Kylie Clayton",{start:"2026-09-01",end:"2026-09-30"})');
  assert.equal(summary.critical,1);
  assert.equal(summary.handled,4);
  assert.equal(summary.approved,4);
  assert.equal(summary.unapproved,0);
});

test('historical status and duration use the selected snapshot and its employee',async()=>{
  const a=await app();
  a.run('Object.assign(currentView(),{dailyCriticalRows:input.team.views.all.dailyCriticalRows,currentCriticalRows:input.team.views.all.currentCriticalRows,logs:[]});');
  const bounds='{start:"2026-09-01",end:"2026-09-30"}';
  const status=a.run(`employeeStatusRows("kylie clayton",${bounds})`);
  assert.equal(status.length,1);
  assert.equal(status[0].label,'In Review');
  assert.equal(status[0].count,1);
  const durations=a.run(`employeeDurationRows("Kylie Clayton",${bounds})`);
  assert.equal(durations.length,1);
  assert.equal(durations[0].label,'7-30days');
  assert.equal(durations[0].count,1);
  const latest=a.run('employeeStatusRows("Kylie Clayton",employeeBounds())');
  assert.equal(latest.reduce((sum,row)=>sum+row.count,0),2);
});

test('cached trend is populated and never includes dates outside the selection',async()=>{
  const a=await app();
  const rows=a.run('employeeCriticalTrendRows("Kylie Clayton",employeeBounds())');
  assert.deepEqual(Array.from(rows,row=>row.date),['2026-10-01','2026-10-20']);
  assert.equal(rows.at(-1).count,2);
});

test('modal awaits period/history details, reports failures, and retries without loading all raw tickets',async()=>{
  const a=await app(),calls=[];
  let fail=true;
  a.c.ensureFullTeamViewDetails=async()=>{calls.push('view');if(fail)throw Error('offline');};
  a.c.ensureCurrentPeriodSnapshotDetails=async()=>{calls.push('period');};
  a.c.ensureKpiComparisonSnapshotDetails=async()=>{calls.push('comparison');};
  a.c.ensureRawTicketDetailsLoaded=()=>{throw Error('unnecessary raw ticket download');};
  a.c.renderEmployeeModalWithLoading=()=>{};
  await a.run('openEmployeeModal("Kylie Clayton")');
  assert.equal(a.run('employeeModalDetailState'),'error');
  assert.deepEqual(calls,['view','period','comparison']);
  fail=false;
  await a.run('openEmployeeModal("Kylie Clayton")');
  assert.equal(a.run('employeeModalDetailState'),'ready');
  assert.equal(a.run('selectedEmployeeName'),name);
});

test('closing the dialog prevents a late request from changing a later dialog',async()=>{
  const a=await app();let finish;
  a.c.ensureFullTeamViewDetails=()=>new Promise(resolve=>{finish=resolve;});
  a.c.ensureCurrentPeriodSnapshotDetails=async()=>{};
  a.c.ensureKpiComparisonSnapshotDetails=async()=>{};
  a.c.renderEmployeeModalWithLoading=()=>{};
  const pending=a.run('openEmployeeModal("Kylie Clayton")');
  a.run('closeEmployeeModal()');finish();await pending;
  assert.equal(a.run('selectedEmployeeName'),'');
  assert.equal(a.element('employeeModalBackdrop').classList.contains('active'),false);
});
