import fs from 'node:fs/promises';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const dir='C:/Users/Leo.Li/Documents/GitHub/warranty/outputs/01a15cc5_iq09_compare';
const p=JSON.parse(await fs.readFile(`${dir}/comparison.json`,'utf8'));
const live=JSON.parse(await fs.readFile(`${dir}/live_check.json`,'utf8'));
const wb=Workbook.create();
const summary=[['核对项目','数量'],['对方表记录数',p.summary.userRows],['HANA结果表记录数',p.summary.hanaRows],['共同设备数',p.summary.sharedEquipment],['共同字段一致的设备数',p.summary.identicalEquipment],['共同设备中有字段差异的设备数',p.summary.changedEquipment],['仅对方表有',p.summary.onlyUser],['仅HANA结果表有',p.summary.onlyHana],['核对共同字段数',p.summary.comparedFields],['字段差异数',p.summary.fieldDifferences],['当前HANA同范围记录数',live.currentCount]];
const diffHeaders=['设备编号','物料','物料描述','序列号','对方表行号','对方工厂','对方库位','对方状态','对方销售订单','当前HANA工厂','当前HANA库位','当前HANA状态','当前库存分配记录','状态变更日期','状态变更时间（SAP系统）'];
const diff=p.onlyUser.map(r=>{
 const id=String(r.Equipment).replace(/^0+/,'');
 const current=live.live.rows.find(x=>x[0].replace(/^0+/,'')===id);
 const event=live.statusHistory.rows.find(x=>x[0]===`IE${current[0]}`&&x[2]==='AVLB'&&x[5]==='');
 const date=event[3];const time=event[4];
 return [id,r.Material,r['Material Description'],r['Serial Number'],r._row,r.Plant,r['Storage location'],r['System status'],r['Sales Order'],current[4],current[5],current[11],current[4]===null?'无':'有',new Date(`${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6,8)}T00:00:00Z`),`${time.slice(0,2)}:${time.slice(2,4)}:${time.slice(4,6)}`];
});
const fieldRows=[['对方表字段','HANA结果表字段','核对记录数','一致记录数','差异记录数'],...p.fieldMap.map(([a,b])=>[a,b,p.summary.sharedEquipment,p.summary.sharedEquipment-p.differences.filter(r=>r[3]===b).length,p.differences.filter(r=>r[3]===b).length])];
function add(name,values){
 const s=wb.worksheets.add(name);
 s.getRangeByIndexes(0,0,values.length,values[0].length).values=values;
 s.getUsedRange().format.font={name:'Arial',size:10};
 s.getUsedRange().format.autofitColumns();
 s.getRangeByIndexes(0,0,1,values[0].length).format.rowHeight=26;
 s.freezePanes.freezeRows(1);
 return s;
}
const s=add('核对结果',summary);s.getRange('A:A').format.columnWidth=43;s.getRange('B:B').format.columnWidth=14;
const d=add('差异明细',[diffHeaders,...diff]);
d.getRange('A:A').format.columnWidth=17;d.getRange('B:B').format.columnWidth=17;
d.getRange('C:C').format.columnWidth=49;d.getRange('D:D').format.columnWidth=24;
d.getRange('E:M').format.columnWidth=18;d.getRange('N:O').format.columnWidth=27;
d.getRange(`A2:D${diff.length+1}`).setNumberFormat('@');
d.getRange(`F2:M${diff.length+1}`).setNumberFormat('@');
d.getRange(`N2:N${diff.length+1}`).setNumberFormat('yyyy-mm-dd');
const f=add('字段核对',fieldRows);f.getRange('A:B').format.columnWidth=34;f.getRange('C:E').format.columnWidth=17;
wb.recalculate();
console.log((await wb.inspect({kind:'table',range:'核对结果!A1:B12',include:'values',tableMaxRows:12,tableMaxCols:2,maxChars:2000})).ndjson);
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!',options:{useRegex:true,maxResults:10},maxChars:600})).ndjson);
for(const [name,sheetName,range] of [['summary','核对结果','A1:B12'],['diff','差异明细','A1:O3'],['fields','字段核对','A1:E14']]){
 const image=await wb.render({sheetName,range,scale:1,format:'png'});
 await fs.writeFile(`${dir}/${name}.png`,new Uint8Array(await image.arrayBuffer()));
}
await(await SpreadsheetFile.exportXlsx(wb)).save(`${dir}/IQ09_对比结果_20261022.xlsx`);
await fs.writeFile(`${dir}/expected.json`,JSON.stringify({核对结果:summary,差异明细:[diffHeaders,...diff],字段核对:fieldRows}));
console.log('Exported comparison workbook');
