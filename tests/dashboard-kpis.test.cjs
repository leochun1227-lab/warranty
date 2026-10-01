const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
const context=vm.createContext({
  clean:v=>v==null?'':String(v).trim(),esc:v=>String(v),
  num:v=>String(v),money:v=>'$'+Number(v).toFixed(0),
  numFixed:(v,d)=>Number(v).toFixed(d),pctText:(v,d)=>Number(v).toFixed(d)+'%'
});
vm.runInContext(html.slice(html.indexOf('function kpiDateLabel('),html.indexOf('function renderKpis(){')),context);
const markup=(current,previous,options={})=>context.kpiComparisonMarkup(current,previous,{month:'Aug',...options});

test('KPI dates use DD/MM/YYYY and September is Sep',()=>{
  assert.equal(context.kpiDateLabel('2026-10-30'),'30/10/2026');
  assert.equal(context.kpiDateLabel('2026-09-01'),'01/09/2026');
  assert.equal(context.kpiMonthLabel('2026-09-30'),'Sep');
});
test('counts and costs compare with the actual prior value',()=>{
  assert.match(markup(150,100),/\+50\.0% vs Aug/);
  assert.match(markup(750,1000,{format:'money'}),/Aug: <b>\$1000<\/b>/);
  assert.match(markup(750,1000,{format:'money'}),/-25\.0% vs Aug/);
});
test('ratio comparisons use percentage points',()=>{
  assert.match(markup(15.31,12.5,{format:'ratio'}),/\+2\.8 pp vs Aug/);
  assert.match(markup(15.31,12.5,{format:'ratio'}),/12\.50%/);
  const ratioOptions={format:'ratio',higherIsBetter:true};
  assert.match(markup(15.31,20.20,ratioOptions),/kpiChange bad/);
  assert.match(markup(15.31,20.20,ratioOptions),/-4\.9 pp vs Aug/);
  assert.match(markup(20.20,15.31,ratioOptions),/kpiChange good/);
  assert.match(markup(15.31,15.31,ratioOptions),/kpiChange flat/);
});
test('missing data never becomes a zero baseline',()=>{
  assert.match(markup(100,null),/Aug: unavailable/);
  assert.match(markup(100,undefined),/No comparison data/);
  assert.match(markup(null,100),/No comparison data/);
});
test('zero baselines never produce infinite or invented growth percentages',()=>{
  assert.match(markup(100,0),/N\/A \(zero base\)/);
  assert.match(markup(0,0),/0\.0% vs Aug/);
});
test('lower-is-better metrics color reductions green and increases red',()=>{
  assert.match(markup(90,100,{lowerIsBetter:true}),/kpiChange good/);
  assert.match(markup(110,100,{lowerIsBetter:true}),/kpiChange bad/);
  assert.match(markup(110,100,{format:'money'}),/kpiChange neutral/);
  assert.match(markup(470896,595505,{format:'money',lowerIsBetter:true}),/kpiChange good/);
  assert.match(markup(470896,595505,{format:'money',lowerIsBetter:true}),/-20\.9% vs Aug/);
});

function comparisonLoader({fail=false}={}){
  const key='2026-08-01|2026-08-31';
  const view={periodSnapshots:{[key]:{summary:{approvedAmount:595505,approvedCostTickets:896}}}};
  let reads=0;
  const ctx=vm.createContext({
    console:{warn(){}},periodBounds:()=>({start:'2026-09-01',end:'2026-09-30'}),
    previousPeriodBounds:()=>({start:'2026-08-01',end:'2026-08-31'}),earliestDateForView:()=> '2026-05-25',
    currentView:()=>view,fullTeamViewPath:()=>'/team/all',firebaseSafeKey:v=>v,
    loadTeamDetailSource:(_key,load)=>load(),
    readJson:async p=>{reads++;assert.match(p,/2026-08-01\|2026-08-31$/);if(fail)throw Error('offline');return {summary:{approvedAmount:595505,approvedCostTickets:830}};}
  });
  vm.runInContext(html.slice(html.indexOf('async function ensureKpiComparisonSnapshotDetails(){'),html.indexOf('async function ensureExportDetailsLoaded(){')),ctx);
  return {ctx,view,key,reads:()=>reads};
}
test('comparison loads the full prior-month denominator and reuses that snapshot',async()=>{
  const a=comparisonLoader();
  await a.ctx.ensureKpiComparisonSnapshotDetails();
  const summary=a.view.periodSnapshots[a.key].summary;
  assert.equal(Math.round(summary.approvedAmount/summary.approvedCostTickets),717);
  await a.ctx.ensureKpiComparisonSnapshotDetails();
  assert.equal(a.reads(),1);
});
test('unavailable prior-month details do not mark the summary complete or prevent the current dashboard',async()=>{
  const a=comparisonLoader({fail:true});
  assert.equal(await a.ctx.ensureKpiComparisonSnapshotDetails(),null);
  assert.equal(a.view.periodSnapshots[a.key].__fullExportDetailsLoaded,undefined);
});
