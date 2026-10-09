const assert = require('node:assert/strict');
const api = require('../issue-subcategory-data.js');
assert.equal(api.basePath('c4cTickets_test'), 'issueSubcategoryAnalysis/v1/c4cTickets_test');
assert.throws(() => api.basePath('../tickets'));
assert.throws(() => api.parseSummary({ retrievalComplete: false, groups: [] }));
assert.deepEqual(api.parseSummary({retrievalComplete:true,acceptedIssueCount:0}).groups, []);
assert.throws(() => api.parseSummary({ retrievalComplete: true, groups: [{ subcategoryCode: 'invented', issueCount: 1, ticketCount: 1 }] }));
const summary={retrievalComplete:true,groups:[
  {ticketType:'Z006',month:'2026-09',subcategoryCode:'Z017',subcategoryName:'Lighting',issueCount:5,ticketCount:3},
  {ticketType:'Z006',month:'2026-10',subcategoryCode:'Z017',subcategoryName:'Lighting',issueCount:4,ticketCount:2},
  {ticketType:'Z006',month:'2026-10',subcategoryCode:'Z072',subcategoryName:'Others',issueCount:2,ticketCount:1},
  {ticketType:'Z005',month:'2026-10',subcategoryCode:'Z017',subcategoryName:'Lighting',issueCount:50,ticketCount:30}
],coverage:[{ticketType:'Z006',month:'2026-10',issueCount:8,classifiedCount:6}]};
assert.equal(api.top10(summary,'Z006','all','issues').rows[0].issues,9);
assert.equal(api.top10(summary,'Z006','2026-10','issues').pending,2);
assert.equal(api.top10(summary,'Z006','2026-10','issues').rows.length,1);
assert.equal(api.top10(summary,'Z006','2026-10','issues').total,6);
assert.equal(api.top10(summary,'Z005','2026-10','issues').total,50);
assert.equal(api.top10(summary,'Z006','2025-01').total,0);
const compact={schema:'issue-startup-v2',sourceRoot:'c4cTickets_test',sourceVersion:'2026-10-09T02:00:00Z',generatedAt:'2026-10-09T02:00:00Z',issueCount:8,acceptedIssueCount:6,ticketCount:3,acceptedTicketCount:2,
 categories:[['Z017','Lighting'],['Z072','Other']],months:['2026-10'],rows:[[0,0,0,4,2,3,1,0],[0,0,1,2,1,0,0,2]],coverage:[[0,0,8,6,3,2,2]],automation:{status:'partial'}};
const decoded=api.decodeStartup(compact).summary;
assert.equal(api.top10(decoded,'Z006','2026-10').pending,2);
assert.deepEqual(api.exportRows(decoded,'Z006','2026-10','issues').slice(1).map(r=>[r[4],r[5],r[6]]),[['Lighting',4,'66.7%']]);
assert.equal(api.exportRows(decoded,'Z005','2026-10').length,1);
assert.deepEqual(api.decodeStartup({...compact,data:JSON.stringify(compact)}).summary,decoded);
assert.throws(()=>api.decodeStartup({...compact,acceptedIssueCount:99}));
assert.throws(()=>api.decodeStartup({...compact,sourceRoot:'wrong-source'}));
assert.throws(()=>api.decodeStartup({...compact,rows:[...compact.rows,compact.rows[0]]}));
assert.throws(()=>api.decodeStartup({...compact,coverage:[[0,0,8,5]]}));
assert.throws(()=>api.decodeStartup({...compact,data:JSON.stringify({...compact,sourceVersion:'different'})}));
// Same Ticket in two categories: denominator is distinct Tickets, never the sum.
assert.equal(api.top10(decoded,'Z006').total,2);
assert.deepEqual(api.exportRows(decoded,'Z006','all').slice(1).map(r=>[r[5],r[6]]),[[2,'100.0%']]);
const divergent=api.decodeStartup({...compact,issueCount:17,acceptedIssueCount:17,ticketCount:6,acceptedTicketCount:6,
 rows:[[0,0,0,10,2,10,0,0],[0,0,1,7,5,7,0,0]],coverage:[[0,0,17,17,6,6,0]]}).summary;
assert.equal(api.top10(divergent,'Z006').rows[0].name,'Lighting');
assert.equal(api.top10(divergent,'Z006','all','issues').rows[0].name,'Lighting');
assert.equal(api.top10(divergent,'Z006').rows[0].share,2/6);
assert.throws(()=>api.decodeStartup({...compact,schema:'issue-startup-v1'}));
assert.throws(()=>api.decodeStartup({...compact,acceptedTicketCount:3}));
assert.throws(()=>api.top10(decoded,'Z006','all','invalid'));
(async () => {
  const paths=[];
  const response=await api.load(async path=>{
    paths.push(path);
    return path.endsWith('/startup') ? compact : {status:'failed'};
  },'c4cTickets_test');
  assert.equal(response.summary.groups[0].issueCount,4);
  assert.equal(response.refreshFailed,true);
  assert.equal(response.refreshing,false);
  assert.equal(paths.length,2);
  assert.ok(paths.every(p=>!p.endsWith('/summary')&&!p.endsWith('/results')));
  console.log('Issue Subcategory reader tests passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

// Annual scopes never blend years or unmatched Ticket dates.
const history={retrievalComplete:true,groups:[
 {ticketType:'Z006',month:'2026-09',subcategoryCode:'Z017',subcategoryName:'Lighting',issueCount:5,ticketCount:3},
 {ticketType:'Z006',month:'2025-09',subcategoryCode:'Z017',subcategoryName:'Lighting',issueCount:50,ticketCount:25},
 {ticketType:'Z006',month:'unknown',subcategoryCode:'Z017',subcategoryName:'Lighting',issueCount:10,ticketCount:8}
],coverage:[
 {ticketType:'Z006',month:'2026-09',issueCount:6,classifiedCount:5,classifiedTicketCount:3,pendingTicketCount:1},
 {ticketType:'Z006',month:'2025-09',issueCount:52,classifiedCount:50,classifiedTicketCount:25,pendingTicketCount:2},
 {ticketType:'Z006',month:'unknown',issueCount:10,classifiedCount:10,classifiedTicketCount:8,pendingTicketCount:0}
]};
assert.equal(api.top10(history,'Z006','2026').total,3);
assert.equal(api.top10(history,'Z006','2025').total,25);
assert.equal(api.top10(history,'Z006','2026','issues').total,5);
assert.equal(api.top10(history,'Z006','2026').pending,1);
assert.equal(api.exportRows(history,'Z006','2025')[1][1],'2025 · All months');
assert.deepEqual(api.reportingCalendar(new Date('2025-12-31T14:00:00Z')),{year:2026,month:1});
assert.equal(api.monthly(history,'Z006','2025','tickets','all',new Date('2026-10-09T00:00:00Z')).length,12);
assert.equal(api.monthly(history,'Z006','2026','tickets','all',new Date('2026-10-09T00:00:00Z')).length,10);

// Both merges the same category across types before choosing ten, rather than joining two Top 10 lists.
const combined={retrievalComplete:true,groups:[],coverage:[]};
for(const [i,type] of ['Z006','Z005'].entries()){
 for(let n=0;n<11;n++)combined.groups.push({ticketType:type,month:'2026-09',subcategoryCode:n===10?'Z099':'Z'+String(i*20+n).padStart(3,'0'),subcategoryName:n===10?'Shared':'Category '+i+' '+n,issueCount:12-n,ticketCount:12-n});
 combined.coverage.push({ticketType:type,month:'2026-09',issueCount:77,classifiedCount:77,classifiedTicketCount:77,pendingTicketCount:0});
}
// A shared category must appear once with the combined count, and scope limits remain ten.
for(const g of combined.groups)if(g.subcategoryCode==='Z099')g.issueCount=g.ticketCount=8;
for(const c of combined.coverage)c.issueCount=c.classifiedCount=c.classifiedTicketCount=83;
const merged=api.top10(combined,'both','2026');
assert.equal(merged.rows.length,10);assert.equal(merged.rows[0].code,'Z099');assert.equal(merged.rows[0].count,16);assert.equal(merged.total,166);
assert.equal(api.exportRows(combined,'both','2026').length,11);
assert.equal(api.exportRows(combined,'both','2026')[1][0],'Both');
assert.equal(api.monthly(combined,'both','2026','tickets','Z099',new Date('2026-10-09'))[8].count,16);

// Excluding Other happens before the Top 10 limit, for both metrics and every scope.
combined.groups.push({ticketType:'Z006',month:'2026-09',subcategoryCode:'Z072',subcategoryName:'Other',issueCount:1000,ticketCount:1000});
for(const metric of ['tickets','issues'])for(const scope of ['both','Z006','Z005']){
 assert.equal(api.top10(combined,scope,'2026',metric).rows.length,10);
 assert.ok(api.top10(combined,scope,'2026',metric).rows.every(r=>r.code!=='Z072'));
 assert.ok(api.exportRows(combined,scope,'2026',metric).slice(1).every(r=>r[3]!=='Z072'));
}
assert.ok(api.top10(combined,'both','2026','issues').rows.every(r=>r.code!=='Z072'));

const priced=api.decodeStartup({...compact,partsAmounts:[[12345,2],[0,1]]}).summary;
assert.equal(api.top10(priced,'both','2026').rows[0].partsAmountCents,12345);
assert.equal(api.top10(priced,'both','2026').rows[0].partsAmountKnownTickets,2);
assert.equal(api.exportRows(priced,'both','2026')[1][10],123.45);
assert.equal(api.monthly(priced,'both','2026','tickets','Z017',new Date('2026-10-09'))[9].partsAmountCents,12345);
assert.throws(()=>api.decodeStartup({...compact,partsAmounts:[[123,3],[0,1]]}));
assert.throws(()=>api.decodeStartup({...compact,partsAmounts:[[123,1]]}));

const allCreated=api.decodeStartup({...compact,allCreatedTickets:true,createdTicketCount:4,createdCoverage:[[0,0,4]]}).summary;
assert.equal(api.top10(allCreated,'both','2026').total,4);
assert.equal(api.top10(allCreated,'both','2026').rows[0].share,0.5);
assert.throws(()=>api.decodeStartup({...compact,allCreatedTickets:true,createdTicketCount:2,createdCoverage:[[0,0,2]]}));

const positionSnapshot={...compact,categoryDimension:'issue_position',otherCategoryCode:'Z009',excludedRankingCodes:['Z009','Z012','9997'],issueCount:8,acceptedIssueCount:8,ticketCount:4,acceptedTicketCount:4,categories:[['Z003','Electrical System'],['Z009','Other'],['Z012','Labour'],['9997','9997']],rows:[[0,0,0,2,1,2,0,0],[0,0,1,2,1,2,0,0],[0,0,2,2,1,2,0,0],[0,0,3,2,1,2,0,0]],coverage:[[0,0,8,8,4,4,0]]};
const positioned=api.decodeStartup(positionSnapshot).summary;
for(const metric of ['tickets','issues'])assert.deepEqual(api.top10(positioned,'both','2026',metric).rows.map(r=>r.code),['Z003']);
assert.equal(api.top10(positioned,'both','2026','issues').total,8);
assert.equal(api.exportRows(positioned,'both','2026')[0][4],'Issue Position');

const approvedSnapshot={...positionSnapshot,reportingBasis:'approved_on',ticketScope:'approved_only',allReportTickets:true,reportTicketCount:5,reportCoverage:[[0,0,5]]};
const approved=api.decodeStartup(approvedSnapshot).summary;
assert.equal(api.top10(approved,'both','2026').total,5);
assert.equal(api.top10(approved,'both','2026').rows[0].share,0.2);
assert.equal(api.top10(approved,'both','2026','issues').total,8);
assert.equal(api.exportRows(approved,'both','2026')[0][1],'Ticket approval period');
assert.match(api.exportRows(approved,'both','2026')[1][8],/Claim Approved On/);
assert.throws(()=>api.decodeStartup({...approvedSnapshot,ticketScope:'all'}));
assert.throws(()=>api.decodeStartup({...approvedSnapshot,reportTicketCount:3,reportCoverage:[[0,0,3]]}));
