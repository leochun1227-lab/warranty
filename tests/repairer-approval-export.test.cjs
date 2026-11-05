const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../repairer-approval-export.js'),'utf8');
const shop={repairId:'Green RV Pty Ltd|QLD',repairName:'Green RV Pty Ltd (QLD)',state:'QLD'};
const record=(id,date,amount,dealer='204642',repairer=shop)=>({id,date,amount,dealer,repairer,decisionKey:'approved'});
const plain=value=>JSON.parse(JSON.stringify(value));
test('exports three dealer groups and selected repairers in two annual tables without detail sheets',()=>{
  const ctx=vm.createContext({});vm.runInContext(source,ctx);
  const sheets=plain(ctx.buildApprovalExportSheets({
    records:[record('1','2025-01-05',100),record('2','2025-01-06',25,'204670'),record('3','2025-11-01',50),record('4','2026-01-01',700),record('5','2026-11-01',90),record('6','2025-01-02',200,'3133'),record('7','2025-01-02',300,'200035'),record('1','2025-01-05',100),{...record('8','2025-01-01',900),decisionKey:'unapproved'}],
    periods:{'2026-01':[{...shop,confirmed_cost:600}],'2026-11':[]},repairers:[shop],asOf:'2026-11-05T00:30:03+00:00',rankingLabel:'Cost',stateCode:'QLD',
  }));
  assert.deepEqual(sheets.map(s=>s.name),['Monthly Approvals','Top 8 Repairers']);
  const [groups,shops]=sheets;
  assert.deepEqual(groups.rows.slice(6,9).map(r=>r[0]),['Green RV','Leisure Lion','Caravan Hub']);
  assert.equal(groups.rows[6][1],125,'dealer IDs are grouped, duplicate tickets excluded');
  assert.equal(groups.rows[7][1],200);
  assert.equal(groups.rows[8][1],300);
  assert.equal(groups.rows[4][0],2025);
  assert.equal(groups.rows[11][0],2026);
  assert.deepEqual(groups.rows[12].slice(0,4),['Month','Nov 2025','Dec 2025','January']);
  assert.equal(groups.rows[12].at(-1),'November MTD');
  assert.equal(groups.rows[13][1],50,'comparison month reuses 2025');
  assert.equal(groups.rows[13][3],700,'dealer group retains the template source');
  const secondHeader=shops.rows.findIndex(r=>r[0]===2026)+1;
  assert.equal(shops.rows[secondHeader+1][3],600,'repairer amount matches current monthly summary');
  assert.equal(shops.rows[secondHeader+1][4],'No data available','missing summary month is not a zero');
  assert.equal(shops.rows[secondHeader+1].at(-1),0,'present month with no tickets is a zero');
});
test('historical repairer matching keeps state boundaries and exposes ambiguous assignments',()=>{
  const ctx=vm.createContext({});vm.runInContext(source,ctx);
  assert.equal(ctx.approvalExportRepairerMatch({...shop,state:'NSW',repairName:'Green RV Pty Ltd (NSW)'},shop),false);
  const records=[{...record('1','2025-01-01',100),ambiguousRepairers:[shop,{...shop,repairId:'Other|VIC',repairName:'Other (VIC)',state:'VIC'}]}];
  const sheets=ctx.buildApprovalExportSheets({records,periods:{},repairers:[shop],asOf:'2026-10-21',rankingLabel:'Cost',stateCode:''});
  assert.equal(sheets[1].rows[6][1],'No data available');
  assert.equal(sheets[0].rows[6][1],100,'dealer group does not depend on repairer attribution');
});
test('source loading bridges historical provider IDs to current vendor names and rejects changed snapshots',async()=>{
  const old={id:'001',ticket:{WarrantyHandlingDealerID:'204642'},roles:{43:{InvolvedPartyBusinessPartnerID:'provider'}}};
  const current={...old,id:'002'};
  let version=0,changed=false;
  const ctx=vm.createContext({
    readJson:async p=>p.endsWith('/generatedAt')?(changed?`v${++version}`:'2026-11-05'):[{id:'001',decisionKey:'approved',decisionDate:'2025-01-01',amount:100,exportAmount:0}],
    loadRepairRawTickets:async()=>({old,current}),ensureRepairDetailsLoaded:async()=>[current],
    normalizeTicket:r=>r,repairInfo:r=>r.id==='002'?shop:{repairName:'Old vendor name',repairId:'old'},repairCostAfterSanityCheck:(_,cost)=>cost,isSnowyRiverName:()=>false,
  });vm.runInContext(source,ctx);
  const result=await ctx.loadApprovalExportRecords();
  assert.equal(result.records[0].amount,0,'explicit export zero is preserved');
  assert.equal(result.records[0].repairer.repairId,shop.repairId);
  changed=true;
  await assert.rejects(ctx.loadApprovalExportRecords(),/changed while loading/);
});
test('shared Excel writer retains existing naming-rule sheets unless explicitly disabled',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../repairs.html'),'utf8');
  let xml;
  const ctx=vm.createContext({
    clean:v=>String(v??'').trim(),buildRepairerNamingRulesExportRows:()=>[['Rule'],['Existing rule']],
    Blob:class {constructor(parts){xml=parts.join('');}},URL:{createObjectURL:()=>'',revokeObjectURL:()=>{}},
    document:{createElement:()=>({click(){},remove(){}}),body:{appendChild(){}}},setTimeout:()=>{},alert:message=>{throw Error(message);},
  });
  vm.runInContext(html.slice(html.indexOf('function xmlEscape('),html.indexOf('const nextPaint=')),ctx);
  ctx.exportWorkbook([{name:'Tickets',rows:[['Ticket','Cost'],['001',12.34]]}],'tickets');
  assert.match(xml,/Repairer Naming Rules/);
  assert.match(xml,/<Data ss:Type="String">001<\/Data>/);
  assert.match(xml,/<Data ss:Type="Number">12.34<\/Data>/);
  ctx.exportWorkbook([{name:'Monthly Approvals',approvalLayout:true,rows:[[],['Title'],['Month','January'],['A & B',12.34]],rowStyles:{1:'ApprovalTitle',2:'ApprovalHeader'}}],'monthly',{includeNamingRules:false});
  assert.equal((xml.match(/<Worksheet ss:Name=/g)||[]).length,1);
  assert.doesNotMatch(xml,/Repairer Naming Rules/);
  assert.match(xml,/A &amp; B/);
  assert.match(xml,/ss:MergeAcross="1"/);
  assert.match(xml,/ss:StyleID="ApprovalAmount"/);
});
