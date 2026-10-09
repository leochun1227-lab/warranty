(function (root) {
  'use strict';
  function basePath(sourceRoot) {
    if (!/^[A-Za-z0-9_-]+$/.test(sourceRoot)) throw new Error('Invalid ticket source root');
    return 'issueSubcategoryAnalysis/v1/' + sourceRoot;
  }
  function parseSummary(value) {
    if (!value || value.retrievalComplete !== true) {
      throw new Error('No completed live issue scan is available');
    }
    // RTDB drops empty arrays; an entirely pending run is a valid zero-accepted summary.
    const groups = value.groups == null && value.acceptedIssueCount === 0 ? [] : value.groups;
    if (!Array.isArray(groups) || groups.some(g => !/^(?:Z\d{3}|9997)$/.test(g.subcategoryCode) ||
        !Number.isInteger(g.issueCount) || g.issueCount < 0 ||
        !Number.isInteger(g.ticketCount) || g.ticketCount < 0)) {
      throw new Error('Invalid issue subcategory summary');
    }
    return { ...value, groups };
  }
  function decodeStartup(value, sourceRoot = 'c4cTickets_test') {
    if(typeof value?.data === 'string') {
      const decoded=JSON.parse(value.data);
      if(value.sourceVersion!==decoded.sourceVersion || value.schema!==decoded.schema)throw new Error('Snapshot envelope mismatch');
      value=decoded;
    }
    const count=n=>Number.isSafeInteger(n)&&n>=0;
    if(value?.schema!=='issue-startup-v2'||value.sourceRoot!==sourceRoot||
        !Number.isFinite(Date.parse(value.generatedAt))||value.sourceVersion!==value.generatedAt||
        !count(value.issueCount)||!count(value.acceptedIssueCount)||!count(value.ticketCount)||!count(value.acceptedTicketCount)||
        !Array.isArray(value.categories)||!Array.isArray(value.months)||!Array.isArray(value.rows)||!Array.isArray(value.coverage))throw new Error('Invalid Subcategory startup');
    if(value.exportVersion!=null&&!/^[a-f0-9]{32}$/.test(value.exportVersion))throw new Error('Invalid export version');
    const positionMode=value.categoryDimension==='issue_position',otherCode=positionMode?'Z009':'Z072';
    if(positionMode&&(value.otherCategoryCode!==otherCode||!Array.isArray(value.excludedRankingCodes)||['Z009','Z012','9997'].some(c=>!value.excludedRankingCodes.includes(c))||value.excludedRankingCodes.some(c=>!/^(?:Z\d{3}|9997)$/.test(c))))throw Error('Invalid Issue Position rules');
    const categories=value.categories,months=value.months,types=['Z006','Z005'];
    if(categories.some(r=>!Array.isArray(r)||r.length!==2||!/^(?:Z\d{3}|9997)$/.test(r[0])||typeof r[1]!=='string'||!r[1].trim())||
        new Set(categories.map(r=>r[0])).size!==categories.length||
        months.some(m=>typeof m!=='string'||!/^(\d{4}-(0[1-9]|1[0-2])|unknown)$/.test(m))||new Set(months).size!==months.length)throw new Error('Invalid startup dictionary');
    const sums=new Map(),seen=new Set();
    const groups=value.rows.map(r=>{
      if(!Array.isArray(r)||r.length!==8||!r.every(count)||!types[r[0]]||!months[r[1]]||!categories[r[2]]||r[4]>r[3]||r[5]+r[6]+r[7]!==r[3])throw new Error('Invalid startup group');
      const key=r.slice(0,3).join('|');if(seen.has(key))throw new Error('Duplicate startup group');seen.add(key);
      const cm=r.slice(0,2).join('|');sums.set(cm,(sums.get(cm)||0)+r[3]);
      return {ticketType:types[r[0]],month:months[r[1]],subcategoryCode:categories[r[2]][0],subcategoryName:categories[r[2]][1],issueCount:r[3],ticketCount:r[4],sourceIssueCount:r[5],aiIssueCount:r[6],otherFallbackCount:r[7]};
    });
    if(value.partsAmounts!=null){
      if(!Array.isArray(value.partsAmounts)||value.partsAmounts.length!==groups.length)throw Error('Invalid parts amount coverage');
      value.partsAmounts.forEach((r,i)=>{
        if(!Array.isArray(r)||r.length!==2||!Number.isSafeInteger(r[0])||!count(r[1])||r[1]>groups[i].ticketCount)throw Error('Invalid parts amount');
        groups[i].partsAmountCents=r[0];groups[i].partsAmountKnownTickets=r[1];
      });
    }
    const covSeen=new Set();
    const coverage=value.coverage.map(r=>{
      if(!Array.isArray(r)||r.length!==7||!r.every(count)||!types[r[0]]||!months[r[1]]||r[3]>r[2]||
          Math.max(r[5],r[6])>r[4]||r[4]>r[5]+r[6]||r[4]>r[2]||r[5]>r[3]||r[6]>r[2]-r[3])throw new Error('Invalid startup coverage');
      const key=r.slice(0,2).join('|');if(covSeen.has(key)||(sums.get(key)||0)!==r[3])throw new Error('Startup counts do not reconcile');covSeen.add(key);
      const matching=groups.filter(g=>g.ticketType===types[r[0]]&&g.month===months[r[1]]);
      if(matching.some(g=>g.ticketCount>r[5])||matching.reduce((n,g)=>n+g.ticketCount,0)<r[5])throw new Error('Invalid classified Ticket coverage');
      return {ticketType:types[r[0]],month:months[r[1]],issueCount:r[2],classifiedCount:r[3],ticketCount:r[4],classifiedTicketCount:r[5],pendingTicketCount:r[6]};
    });
    if([...sums.keys()].some(k=>!covSeen.has(k))||groups.reduce((n,r)=>n+r.issueCount,0)!==value.acceptedIssueCount||coverage.reduce((n,r)=>n+r.issueCount,0)!==value.issueCount)throw new Error('Startup totals do not reconcile');
    if(coverage.reduce((n,r)=>n+r.ticketCount,0)!==value.ticketCount||coverage.reduce((n,r)=>n+r.classifiedTicketCount,0)!==value.acceptedTicketCount)throw new Error('Ticket totals do not reconcile');
    let createdCoverage=[];
    if(value.allCreatedTickets){
      if(!Array.isArray(value.createdCoverage)||!count(value.createdTicketCount))throw Error('Missing Created On totals');
      const seenCreated=new Set();
      createdCoverage=value.createdCoverage.map(r=>{
        if(!Array.isArray(r)||r.length!==3||!r.every(count)||!types[r[0]]||!months[r[1]]||seenCreated.has(r[0]+'|'+r[1]))throw Error('Invalid Created On coverage');
        seenCreated.add(r[0]+'|'+r[1]);return {ticketType:types[r[0]],month:months[r[1]],ticketCount:r[2]};
      });
      if(createdCoverage.reduce((n,r)=>n+r.ticketCount,0)!==value.createdTicketCount||coverage.some(r=>r.ticketCount>(createdCoverage.find(c=>c.ticketType===r.ticketType&&c.month===r.month)?.ticketCount??0)))throw Error('Created On totals do not reconcile');
    }
    return {snapshot:value,summary:{retrievalComplete:true,generatedAt:value.generatedAt,sourceVersion:value.sourceVersion,issueCount:value.issueCount,acceptedIssueCount:value.acceptedIssueCount,ticketCount:value.ticketCount,acceptedTicketCount:value.acceptedTicketCount,categoryDimension:positionMode?'issue_position':'subcategory',otherCategoryCode:otherCode,excludedRankingCodes:positionMode?value.excludedRankingCodes:['Z072'],allCreatedTickets:!!value.allCreatedTickets,createdCoverage,exportVersion:value.exportVersion||null,groups,coverage},automation:value.automation||{}};
  }
  async function load(readJson, sourceRoot) {
    const path = basePath(sourceRoot);
    const [startup, automation] = await Promise.all([
      readJson(path + '/startup'), readJson(path + '/automation')
    ]);
    return {
      summary: decodeStartup(startup,sourceRoot).summary, automation: automation || {},
      refreshFailed: automation?.status === 'failed',
      refreshing: automation?.status === 'running'
    };
  }
  function reportingCalendar(now=new Date()) {
    const parts=new Intl.DateTimeFormat('en-AU',{timeZone:'Australia/Sydney',year:'numeric',month:'2-digit'}).formatToParts(now);
    return {year:Number(parts.find(p=>p.type==='year').value),month:Number(parts.find(p=>p.type==='month').value)};
  }
  function matchesPeriod(month,period) {
    return period==='all'||(/^\d{4}$/.test(period)?month.startsWith(period+'-'):month===period);
  }
  function periodLabel(period) {
    return /^\d{4}$/.test(period)?period+' · All months':period==='all'?'All months':period==='unknown'?'Date not matched':period;
  }
  function top10(summary, type, month = 'all', metric = 'tickets') {
    if(!['tickets','issues'].includes(metric))throw new Error('Invalid counting metric');
    const groups = parseSummary(summary).groups.filter(g => (type === 'both' || g.ticketType === type) && matchesPeriod(g.month,month));
    const merged = new Map();
    for (const g of groups) {
      const row = merged.get(g.subcategoryCode) || {code:g.subcategoryCode,name:g.subcategoryCode===(summary.otherCategoryCode||'Z072')?'Other':g.subcategoryName,issues:0,tickets:0,source:0,ai:0,other:0,partsAmountCents:0,partsAmountKnownTickets:0};
      row.partsAmountCents+=g.partsAmountCents||0;row.partsAmountKnownTickets+=g.partsAmountKnownTickets||0;
      row.issues+=g.issueCount; row.tickets+=g.ticketCount;
      row.source+=g.sourceIssueCount||0; row.ai+=g.aiIssueCount||0; row.other+=g.otherFallbackCount||0;
      merged.set(row.code,row);
    }
    const all=[...merged.values()].sort((a,b)=>b[metric]-a[metric]||a.code.localeCompare(b.code));
    const coverage=(summary.coverage||[]).filter(g=>(type==='both'||g.ticketType===type)&&matchesPeriod(g.month,month));
    const issueCount=coverage.reduce((n,g)=>n+g.issueCount,0);
    const total=metric==='tickets'?(summary.allCreatedTickets?summary.createdCoverage.filter(g=>(type==='both'||g.ticketType===type)&&matchesPeriod(g.month,month)).reduce((n,g)=>n+g.ticketCount,0):coverage.reduce((n,g)=>n+g.classifiedTicketCount,0)):all.reduce((n,r)=>n+r.issues,0);
    if(!Number.isSafeInteger(total))throw new Error('Distinct Ticket coverage is missing');
    const pending=metric==='tickets'?coverage.reduce((n,g)=>n+g.pendingTicketCount,0):Math.max(0,issueCount-total);
    // Other stays in totals and complete details, but never participates in Top 10 rankings.
    const ranked=all.filter(r=>!(summary.excludedRankingCodes||['Z072']).includes(r.code));
    return {rows:ranked.slice(0,10).map(r=>({...r,count:r[metric],share:total?r[metric]/total:0})),total,categories:ranked.length,
      pending,issueCount:coverage.length?issueCount:all.reduce((n,r)=>n+r.issues,0)};
  }
  function exportRows(summary,scope,month,metric='tickets'){
    if(!['both','Z005','Z006'].includes(scope))throw new Error('Invalid export scope');
    const dimension=summary.categoryDimension==='issue_position'?'Issue Position':'Subcategory';
    const rows=[['Ticket type','Ticket creation period','Rank',dimension+' code',dimension,metric==='tickets'?'Distinct Ticket count':'Issue count','Share of '+(summary.allCreatedTickets&&metric==='tickets'?'Created On tickets':'classified '+metric),'Data updated','Date basis','Count basis','Ticket Parts Amount (AUD)','Tickets with parts amount','Amount basis']];
    const type=scope;top10(summary,type,month,metric).rows.forEach((r,i)=>rows.push([
      type==='both'?'Both':type==='Z006'?'In Field':'Pre Delivery',periodLabel(month),i+1,r.code,r.name,r.count,(r.share*100).toFixed(1)+'%',summary.generatedAt,'Ticket CreatedOn',metric==='tickets'?'Distinct Tickets per '+dimension+'; categories can overlap':'One Issue per '+dimension,r.partsAmountKnownTickets?r.partsAmountCents/100:'',r.partsAmountKnownTickets,'Factory Parts + Repairer Parts; distinct Ticket per category; categories overlap; no labour'
    ]));
    return rows;
  }
  function monthly(summary,type,year,metric='tickets',category='all',now=new Date()){
    if(!['tickets','issues'].includes(metric)||!/^\d{4}$/.test(String(year)))throw new Error('Invalid monthly selection');
    const calendar=reportingCalendar(now);
    const last=Number(year)<calendar.year?12:Number(year)===calendar.year?calendar.month:0;
    return Array.from({length:last},(_,i)=>{
      const month=year+'-'+String(i+1).padStart(2,'0');
      const total=category==='all'?top10(summary,type,month,metric).total:
        summary.groups.filter(g=>(type==='both'||g.ticketType===type)&&g.month===month&&g.subcategoryCode===category).reduce((n,g)=>n+g[metric==='tickets'?'ticketCount':'issueCount'],0);
      const groups=summary.groups.filter(g=>(type==='both'||g.ticketType===type)&&g.month===month&&g.subcategoryCode===category);
      return {month,count:total,partsAmountCents:groups.reduce((n,g)=>n+(g.partsAmountCents||0),0),partsAmountKnownTickets:groups.reduce((n,g)=>n+(g.partsAmountKnownTickets||0),0),tickets:groups.reduce((n,g)=>n+g.ticketCount,0)};
    });
  }
  const api = { reportingCalendar, matchesPeriod, periodLabel, monthly, basePath, parseSummary, decodeStartup, load, top10, exportRows };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.IssueSubcategoryData = api;
})(typeof window !== 'undefined' ? window : globalThis);
