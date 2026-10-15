const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'..');
const ctx=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root,'claim-trend-ticket-metrics.js'),'utf8'),ctx);
const api=ctx.ClaimTrendTicketMetrics;
const ticket=(id,code,extra={})=>({id,statusCode:code,created:'2026-09-09',claimType:'Pre Delivery Warranty Claims',...extra});
const plain=value=>JSON.parse(JSON.stringify(value));
test('Pending excludes approved, closed, rejected and cancelled Claims',()=>{
  for(const code of ['Z1','Z2','Z3','Z4','Z5','Z6','ZR','ZM','ZV','Y6','YA','YC'])assert.equal(api.isPending(ticket('1',code)),true,code);
  for(const code of ['Z8','Z9','Y0','Y1','Y2','Y4','Y5','Y7','Y8','YB','YD','','UNKNOWN'])assert.equal(api.isPending(ticket('1',code)),false,code);
  for(const extra of [{claimApprovedOn:'2026-10-01'},{resolvedOn:'2026-10-01'},{statusText:'Rejected'},{statusText:'Cancelled'},{statusText:'Closed'}])assert.equal(api.isPending(ticket('1','Z1',extra)),false);
});
test('Warranty Pending counts both claim types, deduplicates, excludes PDI and keeps its full date scope',()=>{
  const rows=[ticket('a','Z1',{created:'2025-01-01'}),ticket('b','ZR',{claimType:'In Field Warranty Claims'}),ticket('b','ZR'),ticket('c','Z2',{claimType:'PDI',TicketType:'Z010'}),ticket('d','Y8')];
  assert.deepEqual(plain(api.pendingMonthly(rows)),[{month:'2025-01',pendingIn:0,pendingPre:1},{month:'2026-09',pendingIn:1,pendingPre:0}]);
  rows[0].statusCode='Z9';rows[1].statusCode='Y8';rows[2].statusCode='Y8';
  assert.deepEqual(plain(api.pendingMonthly(rows)),[]);
});
test('wrapped raw tickets and compact currentStatus records have the same Pending counts',()=>{
  const raw={ticket:{TicketID:'a',TicketStatus:'Z1',CreatedOn:'09/09/2026',TicketTypeText:'Pre Delivery Warranty Claims'}};
  assert.deepEqual(plain(api.pendingMonthly([raw])),plain(api.pendingMonthly([ticket('a','Z1')])));
});
const html=fs.readFileSync(path.join(root,'infieldpredelivery.html'),'utf8');
test('Pending quote amounts deduplicate tickets, retain zero/refunds and never use SAP or approved amounts',()=>{
  const tickets=[ticket('a','Z1'),ticket('a','Z1'),ticket('b','Z2'),ticket('c','ZR'),ticket('d','Z1'),ticket('e','Z1'),ticket('f','Z8'),ticket('g','Z1',{claimType:'In Field Warranty Claims'})];
  const quotes=[{id:'a',quotedClaimAmount:'1,200.25',AmountIncludingTax:'9000',approvedAmount:8000},
    {id:'b',quotedClaimAmount:'0'},{id:'c',ClaimTotalAmount:'-20.10'},
    {id:'d',quotedClaimAmount:'',AmountIncludingTax:'400'},
    {id:'e',amount:999,approvedAmount:999},{id:'f',ClaimTotalAmount:'800'},
    {id:'g',AmountIncludingTax:'35.40'}];
  assert.deepEqual(plain(api.pendingAmountMonthly(tickets,quotes)),[{month:'2026-09',amountIn:35.4,amountPre:1180.15,knownIn:1,knownPre:3,missingIn:0,missingPre:2}]);
  tickets.forEach(row=>row.statusCode='Y8');
  assert.deepEqual(plain(api.pendingAmountMonthly(tickets,quotes)),[]);
});
test('Pending amounts treat missing and invalid quote fields as unavailable, not zero',()=>{
  const tickets=[ticket('a','Z1',{quotedClaimAmount:'invalid'}),ticket('b','Z1',{quotedClaimAmount:null}),ticket('c','Z1',{quotedClaimAmount:'0'})];
  const row=api.pendingAmountMonthly(tickets)[0];
  assert.equal(row.amountPre,0);assert.equal(row.knownPre,1);assert.equal(row.missingPre,2);
});
const loader=html.slice(html.indexOf('let pendingState='),html.indexOf('function renderPendingApproved('));
test('Pending load checks source generation and reports failure without false zero counts',async()=>{
  let calls=0,renders=0;
  const scope=vm.createContext({ROOT:'warranty',ClaimTrendTicketMetrics:api,console:{warn(){}},readJson:async p=>p.endsWith('currentStatus')?[ticket('a','Z1')]:++calls===1?'v1':'v2',renderWithPageLoading(){renders++;}});
  vm.runInContext(loader+'\nloadPendingTickets();',scope);
  await new Promise(r=>setImmediate(r));
  assert.equal(vm.runInContext('pendingState.rows',scope),null);
  assert.equal(vm.runInContext('pendingState.error',scope),true);
  assert.equal(renders,1);
});
test('Warranty chart totals use the selected months and Quality view does not start the loader',()=>{
  const code=html.slice(html.indexOf('function renderPendingApproved('),html.indexOf('function drawSingleLineChart('));
  let loads=0,split;
  const totals={};
  const elements=Object.fromEntries(['approvedChart','approvedLegend','approvedSplitChart','approvedSplitLegend','approvedTotalsSplit','toggleInFieldPending'].map(id=>[id,{setAttribute(key,value){this[key]=value;}}]));
  const scope=vm.createContext({document:{body:{classList:{contains:()=>false}}},pendingState:{rows:[{month:'2026-09',pendingIn:4,pendingPre:2},{month:'2026-10',pendingIn:1,pendingPre:3}]},loadPendingTickets(){loads++;},drawSingleLineChart(){},drawChart(id,rows,inKey,preKey,opts){split={rows,inKey,preKey,opts};},rowsForRange:()=>[{month:'2026-10',approvedCreatedIn:6,approvedCreatedPre:2,approvedIn:7,approvedPre:3}],approvedDateBasis:'created',$:id=>elements[id],approvedDateLabel:()=> 'Created On',setLegend(){},setTotals(id,items){totals[id]=items;},num:String,sumValueRows:(rows,key='total')=>rows.reduce((n,r)=>n+r[key],0)});
  vm.runInContext(code,scope);
  scope.showInFieldPending=false;
  scope.renderWithPageLoading=()=>scope.renderPendingApproved([{month:'2026-10',total:8}]);
  scope.renderPendingApproved([{month:'2026-10',total:8}]);
  assert.equal(totals.approvedTotalsAll[1].value,'4');assert.equal(loads,1);
  assert.deepEqual(plain(totals.approvedTotalsSplit.map(r=>r.value)),['6','2','1','3']);
  assert.equal(split.rows[0].pendingIn+split.rows[0].pendingPre,4);
  assert.deepEqual(plain(split.opts.extraSeries.map(s=>s.key)),['pendingPre']);
  assert.equal(elements.toggleInFieldPending['aria-pressed'],'false');
  elements.toggleInFieldPending.onclick();
  assert.deepEqual(plain(split.opts.extraSeries.map(s=>s.key)),['pendingIn','pendingPre']);
  assert.equal(elements.toggleInFieldPending['aria-pressed'],'true');
  elements.toggleInFieldPending.onclick();
  assert.deepEqual(plain(split.opts.extraSeries.map(s=>s.key)),['pendingPre']);
  scope.approvedDateBasis='approved';scope.renderPendingApproved([{month:'2026-10',total:10}]);
  assert.equal(split.inKey,'approvedIn');assert.equal(split.preKey,'approvedPre');
  assert.deepEqual(plain(totals.approvedTotalsSplit.map(r=>r.value)),['7','3','1','3']);
  scope.document.body.classList.contains=()=>true;
  scope.renderPendingApproved([]);assert.equal(loads,4);
});
test('Pending amount chart marks partial totals and leaves all-missing monthly values as gaps',()=>{
  const code=html.slice(html.indexOf('function renderPendingAmounts('),html.indexOf('function drawSingleLineChart('));
  const elements=Object.fromEntries(['toggleAmountInFieldPending','amountLegend','amountPendingNote','approvedAmountChart','approvedAmountSplitChart'].map(id=>[id,{setAttribute(key,value){this[key]=value;}}]));
  let allRows,splitOptions;const totals={};
  const scope=vm.createContext({document:{body:{classList:{contains:()=>false}}},showAmountInFieldPending:false,
    pendingAmountState:{rows:[{month:'2026-09',amountIn:20,amountPre:0,knownIn:1,knownPre:0,missingIn:1,missingPre:2}]},pendingState:{},
    $:id=>elements[id],loadPendingAmounts(){},rowsForRange:()=>[{month:'2026-09',approvedAmountIn:200,approvedAmountPre:100}],amountDateBasis:'approved',
    sumValueRows:(rows,key='total')=>rows.reduce((sum,row)=>sum+Number(row[key]||0),0),moneyNum:v=>`$${v}`,moneyK:v=>`$${v}`,num:String,niceCurrencyMax:v=>v,
    drawSingleLineChart(id,rows){allRows=rows;},drawChart(id,rows,a,b,opts){splitOptions=opts;assert.equal(rows[0].pendingAmountPre,null);},setLegend(){},setTotals(id,items){totals[id]=items;}});
  vm.runInContext(code,scope);scope.renderPendingAmounts([{month:'2026-09',total:300}]);
  assert.equal(allRows[0].pendingAmount,20);
  assert.equal(totals.amountTotalsAll[1].value,'$20 *');
  assert.equal(totals.amountTotalsSplit[3].value,'—');
  assert.match(elements.amountPendingNote.textContent,/3 Pending Claims/);
  assert.deepEqual(plain(splitOptions.extraSeries.map(series=>series.key)),['pendingAmountPre']);
});
