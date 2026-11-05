/* Monthly approval export: dealer groups from the reference, plus the selected repairers. */
const REPAIR_APPROVAL_GROUPS=[
  {name:'Green RV',dealers:['204642','204670']},
  {name:'Leisure Lion',dealers:['3133','3135','3138']},
  {name:'Caravan Hub',dealers:['200035','204677']},
];
function approvalExportMonths(year){
  return Array.from({length:12},(_,i)=>`${year}-${String(i+1).padStart(2,'0')}`);
}
function approvalExportState(value){
  return String(value.state||String(value.repairName||'').match(/\((QLD|NSW|VIC|WA|SA|TAS|ACT|NT|NZ|AU)\)\s*$/i)?.[1]||String(value.repairId||'').split('|')[1]||'').toUpperCase();
}
function approvalExportRepairerMatch(info,row){
  const name=value=>String(value.repairName||'').replace(/\s*\((?:QLD|NSW|VIC|WA|SA|TAS|ACT|NT|NZ|AU)\)\s*$/i,'').trim().toLowerCase();
  return name(info)===name(row)&&!!name(info)&&approvalExportState(info)===approvalExportState(row);
}
function approvalExportNameKey(info){
  return String(info.repairName||'').replace(/\s*\((?:QLD|NSW|VIC|WA|SA|TAS|ACT|NT|NZ|AU)\)\s*$/i,'')
    .toUpperCase().replace(/\b(?:AS REPAIRER|REPAIRS|REPAIRER)\b/g,' ').replace(/[^A-Z0-9]+/g,' ').trim();
}
function buildApprovalExportSheets({records,periods,repairers,asOf,rankingLabel,stateCode}){
  const groups=REPAIR_APPROVAL_GROUPS.map(group=>({...group,months:{}}));
  const shops=repairers.map(row=>({...row,months:{},unavailable:new Set()}));
  const dealerGroups=new Map(groups.flatMap(group=>group.dealers.map(dealer=>[dealer,group])));
  const seen=new Set();
  for(const record of records){
    if(record.decisionKey!=='approved'||!/^202[56]-\d{2}-\d{2}$/.test(record.date)||seen.has(record.id))continue;
    seen.add(record.id);
    const month=record.date.slice(0,7),group=dealerGroups.get(record.dealer);
    if(group)group.months[month]=(group.months[month]||0)+record.amount;
    if(month.startsWith('2025-')){
      for(const shop of shops){
        if(record.ambiguousRepairers?.some(info=>approvalExportRepairerMatch(info,shop)))shop.unavailable.add(month);
        else if(approvalExportRepairerMatch(record.repairer,shop))shop.months[month]=(shop.months[month]||0)+record.amount;
      }
    }
  }
  // Keep 2026 figures on the same source and identity rules as the Repairer table.
  for(const [month,rows] of Object.entries(periods)){
    if(!month.startsWith('2026-'))continue;
    for(const shop of shops){
      shop.months[month]=rows.filter(row=>approvalExportRepairerMatch(row,shop))
        .reduce((sum,row)=>sum+Number(row.confirmed_cost||0),0);
    }
  }
  const latestMonth=[...records.map(row=>row.date.slice(0,7)),...Object.keys(periods)]
    .filter(month=>/^2026-\d{2}$/.test(month)).sort().pop();
  const months26=['2025-11','2025-12',...approvalExportMonths(2026).filter(month=>!latestMonth||month<=latestMonth)];
  const monthLabel=(month,year)=>{
    const date=new Date(`${month}-01T00:00:00Z`);
    const label=date.toLocaleDateString('en-AU',{month:month.startsWith(String(year))?'long':'short',...(!month.startsWith(String(year))?{year:'numeric'}:{}),timeZone:'UTC'});
    return month===asOf.slice(0,7)&&asOf.slice(8,10)<String(new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate()).padStart(2,'0')?`${label} MTD`:label;
  };
  function sheet(name,entities,note,value){
    const rows=[[],['Monthly Approval Amounts (AUD)'],[note],[`Approval source updated: ${asOf}. Amounts use the website Approved Cost rules.`]];
    const rowStyles={1:'ApprovalTitle',2:'ApprovalNote',3:'ApprovalNote'};
    for(const [year,months] of [[2025,approvalExportMonths(2025)],[2026,months26]]){
      if(year===2026)rows.push([],[]);
      rowStyles[rows.length]='ApprovalYear';rows.push([year]);
      rowStyles[rows.length]='ApprovalHeader';rows.push(['Month',...months.map(month=>monthLabel(month,year))]);
      for(const entity of entities)rows.push([entity.name||entity.repairName,...months.map(month=>value(entity,month))]);
    }
    rows.push([]);
    rowStyles[rows.length]='ApprovalNote';rows.push(['November and December in the lower table are 2025 comparison months.']);
    return {name,rows,rowStyles,approvalLayout:true};
  }
  return [
    sheet('Monthly Approvals',groups,'Grouped by Warranty Handling Dealer (Assign), as in the reference workbook.',(group,month)=>group.months[month]||0),
    sheet('Top 8 Repairers',shops,`${stateCode||'All states'}; repairers selected by ${rankingLabel}. 2025: approval history; 2026: Repairer monthly data.`,(shop,month)=>shop.unavailable.has(month)||(month.startsWith('2026-')&&!Object.hasOwn(periods,month))?'No data available':shop.months[month]||0),
  ];
}
async function loadApprovalExportRecords(){
  const base='ctmTicketStatusMonitorV44/analytics/team';
  const before=await readJson(`${base}/generatedAt`);
  if(!before)throw new Error('Approval history is unavailable. Please retry the export.');
  const [payload,raw,currentEntries]=await Promise.all([
    readJson(`${base}/views/all/approvalTicketRows`,30000),loadRepairRawTickets(),ensureRepairDetailsLoaded({renderAfter:false}),
  ]);
  const after=await readJson(`${base}/generatedAt`);
  if(before!==after)throw new Error('Approval data changed while loading. Please retry the export.');
  const rows=Array.isArray(payload)?payload:payload&&typeof payload==='object'?Object.values(payload):[];
  if(!rows.length)throw new Error('Approval history is unavailable. Please retry the export.');
  const ticketsById=new Map(Object.entries(raw).filter(([,row])=>row&&typeof row==='object').map(([key,row])=>{
    const entry=normalizeTicket(row,key);return [entry.id,entry];
  }));
  // SAP vendor labels can differ from historical C4C names. Bridge them through
  // the stable C4C service-technician ID on tickets present in both sources.
  const byProvider=new Map(),identities=new Map();
  const provider=entry=>String(entry?.roles?.['43']?.InvolvedPartyBusinessPartnerID||entry?.roles?.['43']?.InvolvedPartyID||'');
  const dealer=entry=>String(entry?.ticket?.WarrantyHandlingDealerID||entry?.ticket?.WarrantyHandlingDealerAssign||entry?.ticket?.['Warranty Handling Dealer(Assign)']||'').trim();
  for(const entry of currentEntries){
    const original=ticketsById.get(entry.id),id=provider(original),info=repairInfo(entry);
    if(info.repairId==='no-repair')continue;
    identities.set(info.repairId,info);
    if(!id)continue;
    for(const key of [id,`${id}|${dealer(original)}`]){
      const matches=byProvider.get(key)||new Map();matches.set(info.repairId,info);byProvider.set(key,matches);
    }
  }
  const records=[];
  for(const row of rows){
    if(!row||row.decisionKey!=='approved'||!/^202[56]-/.test(row.decisionDate||''))continue;
    const entry=ticketsById.get(String(row.id));
    if(!entry)throw new Error(`Approval ticket ${row.id} is unavailable. Please refresh and retry the export.`);
    const ticket=entry.ticket,rawAmount=row.exportAmount??row.amount,amount=Number(rawAmount);
    if(rawAmount==null||rawAmount===''||!Number.isFinite(amount))throw new Error(`Approval amount is unavailable for ticket ${row.id}.`);
    const original=repairInfo(entry),name=approvalExportNameKey(original);
    // Prefer the historical repairer's canonical business name. A provider can
    // appear against several SAP vendors, so that bridge is only a fallback.
    let named=[...identities.values()].filter(info=>approvalExportNameKey(info)===name);
    if(!named.length&&name.split(' ').length>=3)named=[...identities.values()].filter(info=>approvalExportNameKey(info).startsWith(`${name} `));
    const specific=byProvider.get(`${provider(entry)}|${dealer(entry)}`),general=byProvider.get(provider(entry));
    const routedByDealer=isSnowyRiverName(entry.roles?.['43']?.InvolvedPartyName||ticket.OriginalRepairerName||ticket.ServiceTechnician||'');
    const bridged=routedByDealer?specific:(specific?.size===1?specific:general);
    const originalState=approvalExportState(original);
    const matches=(named.length?named:[...(bridged?.values()||[])])
      .filter(info=>!originalState||originalState==='NA'||approvalExportState(info)===originalState);
    const repairer=matches.length===1?matches[0]:original;
    records.push({id:String(row.id),decisionKey:row.decisionKey,date:row.decisionDate,
      dealer:dealer(entry),amount:Math.max(0,repairCostAfterSanityCheck(ticket,amount)),repairer,
      ambiguousRepairers:matches.length>1?matches:[]});
  }
  if(!records.some(row=>row.date.startsWith('2025-')))throw new Error('2025 approval history is unavailable. Please retry the export.');
  return {records,asOf:String(before)};
}
