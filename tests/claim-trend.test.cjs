const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'infieldpredelivery.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const definitions = script.slice(0, script.indexOf('\nsetupNav();'));
function app() {
  const elements = new Map();
  const document = {documentElement:{dataset:{}}, getElementById(id) {
    if (!elements.has(id)) elements.set(id, {classList: {toggle() {}}, setAttribute() {}, style: {}});
    return elements.get(id);
  }};
  const context = vm.createContext({document,window:{}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'claim-trend-ticket-metrics.js'), 'utf8'), context);
  vm.runInContext(definitions, context);
  return {context, run: code => vm.runInContext(code, context), json: code => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context))};
}
function ticket(overrides = {}) {
  return {TicketID: '1', TicketTypeText: 'In Field Warranty', TicketStatus: 'Y2',
    CreatedOn: '31/01/2026', ClaimApprovedOnDateTime: '2026-03-01 09:00:00',
    ChassisNumber: '000123', ChangeOnDateTime: '2026-03-02 08:00:00', ...overrides};
}

test('creation grouping includes approved closed tickets and excludes PDI, unapproved and missing approval dates', () => {
  const a = app();
  a.context.tickets = [ticket(), {ticket: ticket()}, ticket({TicketID:'2',TicketTypeText:'Pre Delivery',CreatedOn:'2026-02-01'}),
    ticket({TicketID:'3',TicketStatus:'Y7'}), ticket({TicketID:'4',TicketType:'Z010'}),
    ticket({TicketID:'5',ClaimApprovedOnDateTime:''}), ticket({TicketID:'6',TicketStatus:'Z1'}),
    ticket({TicketID:'7',TicketTypeText:'Recall Claims'}), ticket({TicketID:'8',CreatedOn:'31/12/2025'})];
  a.run('RAW_TICKETS=tickets; MONTHLY=buildMonthly({}); MONTHS_INDEX=Object.fromEntries(MONTHLY.map(r=>[r.month,r]));');
  assert.equal(a.run('MONTHS_INDEX["2026-01"].approvedCreatedIn'), 2);
  assert.equal(a.run('MONTHS_INDEX["2026-02"].approvedCreatedPre'), 1);
  assert.equal(a.run('MONTHS_INDEX["2025-12"].approvedCreatedIn'), 1);
  assert.equal(a.run('MONTHS_INDEX["2026-01"].approvedIn'), 0, 'historical approval constants cannot override ticket details');
  assert.equal(a.run('MONTHS_INDEX["2026-03"].approvedIn'), 3);
  assert.equal(a.run('withApprovedMonthly(MONTHLY).find(r=>r.month==="2026-01").approvedCreatedIn'), 2, 'repeated toggles do not accumulate');
  assert.equal(a.json('totalCompareRows("approvedCreatedIn","approvedCreatedPre","2026","2025")')[0].firstValue, 2);
});

test('closed approved claims remain counted on closure day without changing the existing amount scope', () => {
  const a=app();
  const active=ticket({TicketTypeText:'Pre Delivery',ClaimApprovedOnDateTime:'2026-01-10 09:00:00',approvedAmount:25});
  a.context.tickets=Array.from({length:100},(_,i)=>({...active,TicketID:String(i+1),TicketStatus:i<94?'Y2':'Y7'}));
  a.run('RAW_TICKETS=tickets; MONTHLY=buildMonthly({});');
  assert.equal(a.run('MONTHLY.find(r=>r.month==="2026-01").approvedPre'),100);
  assert.equal(a.run('MONTHLY.find(r=>r.month==="2026-01").approvedCreatedPre'),100);
  assert.equal(a.run('MONTHLY.find(r=>r.month==="2026-01").approvedAmountPre'),94*25);
  assert.equal(a.run('buildExportRowsForRange("2026-01","2026-01").filter(r=>r.approvedFlag==="Y").length'),100);
  assert.equal(a.run('buildExportRowsForRange("2026-01","2026-01").filter(r=>r.amountFlag==="Y").length'),94);
  a.context.closed=ticket({TicketStatus:'',TicketStatusText:'Approved Claims Closed (Closed)'});
  assert.equal(a.run('ClaimTrendTicketMetrics.isApproved(closed)'),true);
  a.context.closed.ClaimApprovedOnDateTime='';
  assert.equal(a.run('ClaimTrendTicketMetrics.isApproved(closed)'),false);
  a.context.closed=ticket({TicketStatus:'Y8',TicketStatusText:'Unapproved Claims Closed (Closed)'});
  assert.equal(a.run('ClaimTrendTicketMetrics.isApproved(closed)'),false);
  a.context.tickets[0].TicketStatus='Y7';
  a.run('RAW_TICKETS=tickets; MONTHLY=withApprovedMonthly(MONTHLY);');
  assert.equal(a.run('MONTHLY.find(r=>r.month==="2026-01").approvedPre'),100,'daily closure must not remove a previous approval');
});

test('historical Created counts come from exportable details and clear when details change', () => {
  const a=app();
  a.context.tickets=[ticket({CreatedOn:'01/12/2025'}),ticket({TicketID:'2',CreatedOn:'01/12/2025',TicketTypeText:'Pre Delivery'})];
  a.run('RAW_TICKETS=tickets; MONTHLY=buildMonthly({claimCreatedMonthly:[{month:"2025-12",inField:999,preDelivery:999}]});');
  assert.equal(a.run('MONTHLY.find(r=>r.month==="2025-12").createdIn'),1);
  assert.equal(a.run('MONTHLY.find(r=>r.month==="2025-12").createdPre'),1);
  assert.equal(a.run('buildExportRowsForRange("2025-12","2025-12").filter(r=>r.createdFlag==="Y").length'),2);
  a.run('RAW_TICKETS=[]; MONTHLY=buildMonthly({claimCreatedMonthly:[{month:"2025-12",inField:999}]});');
  assert.equal(a.run('MONTHLY.find(r=>r.month==="2025-12")?.createdIn||0'),0);
});

test('Repairer screen and exported repeat rate use the same current ticket denominator', () => {
  const a=app();
  const page=fs.readFileSync(path.join(__dirname,'..','repairs.html'),'utf8');
  a.run(page.match(/^function chassisRepeatRequestRate\([^]*?^}/m)[0]);
  a.run('let workingChassis={repeatedTicketCount:20,repeatRate:99}; let denominator=100; function chassisRepeatTotalTicketDenominator(){return denominator}');
  assert.equal(a.run('chassisRepeatRequestRate()'),20);
  a.run('denominator=0');
  assert.equal(a.run('chassisRepeatRequestRate()'),0);
  assert.match(page,/const repeatedProcessRate=chassisRepeatRequestRate\(w\)/);
  assert.match(page,/\["Repeat Repair Rate %",chassisRepeatRequestRate\(w\)\]/);
});

test('export approval flag follows selected month while the amount flag stays on approval month', () => {
  const a = app();
  a.context.ticket = ticket({approvedAmount:100});
  assert.equal(a.run('exportTicketMatchFlags(ticket,"2026-01","2026-01").approved'), false);
  a.run('approvedDateBasis="created"');
  const jan = a.json('exportTicketMatchFlags(ticket,"2026-01","2026-01")');
  assert.equal(jan.approved, true);
  assert.equal(jan.amountClaim, false);
  assert.equal(jan.approvedGroupMonth, '2026-01');
  assert.equal(jan.approvedMonth, '2026-03');
  const march = a.json('exportTicketMatchFlags(ticket,"2026-03","2026-03")');
  assert.equal(march.approved, false);
  assert.equal(march.amountClaim, true);
  a.run('approvedDateBasis="approved"');
  assert.equal(a.run('exportTicketMatchFlags(ticket,"2026-03","2026-03").approved'), true);
});

test('export keeps string chassis IDs and real zeros, reads API modification date and drops entirely empty columns', () => {
  const a = app();
  a.context.tickets = [ticket(), ticket({TicketID:'2', ChassisNumber:'', SerialID:'not-a-chassis', approvedAmount:0})];
  a.run('RAW_TICKETS=tickets; approvedDateBasis="created"; const exported=buildExportRowsForRange("2026-01","2026-12");');
  const rows = a.json('exportDetailSheetRows(exported)');
  const header = rows[0];
  assert.equal(rows[1][header.indexOf('ChassisNumber')], '000123');
  assert.equal(rows[2][header.indexOf('ChassisNumber')], '');
  assert.equal(rows[1][header.indexOf('Changed On')], '2026-03-02 08:00:00');
  assert.equal(rows[1][header.indexOf('Approved Amount')], '');
  assert.equal(rows[2][header.indexOf('Approved Amount')], 0);
  for (const name of ['Posting Date','Sales Order','PO Price (SAP EKPO.NETWR)','PO Price Source']) assert.ok(!header.includes(name), name);
  for (const name of ['Status Code','Claim Type','Created Ticket','Approved Ticket','Amount Claim Ticket','Unapproved Ticket','Created Month','Approved Month','Approved Group Month','Unapproved Month','Unapproved Group Month']) assert.ok(!header.includes(name), name);
  assert.equal(rows[1][header.indexOf('Matches Metrics')], 'Created, Approved');
  assert.match(a.run('buildWorkbookXml([{name:"Tickets",rows:exportDetailSheetRows(exported)}])'), /ss:Type="String">000123</);
  for (const row of rows) assert.equal(row.length, header.length);
  assert.deepEqual(a.json('exportDetailSheetRows([])'), [['Ticket ID','ChassisNumber']]);
  for (const summary of ['summarySheetRowsSingle(exported)', 'summarySheetRowsCompare(exported,[])']) {
    const [head, data] = a.json(summary);
    assert.equal(data[head.indexOf('Approved Tickets Group By')], 'Created On');
    assert.equal(head.length, data.length);
  }
});

test('failed raw-data load leaves approval mode selected and supports retry', async () => {
  const a = app();
  a.run('readJson=async()=>{throw new Error("offline")}; renderWithPageLoading=()=>{};');
  await a.run('setApprovedDateBasis("created")');
  assert.equal(a.run('approvedDateBasis'), 'approved');
  assert.equal(a.run('rawTicketsLoaded'), false);
  assert.match(a.run('$("error").textContent'), /offline/);
  a.context.tickets = [ticket()];
  a.run('readJson=async()=>tickets;');
  await a.run('setApprovedDateBasis("created")');
  assert.equal(a.run('approvedDateBasis'), 'created');
  assert.equal(a.run('MONTHS_INDEX["2026-01"].approvedCreatedIn'), 1);
  await a.run('setApprovedDateBasis("approved")');
  assert.equal(a.run('approvedDateBasis'), 'approved');
});

test('unapproved groups In Field and Pre Delivery by creation or actual closure, with matching export flags', async () => {
  const a = app();
  a.context.tickets = [
    ticket({TicketStatus:'Y8',ResolvedOnDateTime:'2026-03-15 12:00:00'}),
    ticket({TicketStatus:'Y8',ResolvedOnDateTime:'2026-03-15 12:00:00'}),
    ticket({TicketID:'2',TicketStatus:'Y8',TicketTypeText:'Pre Delivery',CreatedOn:'01/02/2026',ResolvedOnDateTime:'2026-04-01 09:00:00'}),
    ticket({TicketID:'3',TicketStatus:'Y8',ResolvedOnDateTime:'2026-03-01 09:00:00',LastModifiedUser:'Admin ABA'}),
    ticket({TicketID:'4',TicketStatus:'Y7',ResolvedOnDateTime:'2026-03-01 09:00:00'}),
    ticket({TicketID:'5',TicketStatus:'Y8',TicketType:'Z010',ResolvedOnDateTime:'2026-03-01 09:00:00'})
  ];
  a.run('RAW_TICKETS=tickets; rawTicketsLoaded=true; MONTHLY=buildMonthly({}); MONTHS_INDEX=Object.fromEntries(MONTHLY.map(r=>[r.month,r])); renderWithPageLoading=()=>{};');
  assert.equal(a.run('MONTHS_INDEX["2026-01"].unapprovedCreatedIn'),1);
  assert.equal(a.run('MONTHS_INDEX["2026-02"].unapprovedCreatedPre'),1);
  assert.equal(a.run('MONTHS_INDEX["2026-03"].unapprovedClosedIn'),1);
  assert.equal(a.run('MONTHS_INDEX["2026-04"].unapprovedClosedPre'),1);
  assert.equal(a.run('exportTicketMatchFlags(tickets[0],"2026-01","2026-01").unapproved'),false);
  await a.run('setUnapprovedDateBasis("created")');
  assert.equal(a.run('exportTicketMatchFlags(tickets[0],"2026-01","2026-01").unapproved'),true);
  assert.equal(a.run('exportTicketMatchFlags(tickets[2],"2026-02","2026-02").unapproved'),true);
  assert.equal(a.run('approvedDateBasis'),'approved','unapproved switch is independent');
  const rows = a.json('exportDetailSheetRows(buildExportRowsForRange("2026-01","2026-12"))');
  const first = rows.slice(1).find(row=>row[0]==='1');
  assert.equal(first[rows[0].indexOf('Resolved On')],'2026-03-15 12:00:00');
  assert.equal(first[rows[0].indexOf('Created On')],'31/01/2026');
  assert.match(first[rows[0].indexOf('Matches Metrics')], /Unapproved/);
  const [header,data] = a.json('summarySheetRowsSingle([])');
  assert.equal(data[header.indexOf('Unapproved Tickets Group By')],'Created On');
  await a.run('setUnapprovedDateBasis("closed")');
  assert.equal(a.run('exportTicketMatchFlags(tickets[2],"2026-04","2026-04").unapproved'),true);
  assert.equal(a.run('MONTHS_INDEX["2026-04"].unapprovedClosedPre'),1);
  assert.equal(a.json('totalCompareRows("unapprovedClosedIn","unapprovedClosedPre","2026","2025")')[3].firstValue,1);
});

test('missing closure date never falls back to last modification time', () => {
  const a = app();
  a.context.t = ticket({TicketStatus:'Y8',ClaimApprovedOnDateTime:'',ChangeOnDateTime:'2026-05-01 10:00:00'});
  a.run('RAW_TICKETS=[t]; const grouped=withUnapprovedMonthly([]);');
  assert.equal(a.run('grouped.reduce((sum,r)=>sum+r.unapprovedClosedIn,0)'),0);
  assert.equal(a.run('exportTicketMatchFlags(t,"2026-05","2026-05")'),null);
  a.run('unapprovedDateBasis="created"');
  assert.equal(a.run('exportTicketMatchFlags(t,"2026-01","2026-01").unapproved'),true);
});

test('landing range defaults to this year even when previous years are available', () => {
  const a = app();
  const year = String(new Date().getFullYear());
  a.context.bounds = {min:'2025-01',max:`${year}-09`,years:['2025',year]};
  assert.equal(a.run('preferredDefaultYear(bounds)'),year);
  assert.deepEqual(a.json('defaultSingleRange(bounds)'),{start:`${year}-01`,end:`${year}-09`,isMultiYear:false});
});

test('unapproved date switch keeps selection on load failure and can retry', async () => {
  const a = app();
  a.run('readJson=async()=>{throw new Error("offline")}; renderWithPageLoading=()=>{};');
  await a.run('setUnapprovedDateBasis("created")');
  assert.equal(a.run('unapprovedDateBasis'),'closed');
  assert.equal(a.run('rawTicketsLoaded'),false);
  a.context.tickets = [ticket({TicketStatus:'Y8',ResolvedOnDateTime:'2026-04-01 09:00:00'})];
  a.run('readJson=async()=>tickets;');
  await a.run('setUnapprovedDateBasis("closed")');
  assert.equal(a.run('MONTHS_INDEX["2026-04"].unapprovedClosedIn'),1);
});

test('Repairer denominator matches Claim Trend default approval and actual-closure totals', () => {
  const a = app();
  const repairHtml = fs.readFileSync(path.join(__dirname, '..', 'repairs.html'), 'utf8');
  for (const name of ['claimTrendApprovedTicketTotal','claimTrendUnapprovedTicketTotal','repairOverviewApprovedTicketTotal','repairApprovedPlusUnapprovedTicketTotal']) {
    a.run(repairHtml.match(new RegExp('^function '+name+'\\([^]*?^}', 'm'))[0]);
  }
  a.context.tickets = [
    ticket({TicketStatus:'Y8',ResolvedOnDateTime:'2026-06-03 09:00:00'}),
    ticket({TicketID:'2',TicketStatus:'Y8',TicketTypeText:'Pre Delivery',ResolvedOnDateTime:'2026-06-04 09:00:00'}),
    ticket({TicketID:'3',TicketStatus:'Y8',CreatedOn:'01/01/2025',ResolvedOnDateTime:'2025-12-04 09:00:00'}),
    ticket({TicketID:'4',TicketStatus:'Y8',TicketType:'Z010',ResolvedOnDateTime:'2026-06-04 09:00:00'}),
  ];
  a.run(`const view={approvalClosedMonthly:[{month:'2026-06',inFieldApproved:5,preDeliveryApproved:2,inFieldUnapproved:99,preDeliveryUnapproved:99}]};
    RAW_TICKETS=tickets; MONTHLY=buildMonthly(view); MONTHS_INDEX=Object.fromEntries(MONTHLY.map(r=>[r.month,r]));
    let claimTrendUnapprovedCounts={loaded:true,monthly:ClaimTrendTicketMetrics.approvedMonthly(tickets),closedMonthly:ClaimTrendTicketMetrics.unapprovedMonthly(tickets)};
    let selectedRepairYear='2026';
    function repairPeriodBounds(){return {start:activeStart+'-01',end:activeEnd+'-30'}};
    function repairApprovedCostTicketTotal(){return 0};`);
  for (const [start,end] of [['2026-01','2026-09'],['2026-06','2026-06'],['2025-01','2025-12']]) {
    a.run(`activeStart='${start}';activeEnd='${end}';selectedRepairYear='${start.slice(0,4)}'`);
    assert.equal(a.run('claimTrendUnapprovedTicketTotal()'),a.run('rowsForRange().reduce((sum,r)=>sum+(r.unapprovedClosedIn||0)+(r.unapprovedClosedPre||0),0)'));
    assert.equal(a.run('repairApprovedPlusUnapprovedTicketTotal()'),a.run('rowsForRange().reduce((sum,r)=>sum+(r.approvedIn||0)+(r.approvedPre||0)+(r.unapprovedClosedIn||0)+(r.unapprovedClosedPre||0),0)'));
  }
});

test('daily data replacement recomputes both date views without retaining old counts', () => {
  const a = app();
  a.context.day1 = [ticket({TicketStatus:'Y8',ResolvedOnDateTime:'2026-03-01 09:00:00'})];
  a.context.day2 = [ticket({TicketStatus:'Y2'}),ticket({TicketID:'2',TicketStatus:'Y8',TicketTypeText:'Pre Delivery',CreatedOn:'01/02/2026',ResolvedOnDateTime:'2026-04-01 09:00:00'})];
  a.run('RAW_TICKETS=day1; MONTHLY=buildMonthly({}); RAW_TICKETS=day2; MONTHLY=withUnapprovedMonthly(withApprovedMonthly(MONTHLY)); MONTHS_INDEX=Object.fromEntries(MONTHLY.map(r=>[r.month,r]));');
  assert.equal(a.run('MONTHS_INDEX["2026-03"].unapprovedClosedIn'),0);
  assert.equal(a.run('MONTHS_INDEX["2026-01"].unapprovedCreatedIn'),0);
  assert.equal(a.run('MONTHS_INDEX["2026-01"].approvedCreatedIn'),1);
  assert.equal(a.run('MONTHS_INDEX["2026-04"].unapprovedClosedPre'),1);
  assert.equal(a.run('MONTHS_INDEX["2026-02"].unapprovedCreatedPre'),1);
});

test('version-checked ticket cache skips bulk downloads but refreshes when tickets or dashboard change', async () => {
  const saved=new Map();
  async function visit(version,core,raw,amount) {
    const a=app(),calls=[];
    Object.assign(a.context,{setTimeout,clearTimeout,console:{warn(){}},location:{hash:''}});
    a.context.window={WarrantyPageCache:{
      getPage:async(key,v)=>saved.get(key)?.version===v?saved.get(key).value:null,
      setPage:async(key,v,value)=>{saved.set(key,{version:v,value});return true;},
      setLargePage:async(key,v,value)=>{saved.set(key,{version:v,value});return true;},
      showBadge(){}
    }};
    a.context.readData=async path=>{
      calls.push(path);
      if(path.endsWith('/generatedAt'))return version;
      if(path.endsWith('/ticketCoreSyncAt'))return core;
      if(path.endsWith('/ticketSoSyncAt'))return 'so1';
      if(path==='c4cTickets_test/tickets')return raw;
      if(path.endsWith('/approvedAmountMonthly'))return [{month:'2026-03',inField:amount,preDelivery:0}];
      throw Error('Unexpected full dashboard download: '+path);
    };
    a.run('readJson=readData; init=view=>{MONTHLY=buildMonthly(view);};');
    await a.run(script.slice(script.indexOf('const CLAIM_TREND_PAGE_CACHE_KEY=')));
    return {a,calls};
  }
  const first=await visit('day1','core1',[{ticket:ticket(),roles:{unused:true},orders:[{}]},null,null],25);
  assert.ok(first.calls.includes('c4cTickets_test/tickets'));
  const firstTotals=first.a.json('MONTHLY');
  assert.equal(first.a.run('RAW_TICKETS[0].roles'),undefined);
  assert.equal(first.a.run('RAW_TICKETS[0].ticket.ChassisNumber'),'000123');
  const cached=await visit('day1','core1',[],999);
  assert.ok(!cached.calls.includes('c4cTickets_test/tickets'));
  assert.ok(!cached.calls.some(p=>p.endsWith('/approvedAmountMonthly')));
  assert.deepEqual(cached.a.json('MONTHLY'),firstTotals);
  assert.deepEqual(cached.a.json('buildExportRowsForRange("2026-01","2026-03")'),first.a.json('buildExportRowsForRange("2026-01","2026-03")'));
  const changed=await visit('day1','core2',[ticket({TicketStatus:'Y8',ResolvedOnDateTime:'2026-06-01 09:00:00'})],99);
  assert.ok(changed.calls.includes('c4cTickets_test/tickets'));
  assert.ok(!changed.calls.some(p=>p.endsWith('/approvedAmountMonthly')));
  assert.equal(changed.a.run('MONTHLY.find(r=>r.month==="2026-06").unapprovedClosedIn'),1);
  assert.equal(changed.a.run('MONTHLY.find(r=>r.month==="2026-01").approvedCreatedIn'),0);
  const next=await visit('day2','core2',[ticket()],80);
  assert.ok(next.calls.includes('c4cTickets_test/tickets'));
  assert.ok(next.calls.some(p=>p.endsWith('/approvedAmountMonthly')));
  assert.equal(next.a.run('MONTHLY.find(r=>r.month==="2026-03").approvedAmountIn'),80);
});

test('changing source during download is rejected without caching and remains retryable',async()=>{
  const a=app();let writes=0;
  Object.assign(a.context,{setTimeout,clearTimeout,console:{warn(){}},source:[ticket()]});
  a.context.window.WarrantyPageCache={getPage:async()=>null,setLargePage:async()=>{writes++;return true;}};
  a.run('loadClaimSourceVersions=async()=>({tickets:"before"}); readClaimSourceVersions=async()=>({tickets:"after"}); readJson=async()=>source;');
  await assert.rejects(a.run('ensureRawTicketsLoaded()'),/changed while loading/);
  assert.equal(a.run('rawTicketsLoaded'),false);
  assert.equal(a.run('rawTicketsPromise'),null);
  assert.equal(writes,0);
  a.run('loadClaimSourceVersions=async()=>({tickets:"after"});');
  await a.run('ensureRawTicketsLoaded()');
  assert.equal(a.run('rawTicketsLoaded'),true);
  assert.equal(writes,1);
});

test('unavailable IndexedDB falls back to live tickets without changing their fields',async()=>{
  const a=app();
  Object.assign(a.context,{setTimeout,clearTimeout,console:{warn(){}},source:[null,{ticket:ticket(),roles:{unused:true}}]});
  a.context.window.WarrantyPageCache={getPage:async()=>{throw Error('unavailable');}};
  a.run('loadClaimSourceVersions=async()=>({tickets:"current"}); readClaimSourceVersions=loadClaimSourceVersions; readJson=async()=>source;');
  await a.run('ensureRawTicketsLoaded()');
  assert.deepEqual(a.json('RAW_TICKETS'),[null,{ticket:ticket()}]);
  assert.equal(a.run('rawTicketsLoaded'),true);
});

test('every monthly Approved chart count equals the exported Approved rows for both date bases', () => {
  const a=app();
  a.context.tickets=[
    ticket({TicketID:'jan-in',CreatedOn:'01/12/2025',ClaimApprovedOnDateTime:'2026-01-03 08:00:00'}),
    ticket({TicketID:'jan-pre',TicketTypeText:'Pre Delivery',CreatedOn:'05/01/2026',ClaimApprovedOnDateTime:'2026-01-06 08:00:00'}),
    ticket({TicketID:'jun-in',CreatedOn:'01/01/2026',ClaimApprovedOnDateTime:'2026-06-01 08:00:00'}),
    ticket({TicketID:'unapproved',TicketStatus:'Y8',ResolvedOnDateTime:'2026-01-10 08:00:00',ClaimApprovedOnDateTime:'2026-01-06 08:00:00'}),
    ticket({TicketID:'closed',TicketStatus:'Y7',ClaimApprovedOnDateTime:'2026-01-06 08:00:00'}),
  ];
  a.run('RAW_TICKETS=tickets; MONTHLY=buildMonthly({approvalClosedMonthly:[{month:"2026-06",inFieldApproved:999,preDeliveryApproved:999}]}); MONTHS_INDEX=Object.fromEntries(MONTHLY.map(r=>[r.month,r]));');
  for(const basis of ['approved','created']){
    a.context.basis=basis;
    a.run('approvedDateBasis=basis');
    for(const year of ['2025','2026'])for(let month=1;month<=12;month++){
      a.context.month=`${year}-${String(month).padStart(2,'0')}`;
      const result=a.json(`(()=>{
        const sheet=exportDetailSheetRows(buildExportRowsForRange(month,month));
        const metricIndex=sheet[0].indexOf('Matches Metrics'),fieldIndex=sheet[0].indexOf('Field');
        const approved=sheet.slice(1).filter(row=>clean(row[metricIndex]).split(', ').includes('Approved'));
        const bucket=MONTHS_INDEX[month]||{},prefix=basis==='created'?'approvedCreated':'approved';
        return {inChart:bucket[prefix+'In']||0,preChart:bucket[prefix+'Pre']||0,
          inExport:approved.filter(row=>row[fieldIndex]==='In Field').length,
          preExport:approved.filter(row=>row[fieldIndex]==='Pre Delivery').length};
      })()`);
      assert.equal(result.inChart,result.inExport,`${basis} ${a.context.month} In Field`);
      assert.equal(result.preChart,result.preExport,`${basis} ${a.context.month} Pre Delivery`);
    }
  }
});

test('all static pages parse and their local script dependencies exist', () => {
  const root=path.join(__dirname,'..');
  const checked=new Set();
  for(const file of fs.readdirSync(root).filter(name=>name.endsWith('.html'))){
    const page=fs.readFileSync(path.join(root,file),'utf8');
    for(const match of page.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)){
      const src=match[1].match(/\bsrc=["']([^"']+)/i)?.[1];
      if(src){
        if(/^(https?:|\/\/|data:)/i.test(src))continue;
        const relative=src.split(/[?#]/)[0];
        const dependency=path.resolve(root,relative);
        assert.ok(fs.existsSync(dependency),`${file}: missing ${relative}`);
        if(!checked.has(dependency)){
          new vm.Script(fs.readFileSync(dependency,'utf8'),{filename:relative});
          checked.add(dependency);
        }
      }else if(!/type\s*=\s*['"](?:application\/|module)/i.test(match[1])){
        new vm.Script(match[2],{filename:file});
      }
    }
    if(['infieldpredelivery.html','repairs.html'].includes(file)){
      assert.match(page,/<script src="claim-trend-ticket-metrics\.js\?v=3"><\/script>/);
    }
  }
});
