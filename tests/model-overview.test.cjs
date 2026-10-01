const test=require('node:test');
const assert=require('node:assert/strict');
require('../model-overview.js');
const {rankedRows,failureRows,failureSegments}=globalThis.ModelOverview;
test('model ranking excludes synthetic adjustments and sorts each metric without changing source rows',()=>{
  const rows=[{series:'A',cost:200,tickets:3,pgiMatchedVehicles:2,costPerTicket:100},
    {series:'B',cost:300,tickets:2,pgiMatchedVehicles:1,costPerTicket:150},
    {series:'C',cost:0,tickets:1,pgiMatchedVehicles:0,costPerTicket:null},
    {series:'Historical Gap',cost:900,tickets:900}];
  assert.deepEqual(rankedRows(rows,'cost').map(r=>r.series),['B','A','C']);
  assert.deepEqual(rankedRows(rows,'tickets').map(r=>r.series),['A','B','C']);
  assert.deepEqual(rankedRows(rows,'vehicles').map(r=>r.series),['A','B','C']);
  assert.deepEqual(rankedRows(rows,'average').map(r=>r.series),['B','A','C']);
  assert.equal(rows[0].series,'A');assert.equal(rows.length,4);
});
test('early repair count uses linear proportions and share retains its repaired-vehicle denominator',()=>{
  const rows=failureRows([{series:'A',pgiMatchedVehicles:1000,buckets:[200,287,500,13,0]},
    {series:'B',pgiMatchedVehicles:6,buckets:[1,3,2,0,0]},
    {series:'Historical Gap',pgiMatchedVehicles:999,buckets:[999,0]}]);
  assert.equal(rows.length,2);assert.equal(rows[0].count,487);
  const a=failureSegments(rows[0],'count',487),b=failureSegments(rows[1],'count',487);
  assert.equal(a.early+a.mid,100);
  assert.ok(Math.abs((b.early+b.mid)/100-4/487)<1e-10);
  const share=failureSegments(rows[1],'share',487);
  assert.ok(Math.abs(share.early+share.mid-4/6*100)<1e-10);
  assert.equal(failureRows([{series:'A'},{series:'B'}],'B')[0].series,'B');
  assert.deepEqual(failureSegments({early:0,mid:0,dated:0},'share',1),{early:0,mid:0});
});
