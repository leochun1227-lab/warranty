import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const out=path.dirname(fileURLToPath(import.meta.url));
const addresses=JSON.parse(await fs.readFile(path.resolve(out,'../recall_postcode_20260914/conflict_addresses.json'),'utf8'));
const decisions=JSON.parse(await fs.readFile(path.join(out,'address_decisions.json'),'utf8'));
const previous=JSON.parse(await fs.readFile(path.join(out,'expected_rows.json'),'utf8'));
const clean=v=>v===null||v===undefined||String(v).trim()==='#'?'':String(v).trim();
const states={'Queensland':'QLD','Victoria':'VIC','New South Wales':'NSW','Western Australia':'WA','South Australia':'SA','Tasmania':'TAS','Australia':'AU','New Zealand':'NZ'};
const abbr=v=>states[clean(v)]||clean(v).replace(/^AU-/,'');
function evidenceText(r,source){
 const seen=new Set(),blocks=[];
 for(const e of r.sources.filter(x=>x.source===source)){
  const street=[clean(e.house_number),clean(e.street)].filter(Boolean).join(' ')||'(street missing)';
  const place=[clean(e.suburb)||'(suburb missing)',abbr(e.state),clean(e.postcode),abbr(e.country)].filter(Boolean).join(' ');
  const text=`${clean(e.name)}\n${street}\n${place}`;
  const key=text.toUpperCase().replace(/[^A-Z0-9]/g,'');
  if(!seen.has(key)){seen.add(key);blocks.push(text);}
 }
 return blocks.join('\n\n');
}
const wb=Workbook.create();
const s=wb.worksheets.add('Address review');
s.showGridLines=false;
s.getRange('A1:G54').format.font={name:'Arial',size:10,color:'#222222'};
s.getRange('A1:G54').format.verticalAlignment='center';
s.getRange('A1:G54').format.rowHeight=21;
s.getRange('A2').values=[['Recall postcode review using customer addresses']];
s.getRange('A2').format.font={name:'Arial',size:12,bold:true,color:'#222222'};
s.getRange('A3').values=[['38 tickets: 8 address-supported corrections; 18 suggestions; 12 require address or identity confirmation.']];
s.getRange('A4').values=[['Address supported = consistent address + postcode check. Suggested = likely. Confirm address = no unique choice.']];
s.getRange('A5').values=[['These are proposals only. Current residence has not been independently confirmed. No Firebase changes were made in this review.']];
s.getRange('A7:G7').values=[['Ticket ID','Customer on ticket','Registration: name / address','SAP: name / address','Proposed postcode','Assessment','Reason']];
s.getRange('A7:G7').format.fill='#EEEEEE';
s.getRange('A7:G7').format.font.bold=true;
s.getRange('A7:G7').format.horizontalAlignment='center';
s.getRange('A7:G7').format.rowHeight=30;
s.getRange('A7:G7').format.wrapText=true;
const widths={A:11,B:30,C:45,D:45,E:16,F:20,G:55};
for(const [col,width] of Object.entries(widths))s.getRange(`${col}1:${col}54`).format.columnWidth=width;
const rows=[];
for(let i=0;i<decisions.length;i++){
 const d=decisions[i],r=addresses.find(x=>x.ticket_id===d.id),old=previous.find(x=>x[0]===d.id),row=8+i;
 if(!r||!old)throw new Error('Missing evidence '+d.id);
 const values=[d.id,old[1],evidenceText(r,'Registration'),evidenceText(r,'SAP'),d.postcode||'Not selected',d.assessment,d.reason];
 rows.push(values);
 s.getRange(`A${row}:G${row}`).values=[values];
 s.getRange(`A${row}:G${row}`).format.wrapText=true;
 const capacities=[10,29,43,43,15,19,53];
 const lines=values.map((v,j)=>v.split('\n').reduce((n,l)=>n+Math.max(1,Math.ceil(l.length/capacities[j])),0));
 s.getRange(`A${row}:G${row}`).format.rowHeight=Math.max(...lines)*13+12;
 const urls=[...(d.places||[]).map(p=>'https://auspost.com.au/postcode/'+p),...(d.urls||[])];
 const sourceRows=r.sources.map(e=>`${e.source}, row ${e.row}`).join('; ');
 wb.notes.add({id:`source-${d.id}`,target:{cell:{sheetName:s.name,sheetId:s.sheetId,address:`G${row}`}},authorId:'',createdAt:'',body:{plainText:`Evidence: supplied workbook records (${sourceRows}).\nRegistration = Product_Registrations_Till_17072026.xlsx, Sheet38. SAP = SAPAnalyticsReport(Z578DB6F34E1CDB92C9D1CE).xlsx, first sheet.\nPublic postcode checks:\n${urls.join('\n')||'No unique address to validate; no postcode selected.'}\nChecked 14 Sep 2026. Postcode/locality checks do not confirm current residence. Handover and warranty start dates are not address-update dates.`}});
}
s.getRange('A8:A45').setNumberFormat('@');
s.getRange('E8:E45').setNumberFormat('@');
s.getRange('E8:E45').format.horizontalAlignment='center';
s.getRange('A7:G45').format.borders={insideHorizontal:{style:'thin',color:'#E5E5E5'}};
s.getRange('A47').values=[['Source notes']];s.getRange('A47').format.font.bold=true;
s.getRange('A48').values=[['Addresses are copied from the supplied Registration and SAP workbooks. Original postcode and address errors are retained in the source columns.']];
s.getRange('A49').values=[['Reason cells contain Excel Notes with original row references and postcode-check URLs (Australia Post; NZ Post and street sources where needed).']];
s.getRange('A50').values=[['Handover and warranty start dates do not establish when an address was updated. A valid postcode does not establish who currently lives there.']];
s.freezePanes.freezeRows(7);
wb.recalculate();
console.log((await wb.inspect({kind:'table',range:"'Address review'!A8:G10",tableMaxRows:3,tableMaxCols:7,maxChars:2200})).ndjson);
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!',options:{useRegex:true,maxResults:10},maxChars:600})).ndjson);
for(const [n,range]of[['1','A1:G19'],['2','A20:G32'],['3','A33:G51']]){
 const p=await wb.render({sheetName:s.name,range,scale:1,format:'png'});
 await fs.writeFile(path.join(out,`address_review_${n}.png`),new Uint8Array(await p.arrayBuffer()));
}
const filename=path.join(out,'Recall_Postcode_Address_Review.xlsx');
await(await SpreadsheetFile.exportXlsx(wb)).save(filename);
await fs.writeFile(path.join(out,'address_review_expected.json'),JSON.stringify(rows));
console.log(filename);
