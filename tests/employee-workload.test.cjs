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

async function app(input=fixture()){
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

test('monthly approvals retain history when the dashboard month changes and average over the directory',async()=>{
  const input=fixture();
  input.team.views.all.periodSnapshots['2026-08-01|2026-08-31']={employeeApprovalRows:[{name,approved:2}]};
  input.team.views.all.periodSnapshots['2026-07-01|2026-07-31']={};
  const a=await app(input);
  const before=a.run('employeeMonthlyApprovalData()');
  const kylie=before.rows.find(row=>row.name===name);
  assert.equal(kylie.values['2026-10'],2);
  assert.equal(kylie.values['2026-09'],4);
  assert.equal(kylie.values['2026-07'],null,'missing history is not zero');
  assert.deepEqual(Array.from(before.columns,col=>col.month),['2026-07','2026-08','2026-09','2026-10']);
  assert.equal(before.headcount,6,'zero-approval active employees count in the denominator');
  assert.equal(before.columns.at(-1).total,2,'exclude unapproved decisions');
  assert.equal(before.columns.at(-1).average,2/6);
  a.run('activeMonth="2026-09"');
  assert.equal(JSON.stringify(a.run('employeeMonthlyApprovalData()')),JSON.stringify(before));
  assert.equal(kylie.change.text,'+100.0%','September full month vs August, not October MTD vs September');
  assert.equal(before.totals.change.text,'+100.0%');
  assert.equal(before.average.change.text,'+100.0%');
  assert.equal(before.rows.find(row=>row.name==='Chloe Bolger').change.text,'0.0%');
  assert.equal(a.run('typeof employeeMonthlyChange'),'undefined','business calculations are absent from the browser');
  a.run('currentView().periodSnapshots={};currentCriticalStatusRows=()=>{throw Error("browser must not aggregate");};');
  assert.equal(JSON.stringify(a.run('employeeMonthlyApprovalData()')),JSON.stringify(before));
  const exported=a.run('employeeMonthlyApprovalExportRows()');
  assert.equal(exported.find(row=>row[0]===name).at(-1),'+100.0%');
  assert.equal(exported[0].at(-1),'2026-09 vs 2026-08 % (full months)');
  assert.equal(exported[1][0],'Team total');
  assert.match(exported[2][0],/^Average \/ employee/);
  const box={},legend={};a.c.monthlyBox=box;a.c.monthlyLegend=legend;
  a.run('renderEmployeeMonthlyApprovals(monthlyBox,monthlyLegend)');
  assert.ok(box.innerHTML.indexOf('Team total')<box.innerHTML.indexOf('Average / employee'));
  assert.ok(box.innerHTML.indexOf('Average / employee')<box.innerHTML.indexOf('Chloe Bolger'));
  assert.match(box.innerHTML,/2026-09 vs 2026-08/);
  assert.doesNotMatch(box.innerHTML,/<tfoot>/);
});

test('monthly columns cross year boundaries and keep Other out of headcount',async()=>{
  const input=fixture();
  input.team.views.all.trend=[{date:'2027-01-12'}];
  input.team.views.all.periodSnapshots['2027-01-01|2027-01-12']={employeeApprovalRows:[{name:'Kylie Clayton',approved:6},{name:'Leanne Pulford',approved:3}]};
  input.team.views.all.periodSnapshots['2026-12-01|2026-12-31']={employeeApprovalRows:[{name,approved:4}]};
  input.team.views.all.periodSnapshots['2026-11-01|2026-11-30']={employeeApprovalRows:[{name,approved:2}]};
  const a=await app(input);
  const data=a.run('employeeMonthlyApprovalData()');
  assert.equal(data.current,'2027-01');
  assert.equal(data.previous,'2026-12');
  assert.equal(data.prior,'2026-11');
  assert.equal(data.columns.at(-1).total,9);
  assert.equal(data.columns.at(-1).average,1.5);
  assert.equal(data.rows.find(row=>row.name===name).change.text,'+100.0%');
  assert.equal(data.rows.find(row=>row.name==='Other').values['2027-01'],3);
});

test('status columns show each state with proportional heights and accessible ticket links',async()=>{
  const a=await app();
  const markup=a.run(`statusColumnChartMarkup(publishedDashboardPanels().remainingStatus['2026-10'].rows)`);
  assert.match(markup,/height:100%/);
  assert.match(markup,/height:50%/);
  assert.match(markup,/data-status-group="1"/);
  assert.match(markup,/aria-label="In Review: 1 tickets/);
  assert.doesNotMatch(markup,/<svg/);
  assert.match(a.run('statusColumnChartMarkup([])'),/No tickets in progress/);
  assert.match(a.run('statusColumnChartMarkup(null)'),/awaiting a data refresh/);
});

test('repair cost columns use published amounts, percentages and bucket links without browser aggregation',async()=>{
  const input=fixture();
  input.team.views.all.periodSnapshots['2026-10-01|2026-10-20'].repairCostDistribution=[
    {key:'0',label:'$0',count:2,total:0},
    {key:'100-500',label:'$100 - $500',count:3,total:600},
    {key:'2500-inf',label:'>$2.5k',count:1,total:3000}
  ];
  const a=await app(input);
  const data=a.run('publishedDashboardPanels().repairCostDistribution["2026-10"]');
  assert.equal(data.total,3600);
  assert.equal(data.rows[1].heightPct,20);
  assert.equal(data.rows[2].percent,3000/3600*100);
  assert.equal(data.rows[0].key,'0-0.01');
  assert.equal(data.rows[2].key,'2500-Infinity');
  a.run('repairCostDistributionRows=()=>{throw Error("browser must not aggregate");};renderRepairCostDistribution()');
  const markup=a.element('criticalLogRows').innerHTML;
  assert.match(markup,/height:20%/);
  assert.match(markup,/data-repair-cost-key="2500-Infinity"/);
  assert.match(markup,/3 tickets/);
  assert.doesNotMatch(markup,/<svg/);
  assert.match(a.run('repairCostColumnChartMarkup(null)'),/awaiting a data refresh/);
  assert.match(a.run('repairCostColumnChartMarkup({rows:[]})'),/No repair cost data/);
});

test('all categories and months have server-calculated status percentages and monthly comparisons',async()=>{
  const input=fixture();
  input.team.views.inField=structuredClone(input.team.views.all);
  input.team.views.inField.periodSnapshots['2026-08-01|2026-08-31']={employeeApprovalRows:[{name,approved:0}]};
  input.team.views.preDelivery=structuredClone(input.team.views.all);
  delete input.team.views.preDelivery.periodSnapshots['2026-09-01|2026-09-30'];
  const {buildDashboardStartup}=await import('../build_dashboard_startup.mjs');
  const result=buildDashboardStartup(input).page.team.dashboardPanelCache;
  assert.equal(result.generatedAt,input.team.generatedAt);
  assert.deepEqual(Object.keys(result.views),['all','inField','preDelivery']);
  assert.equal(result.views.inField.monthlyApprovals.rows.find(row=>row.name===name).change.text,'N/A');
  assert.equal(result.views.preDelivery.monthlyApprovals.rows.find(row=>row.name===name).change.text,'—');
  const status=result.views.all.remainingStatus;
  assert.equal(status['2026-09'].rows[0].count,1);
  assert.equal(status['2026-10'].rows[0].count,2);
  assert.equal(status['2026-10'].rows[0].percent,2/3*100);
  assert.equal(status['2026-10'].rows[1].heightPct,50);
});

test('legacy startup hydrates the published cache once and refuses another source generation',async()=>{
  const a=await app();
  const cache=a.run('team.dashboardPanelCache');
  a.run('team.dashboardPanelCache={...team.dashboardPanelCache,schema:1}');
  assert.equal(a.run('employeeMonthlyApprovalData()'),null,'old MTD comparison cache is never shown');
  let requests=0;
  a.c.readJson=async path=>{requests++;assert.match(path,/teamPanelCacheV2$/);return {schema:2,data:JSON.stringify(cache)};};
  await a.run('ensureDashboardPanelCache()');
  await a.run('ensureDashboardPanelCache()');
  assert.equal(requests,1);
  assert.equal(a.run('employeeMonthlyApprovalData().current'),'2026-10');
  a.run('team.generatedAt="new-source";delete team.dashboardPanelCache');
  await a.run('ensureDashboardPanelCache()');
  assert.equal(a.run('employeeMonthlyApprovalData()'),null);
  assert.match(a.run('employeeMonthlyApprovalExportRows()[0][0]'),/cache unavailable/);
  assert.doesNotMatch(html,/<script[^>]*src=["'][^"']*dashboard-panel-calculations/);
});
