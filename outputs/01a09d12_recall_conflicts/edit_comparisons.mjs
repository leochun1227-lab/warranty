import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile, FileBlob } from '@oai/artifact-tool';
const out=path.dirname(fileURLToPath(import.meta.url));
const file=path.join(out,'Recall_Postcode_Conflicts.xlsx');
const results=JSON.parse(await fs.readFile(path.resolve(out,'../recall_postcode_20260914/match_results.json'),'utf8')).filter(r=>r.status==='conflicting_postcodes');
const oldRows=JSON.parse(await fs.readFile(path.join(out,'expected_rows.json'),'utf8'));
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(file));
const s=wb.worksheets.getItem('Postcode conflicts');
const sourceNames=['Product_Registrations_Till_17072026.xlsx','SAPAnalyticsReport(Z578DB6F34E1CDB92C9D1CE).xlsx'];
const edited=[];
for(let i=0;i<results.length;i++){
  const r=results[i], excelRow=i+6;
  if(oldRows[i][0]!==r.ticket_id)throw new Error('Row order mismatch');
  const details=sourceNames.map(source=>[...new Set(r.evidence.filter(e=>e.source===source&&e.postcode).map(e=>`${(e.name||[]).join(' / ') || '(name missing)'}: ${e.postcode}`))]);
  const codes=sourceNames.map(source=>[...new Set(r.evidence.filter(e=>e.source===source&&e.postcode).map(e=>e.postcode))].sort());
  let note=`Registration: ${codes[0].join(' / ')}\nSAP: ${codes[1].join(' / ')}`;
  if(codes[0].length>1)note+='\nRegistration rows also disagree.';
  const values=[details[0].join('\n'),details[1].join('\n'),note];
  s.getRange(`D${excelRow}:F${excelRow}`).values=[values];
  // Fit line breaks and ordinary wrapping without reducing the font size.
  const lineCounts=values.map((value,j)=>value.split('\n').reduce((n,line)=>n+Math.max(1,Math.ceil(line.length/(j===2?42:35))),0));
  s.getRange(`A${excelRow}:F${excelRow}`).format.rowHeight=Math.max(38,Math.max(...lineCounts)*13+10);
  edited.push([...oldRows[i].slice(0,3),...values]);
}
s.getRange('D5:F5').values=[['Registration: customer / postcode','SAP: customer / postcode','Postcode comparison']];
s.getRange('D5:F43').format.wrapText=true;
s.getRange('D5:E43').format.columnWidth=37;
s.getRange('F5:F43').format.columnWidth=43;
s.getRange('D6:F43').format.horizontalAlignment='left';
s.getRange('A5:F5').format.rowHeight=32;
s.getRange('A49').values=[['Each source shows customer name and postcode. Duplicate source records are listed once. Review date: 14 Sep 2026.']];
wb.recalculate();
console.log((await wb.inspect({kind:'table',range:"'Postcode conflicts'!A12:F16",tableMaxRows:5,tableMaxCols:6,maxChars:2800})).ndjson);
for(const [label,range] of [['top','A1:F24'],['bottom','A25:F50']]){
 const png=await wb.render({sheetName:s.name,range,scale:1,format:'png'});
 await fs.writeFile(path.join(out,`comparison_${label}.png`),new Uint8Array(await png.arrayBuffer()));
}
await(await SpreadsheetFile.exportXlsx(wb)).save(path.join(out,'Recall_Postcode_Conflicts_Compared.xlsx'));
await fs.writeFile(path.join(out,'expected_comparison_rows.json'),JSON.stringify(edited));
console.log('Updated 38 comparisons.');
