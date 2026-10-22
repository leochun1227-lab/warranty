const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../repairer-overview.js'),'utf8');
const ctx=vm.createContext({});
vm.runInContext(source.slice(0,source.indexOf('(function(){')),ctx);
test('rankings filter states before taking the top N and preserve source data',()=>{
  const rows=[{repairName:'A',state:'QLD',confirmed_cost:100,ticket_count:2},{repairName:'B',state:'VIC',confirmed_cost:500,ticket_count:8},{repairName:'C',state:'QLD',confirmed_cost:200,ticket_count:1}];
  assert.equal(ctx.overviewRankRows(rows,'confirmed_cost','QLD',1)[0].repairName,'C');
  assert.equal(ctx.overviewRankRows(rows,'ticket_count','QLD',1)[0].repairName,'A');
  assert.equal(ctx.overviewRankRows(rows,'confirmed_cost','',1)[0].repairName,'B');
  assert.equal(rows[0].repairName,'A');
});
test('repeat detail selection preserves leading zeros, zero costs, scenario and shop boundaries',()=>{
  const rows=[{categoryLabel:'Partial',repairId:'r1',chassis:'C1',ticket:{ticketId:'001',created:'2026-09-03',ticketStatus:'Unapproved',approvedCost:0}},{categoryLabel:'All',repairId:'r1',ticket:{ticketId:'002',approvedCost:20}},{categoryLabel:'Partial',repairId:'r2',ticket:{ticketId:'003',approvedCost:30}}];
  const result=JSON.parse(JSON.stringify(ctx.overviewRepeatDetails(rows,'Partial','r1')));
  assert.deepEqual(result,[['001','C1','2026-09-03','Unapproved',0]]);
});
test('summary and detail IDs reconcile a state suffix without merging shops across states',()=>{
  const row={repairId:'Shop|QLD',repairName:'Shop (QLD)',state:'QLD'};
  assert.equal(ctx.overviewRepairMatches({repairId:'Shop (QLD)|QLD',repairName:'Shop (QLD)'},row),true);
  assert.equal(ctx.overviewRepairMatches({repairId:'Shop (NSW)|NSW',repairName:'Shop (NSW)'},row),false);
  assert.equal(ctx.overviewRepairMatches({repairId:'Other|QLD',repairName:'Other'},row),false);
});

test('monthly distribution keeps year and state boundaries and calculates weighted average costs',()=>{
  const periods={
    '2025-12':[{repairId:'a',repairName:'A',state:'QLD',confirmed_cost:999,ticket_count:1}],
    '2026-01':[{repairId:'a',repairName:'A',state:'QLD',confirmed_cost:100,ticket_count:1},{repairId:'b',repairName:'B',state:'VIC',confirmed_cost:1000,ticket_count:2}],
    '2026-02':[{repairId:'a',repairName:'A',state:'QLD',confirmed_cost:900,ticket_count:9},{repairId:'c',repairName:'C',state:'QLD',confirmed_cost:300,ticket_count:1}],
  };
  const result=ctx.overviewMonthlyRepairers(periods,2026,'total','avg_confirmed_cost','QLD');
  assert.equal(result.months.length,12);
  assert.equal(result.rows[0].repairId,'c');
  const a=result.rows.find(r=>r.repairId==='a');
  assert.equal(a.confirmed_cost,1000);
  assert.equal(a.ticket_count,10);
  assert.equal(a.avg_confirmed_cost,100);
  assert.equal(a.months['2026-02'].ticket_count,9);
  assert.equal(a.months['2026-03'],undefined);
  assert.equal(periods['2026-01'][0].confirmed_cost,100,'input rows remain untouched');
});

test('monthly ranking uses the selected month before limiting rows',()=>{
  const periods={
    '2026-01':[{repairId:'a',repairName:'A',state:'QLD',confirmed_cost:900,ticket_count:9}],
    '2026-02':[{repairId:'a',repairName:'A',state:'QLD',confirmed_cost:100,ticket_count:1},{repairId:'b',repairName:'B',state:'QLD',confirmed_cost:200,ticket_count:2}],
  };
  const result=ctx.overviewMonthlyRepairers(periods,2026,'2026-02','confirmed_cost','',1);
  assert.equal(result.months.join(','),'2026-02');
  assert.equal(result.rows.length,1);
  assert.equal(result.rows[0].repairId,'b');
  assert.equal(result.rows[0].confirmed_cost,200);
});
