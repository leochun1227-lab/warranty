import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const out = path.dirname(fileURLToPath(import.meta.url));
const data = JSON.parse(await fs.readFile(path.join(out, 'website_analysis.json'), 'utf8'));
const basis = process.argv[2];
if (!['c4c_amount','dashboard_amount'].includes(basis)) throw new Error('Choose a verified amount basis');
const missing = data.records.filter(r=>r[basis]===null).length;
const selectedLabel = basis==='c4c_amount' ? 'C4C approved ticket amounts, including tax' : 'SAP purchase order amounts, using the website Approved Cost rules';
const wb = Workbook.create();
const report = wb.worksheets.add('Monthly Approvals');
const detail = wb.worksheets.add('Approval Detail');
report.showGridLines=false;
detail.showGridLines=false;
report.getRange('A1:M21').format.font={name:'Arial',size:10,color:'#111111'};
report.getRange('A1:M21').format.rowHeight=22;
report.getRange('A1:A21').format.columnWidthPx=142;
report.getRange('B1:M21').format.columnWidthPx=108;
report.getRange('A2').values=[['Monthly Approval Amounts (AUD)']];
report.getRange('A2').format.font={name:'Arial',size:14,bold:true};
report.getRange('A2:M2').format.rowHeight=27;
report.getRange('A3').values=[[selectedLabel+'. October MTD: 1–21 October 2026.']];
report.getRange('A3').format.font={name:'Arial',size:10,italic:true,color:'#555555'};
report.getRange('A4').values=[['Matches website totals; 7 NZD source amounts are included without FX conversion, as on the website.']];
report.getRange('A4').format.font={name:'Arial',size:10,color:'#805B20'};
if (missing) {
  report.getRange('A4').values=[[`Partial totals: ${missing.toLocaleString('en-AU')} approved tickets have no available amount and are excluded. See Approval Detail.`]];
  report.getRange('A4').format.font={name:'Arial',size:10,color:'#A12622'};
}

const rows=data.records.map(r=>[r.group,r.dealer,r.ticket,r.type==='Z005'?'Pre Delivery':'In Field',new Date(r.approved_on+'T00:00:00Z'),r.status,
  r.c4c_amount,r.sap_amount,r.dashboard_amount,r[basis],r[basis]===null?'Missing amount':'Available',r.po_documents,
  r.sap_issue,r.sap_currency,r.sap_source_amount]);
const first=10,last=first+rows.length-1;
detail.getRange(`A1:O${last}`).format.font={name:'Arial',size:10,color:'#111111'};
detail.getRange(`A1:O${last}`).format.rowHeight=19;
detail.getRange('A1').values=[['Approval source records']];
detail.getRange('A1').format.font={name:'Arial',size:14,bold:true};
detail.getRange('A2').values=[['Website source']];
detail.getRange('B2').values=[[data.website_source]];
detail.getRange('A3').values=[['Website data (UTC)']];
detail.getRange('B3').values=[['UTC timestamp: '+data.website_generated_at]];
detail.getRange('A4').values=[['SAP source']];
detail.getRange('B4').values=[[data.sap_source]];
detail.getRange('A5').values=[['SAP data (UTC)']];
detail.getRange('B5').values=[['UTC timestamp: '+data.sap_generated_at]];
detail.getRange('A6').values=[['Scope']];
detail.getRange('B6').values=[['In Field + Pre Delivery. Website approved rows (Y7 excluded). Grouped by Warranty Handling Dealer (Assign); website decision month.']];
detail.getRange('A7').values=[['Group mapping']];
detail.getRange('B7').values=[['Green RV: 204642, 204670. Leisure Lion: 3133, 3135, 3138. Caravan Hub: 200035, 204677.']];
detail.getRange('A8').values=[['Selected amount']];
detail.getRange('B8').values=[[selectedLabel+'. Website zero costs contribute 0; source currency is retained without conversion.']];
detail.getRange('A9:O9').values=[['Entity','Handling dealer ID','Ticket ID','Claim type','Approval decision date','Status code','C4C claim amount','SAP source amount','Website cost','Selected website cost','Amount availability','SAP PO documents','Website amount note','SAP source currency','SAP source amount']];
detail.getRange(`A${first}:O${last}`).values=rows;
detail.getRange(`E${first}:E${last}`).setNumberFormat('dd/mm/yyyy');
detail.getRange(`G${first}:J${last}`).setNumberFormat('#,##0.00;(#,##0.00);"–"');
detail.getRange(`G${first}:J${last}`).format.horizontalAlignment='right';
detail.getRange(`A1:D${last}`).format.columnWidthPx=125;
detail.getRange(`E1:K${last}`).format.columnWidthPx=145;
detail.getRange(`L1:L${last}`).format.columnWidthPx=190;
detail.getRange(`M1:M${last}`).format.columnWidthPx=235;
detail.getRange(`N1:O${last}`).format.columnWidthPx=145;
detail.getRange(`O${first}:O${last}`).setNumberFormat('#,##0.00;(#,##0.00);"–"');
detail.getRange('A9:O9').format.fill='#EEEEEE';
detail.getRange('A9:O9').format.font={name:'Arial',size:10,bold:true};
detail.getRange('A9:O9').format.wrapText=true;
detail.getRange('A9:O9').format.rowHeight=32;
detail.freezePanes.freezeRows(9);
detail.tables.add(`A9:O${last}`,true,'ApprovalRecords');

function column(index){return String.fromCharCode(65+index);}
function table(sheet,year,titleRow,headerRow,firstRow,months,amountColumn='J'){
  const report=sheet;
  report.getRange(`A${titleRow}`).values=[[year]];
  report.getRange(`A${titleRow}`).format.font={name:'Arial',size:11,bold:true};
  report.getRange(`A${titleRow}`).format.horizontalAlignment='left';
  report.getRange(`A${headerRow}:M${headerRow}`).values=[['Month',...months.map(m=>new Date(m+'-01T00:00:00Z'))]];
  report.getRange(`B${headerRow}:M${headerRow}`).setNumberFormat('mmmm');
  if(year===2026){
    report.getRange(`B${headerRow}:C${headerRow}`).setNumberFormat('mmm yyyy');
    report.getRange(`M${headerRow}`).setNumberFormat('"October MTD"');
  }
  report.getRange(`A${headerRow}:M${headerRow}`).format.horizontalAlignment='center';
  report.getRange(`A${headerRow}:M${headerRow}`).format.fill='#F3F3F3';
  report.getRange(`A${firstRow}:A${firstRow+2}`).values=Object.keys(data.groups).map(g=>[g]);
  const formulas=Object.keys(data.groups).map((g,idx)=>months.map((m,j)=>{
    const c=column(j+1), r=firstRow+idx;
    if(year===2026 && j<2) return `=${j===0?'L':'M'}${7+idx}`;
    const upper=m==='2026-10'?'DATE(2026,10,22)':`EDATE(${c}$${headerRow},1)`;
    return `=SUMIFS('Approval Detail'!$${amountColumn}$${first}:$${amountColumn}$${last},'Approval Detail'!$A$${first}:$A$${last},$A${r},'Approval Detail'!$E$${first}:$E$${last},">="&${c}$${headerRow},'Approval Detail'!$E$${first}:$E$${last},"<"&${upper})`;
  }));
  report.getRange(`B${firstRow}:M${firstRow+2}`).formulas=formulas;
  report.getRange(`B${firstRow}:M${firstRow+2}`).setNumberFormat('#,##0.00;(#,##0.00);"–"');
  report.getRange(`B${firstRow}:M${firstRow+2}`).format.horizontalAlignment='right';
  report.getRange(`A${headerRow}:M${firstRow+2}`).format.borders={preset:'all',style:'thin',color:'#777777'};
  report.getRange(`A${headerRow}:M${firstRow+2}`).format.verticalAlignment='center';
}
const months25=Array.from({length:12},(_,i)=>`2025-${String(i+1).padStart(2,'0')}`);
const months26=['2025-11','2025-12',...Array.from({length:10},(_,i)=>`2026-${String(i+1).padStart(2,'0')}`)];
table(report,2025,5,6,7,months25);
table(report,2026,12,13,14,months26);
report.getRange('A18').values=[['November and December in the lower table are 2025 comparison months.']];
report.getRange('A18').format.font={name:'Arial',size:10,italic:true,color:'#555555'};

wb.recalculate();
for(const [sheet,metric] of [[report,basis]]){
for(const [range,months] of [['B7:M9',months25],['B14:M16',months26]]){
  const actual=sheet.getRange(range).values;
  Object.keys(data.groups).forEach((g,i)=>months.forEach((m,j)=>{
    const expected=data.summary[metric][g][m].known_total;
    if(typeof actual[i][j]!=='number' || Math.abs(actual[i][j]-expected)>0.005) throw new Error(`Reconciliation failed: ${g} ${m} ${actual[i][j]} != ${expected}`);
  }));
}
}
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},summary:'Formula error scan'})).ndjson);
console.log((await wb.inspect({kind:'table',range:'Monthly Approvals!A13:M16',include:'values,formulas',tableMaxRows:4,tableMaxCols:13,maxChars:5000})).ndjson);
for(const [name,sheetName,range] of [['monthly_approval_amounts','Monthly Approvals','A1:M19'],['detail_preview','Approval Detail','A1:O18']]){
  const png=await wb.render({sheetName,range,scale:1.5,format:'png'});
  await fs.writeFile(path.join(out,name+'.png'),new Uint8Array(await png.arrayBuffer()));
}
const output=await SpreadsheetFile.exportXlsx(wb);
await output.save(path.join(out,'Monthly_Approval_Amounts_2025_2026.xlsx'));
await fs.writeFile(path.join(out,'verification.json'),JSON.stringify({basis,approvedTickets:rows.length,missingAmounts:missing,summaryCellsReconciled:72,exported:true},null,2));
console.log(JSON.stringify({basis,approvedTickets:rows.length,missingAmounts:missing,exported:true}));
