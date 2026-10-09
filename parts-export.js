(function(root){
'use strict';
const xml=s=>String(s??'').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const encoder=new TextEncoder();
const crcTable=Uint32Array.from({length:256},(_,n)=>{for(let k=0;k<8;k++)n=n&1?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
function crc32(bytes){let crc=0xffffffff;for(const n of bytes)crc=crcTable[(crc^n)&255]^(crc>>>8);return (crc^0xffffffff)>>>0;}
async function zip(entries){
 const local=[],central=[];let offset=0;
 for(const [filename,source] of entries){
  const name=encoder.encode(filename),raw=encoder.encode(source),checksum=crc32(raw);
  const compressed=new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream('deflate-raw'))).arrayBuffer());
  const head=new Uint8Array(30+name.length),h=new DataView(head.buffer);h.setUint32(0,0x04034b50,true);h.setUint16(4,20,true);h.setUint16(8,8,true);h.setUint16(12,33,true);h.setUint32(14,checksum,true);h.setUint32(18,compressed.length,true);h.setUint32(22,raw.length,true);h.setUint16(26,name.length,true);head.set(name,30);
  const directory=new Uint8Array(46+name.length),d=new DataView(directory.buffer);d.setUint32(0,0x02014b50,true);d.setUint16(4,20,true);d.setUint16(6,20,true);d.setUint16(10,8,true);d.setUint16(14,33,true);d.setUint32(16,checksum,true);d.setUint32(20,compressed.length,true);d.setUint32(24,raw.length,true);d.setUint16(28,name.length,true);d.setUint32(42,offset,true);directory.set(name,46);
  local.push(head,compressed);central.push(directory);offset+=head.length+compressed.length;
 }
 const length=central.reduce((n,c)=>n+c.length,0),end=new Uint8Array(22),e=new DataView(end.buffer);e.setUint32(0,0x06054b50,true);e.setUint16(8,entries.length,true);e.setUint16(10,entries.length,true);e.setUint32(12,length,true);e.setUint32(16,offset,true);
 return new Blob([...local,...central,end],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
}
function letters(n){let s='';do{s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26)-1;}while(n>=0);return s;}
async function workbook(sheets){
 const ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main',rels='http://schemas.openxmlformats.org/officeDocument/2006/relationships';
 const files=[['[Content_Types].xml','<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+sheets.map((_,i)=>'<Override PartName="/xl/worksheets/sheet'+(i+1)+'.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>').join('')+'</Types>'],
 ['_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="'+rels+'/officeDocument" Target="xl/workbook.xml"/></Relationships>'],
 ['xl/workbook.xml','<workbook xmlns="'+ns+'" xmlns:r="'+rels+'"><sheets>'+sheets.map((s,i)=>'<sheet name="'+xml(s.name)+'" sheetId="'+(i+1)+'" r:id="rId'+(i+1)+'"/>').join('')+'</sheets></workbook>'],
 ['xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+sheets.map((_,i)=>'<Relationship Id="rId'+(i+1)+'" Type="'+rels+'/worksheet" Target="worksheets/sheet'+(i+1)+'.xml"/>').join('')+'<Relationship Id="styles" Type="'+rels+'/styles" Target="styles.xml"/></Relationships>'],
 ['xl/styles.xml','<styleSheet xmlns="'+ns+'"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEDF2F7"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>']];
 sheets.forEach((s,i)=>{
  if(s.rows.length>1048576)throw Error('Export exceeds the Excel row limit');
  const rows=s.rows.map((row,y)=>'<row r="'+(y+1)+'">'+row.map((v,x)=>{const cell='<c r="'+letters(x)+(y+1)+'" s="'+(y===0?1:2)+'"';if(typeof v==='number'&&Number.isFinite(v))return cell+'><v>'+v+'</v></c>';if(String(v??'').length>32767)throw Error('An Issue description exceeds the Excel cell limit');return cell+' t="inlineStr"><is><t xml:space="preserve">'+xml(v)+'</t></is></c>';}).join('')+'</row>').join('');
  files.push(['xl/worksheets/sheet'+(i+1)+'.xml','<worksheet xmlns="'+ns+'"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'+s.rows[0].map((name,x)=>'<col min="'+(x+1)+'" max="'+(x+1)+'" width="'+(/description/i.test(name)?55:/Subcategory|Product|Dealer|Repairer/.test(name)?25:20)+'" customWidth="1"/>').join('')+'</cols><sheetData>'+rows+'</sheetData><autoFilter ref="A1:'+letters(s.rows[0].length-1)+s.rows.length+'"/></worksheet>']);
 });return zip(files);
}
async function unpack(value,entry,allTickets=false){
 if(typeof value!=='string'||!globalThis.DecompressionStream)throw Error('This browser cannot read compressed export details. Please use Edge or Chrome.');
 const bytes=Uint8Array.from(atob(value),c=>c.charCodeAt(0));
 const reader=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip')).getReader();const chunks=[];let length=0;
 while(true){const {done,value}=await reader.read();if(done)break;length+=value.length;if(length>2*1024*1024||length>entry[6]){await reader.cancel();throw Error('Export detail exceeds its expected size');}chunks.push(value);}
 const raw=new Uint8Array(length);let offset=0;for(const c of chunks){raw.set(c,offset);offset+=c.length;}
 const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',raw))].map(b=>b.toString(16).padStart(2,'0')).join('');
 if(length!==entry[6]||hash!==entry[5])throw Error('Export detail failed integrity validation');
 const data=JSON.parse(new TextDecoder().decode(raw));
 if(allTickets){
  if(data.schema!=='failure-all-tickets-v1'||data.type!==entry[0]||data.month!==entry[1]||!Array.isArray(data.tickets)||data.tickets.length!==entry[4])throw Error('Invalid Created On Ticket shard');
  for(const r of data.tickets){
   if(!Array.isArray(r)||r.length!==13||r.slice(0,10).some(v=>typeof v!=='string')||!Array.isArray(r[10])||!r[10].length||!Array.isArray(r[11])||r[10].length!==r[11].length||r[10].some(v=>!/^(?:Z\d{3}|9997)$/.test(v))||r[11].some(v=>typeof v!=='string')||!Number.isSafeInteger(r[12])||r[12]<0)throw Error('Invalid Created On Ticket row');
   if(r[9]!==''&&(!/^-?\d+\.\d{2}$/.test(r[9])||!Number.isSafeInteger(Math.round(Number(r[9])*100))))throw Error('Invalid parts amount');
  }
  return data;
 }
 if(!['failure-export-shard-v1','failure-export-shard-v2'].includes(data.schema)||data.type!==entry[0]||data.month!==entry[1]||data.category!==entry[2]||!Array.isArray(data.tickets)||!Array.isArray(data.issues)||data.issues.length!==entry[4])throw Error('Export detail does not match this view');
 if(data.tickets.some(r=>!Array.isArray(r)||r.length!==(data.schema==='failure-export-shard-v2'?10:9)||r.some(v=>typeof v!=='string'))||data.issues.some(r=>!Array.isArray(r)||r.length!==10||!Number.isInteger(r[0])||!data.tickets[r[0]]||r.slice(1).some(v=>typeof v!=='string')))throw Error('Invalid export rows');
 if(data.schema==='failure-export-shard-v1')data.tickets.forEach(r=>r.push(''));
 if(data.tickets.some(r=>r[9]!==''&&(!/^-?\d+\.\d{2}$/.test(r[9])||!Number.isSafeInteger(Math.round(Number(r[9])*100)))))throw Error('Invalid Ticket parts amount');
 return data;
}
async function build({summary,scope,month,metric,year,api,read,cache,onProgress}){
 const version=summary.exportVersion;if(!/^[a-f0-9]{32}$/.test(version||''))throw Error('Ticket details have not been published for this snapshot yet. Refresh and try again.');
 const pendingCache=[];
 const load=async(path)=>{const key='failure-export:'+version+':'+path;let cached;try{cached=await cache?.getPageRecord(key);}catch{}if(cached?.value)return cached.value;const value=await read('exports/'+version+'/'+path);if(value==null)throw Error('Export details unavailable for this snapshot');pendingCache.push([key,value]);return value;};
 const manifest=JSON.parse(await load('manifest'));
 if(manifest.schema!=='failure-export-v1'||manifest.version!==version||!Array.isArray(manifest.shards))throw Error('Invalid export manifest');
 const types=['Z006','Z005'].filter(t=>scope==='both'||scope===t),label=t=>t==='Z006'?'In Field':'Pre Delivery';const ranks=new Map();
 const dimension=summary.categoryDimension==='issue_position'?'Issue Position':'Subcategory';
 const displayed=api.top10(summary,scope,month,metric).rows;
 const complete=summary.allCreatedTickets===true;
 if(complete&&manifest.allCreatedTickets!==true)throw Error('All Created On Ticket details are missing');
 const categories=complete?[...new Map(summary.groups.filter(g=>types.includes(g.ticketType)&&api.matchesPeriod(g.month,month)).map(g=>[g.subcategoryCode,{code:g.subcategoryCode,name:g.subcategoryName}])).values()]:displayed;
 for(const t of types)categories.forEach(r=>{const i=displayed.findIndex(d=>d.code===r.code);
  const groups=summary.groups.filter(g=>g.ticketType===t&&g.subcategoryCode===r.code&&api.matchesPeriod(g.month,month));
  ranks.set(t+'|'+r.code,{...r,rank:i<0?'':i+1,type:t,tickets:groups.reduce((n,g)=>n+g.ticketCount,0),issues:groups.reduce((n,g)=>n+g.issueCount,0),partsAmountCents:groups.reduce((n,g)=>n+(g.partsAmountCents||0),0),partsAmountKnownTickets:groups.reduce((n,g)=>n+(g.partsAmountKnownTickets||0),0),hasAmounts:groups.some(g=>g.partsAmountKnownTickets!=null)});
 });
 const keys=new Set();
 const entries=manifest.shards.filter(e=>{
  if(!Array.isArray(e)||e.length!==7||!['Z006','Z005'].includes(e[0])||!/^(\d{4}-\d{2}|unknown)$/.test(e[1])||!/^(?:Z\d{3}|9997)$/.test(e[2])||!/^Z\d{3}_(?:Z\d{3}|9997)\/(?:\d{4}-\d{2}|unknown)_\d+$/.test(e[3])||!Number.isSafeInteger(e[4])||e[4]<1||!/^[a-f0-9]{64}$/.test(e[5])||!Number.isSafeInteger(e[6])||e[6]>2*1024*1024||keys.has(e[3]))throw Error('Invalid export manifest entry');keys.add(e[3]);
  return ranks.has(e[0]+'|'+e[2])&&api.matchesPeriod(e[1],month);
 });
 const buckets=new Map(),issues=[],uniqueIssues=new Set(),groupLoads=new Map();let next=0,done=0;
 const readShard=async e=>{if(complete&&manifest.periodGroups){const key=e[0]+'_'+e[1];if(!groupLoads.has(key))groupLoads.set(key,load('periods/'+key));const group=await groupLoads.get(key);const value=group?.[e[3].replace('/','_')];if(typeof value!=='string')throw Error('Export month details unavailable');return value;}if(month!=='all')return load('shards/'+e[3]);const [group,key]=e[3].split('/');if(!groupLoads.has(group))groupLoads.set(group,load('shards/'+group));const value=await groupLoads.get(group);if(!value||typeof value!=='object'||typeof value[key]!=='string')throw Error('Export category details unavailable');return value[key];};
 await Promise.all(Array.from({length:Math.min(6,entries.length)},async()=>{while(next<entries.length){const e=entries[next++];const data=await unpack(await readShard(e),e);const rank=ranks.get(data.type+'|'+data.category);
  for(const row of data.issues){const t=data.tickets[row[0]],id=data.type+'|'+t[0]+'|'+row[1];if(uniqueIssues.has(id))throw Error('Duplicate Issue in export');uniqueIssues.add(id);
   if((t[1].slice(0,7)||'unknown')!==data.month)throw Error('Ticket date does not match export month');
   const key=data.type+'|'+data.category+'|'+t[0];let bucket=buckets.get(key);if(!bucket){bucket={rank,ticket:t,ids:[],descriptions:[]};buckets.set(key,bucket);}bucket.ids.push(row[1]);bucket.descriptions.push(row[4]);
   issues.push([label(data.type),rank.rank,rank.name,t[0],t[1],...row.slice(1),rank.code]);
  }onProgress(++done,entries.length);
 }}));
 const ticketRows=[...buckets.values()].sort((a,b)=>a.rank.type.localeCompare(b.rank.type)||a.rank.rank-b.rank.rank||a.ticket[0].localeCompare(b.ticket[0]));
 for(const [key,r] of ranks){const rows=ticketRows.filter(b=>b.rank===r);if(rows.length!==r.tickets||rows.reduce((n,b)=>n+b.ids.length,0)!==r.issues)throw Error('Ticket details do not reconcile with the displayed Top 10');if(r.hasAmounts){const known=rows.filter(b=>b.ticket[9]!=='');if(known.length!==r.partsAmountKnownTickets||known.reduce((n,b)=>n+Math.round(Number(b.ticket[9])*100),0)!==r.partsAmountCents)throw Error('Parts amounts do not reconcile with the displayed Top 10');}}
 issues.sort((a,b)=>a[0].localeCompare(b[0])||a[1]-b[1]||a[3].localeCompare(b[3])||a[5].localeCompare(b[5]));
 const allTickets=[],bucketsByTicket=new Map();
 for(const b of ticketRows){const key=b.rank.type+'|'+b.ticket[0];if(!bucketsByTicket.has(key))bucketsByTicket.set(key,[]);bucketsByTicket.get(key).push(b);}
 if(complete){
  if(!Array.isArray(manifest.ticketShards))throw Error('Missing Created On Ticket manifest');
  const ticketKeys=new Set(),seenTickets=new Set();
  const selected=manifest.ticketShards.filter(e=>{
   if(!Array.isArray(e)||e.length!==6||!['Z006','Z005'].includes(e[0])||!/^(\d{4}-\d{2}|unknown)$/.test(e[1])||!/^tickets\/Z\d{3}_(?:\d{4}-\d{2}|unknown)_\d+$/.test(e[2])||ticketKeys.has(e[2])||!Number.isSafeInteger(e[3])||e[3]<1||!/^[a-f0-9]{64}$/.test(e[4])||!Number.isSafeInteger(e[5])||e[5]>2*1024*1024)throw Error('Invalid Created On Ticket manifest');ticketKeys.add(e[2]);
   return types.includes(e[0])&&api.matchesPeriod(e[1],month);
  });
  let cursor=0;
  await Promise.all(Array.from({length:Math.min(6,selected.length)},async()=>{while(cursor<selected.length){const e=selected[cursor++],data=await unpack(await load('shards/'+e[2]),[e[0],e[1],'',e[2],e[3],e[4],e[5]],true);
   for(const r of data.tickets){
    if(seenTickets.has(r[0])||(r[1].slice(0,7)||'unknown')!==e[1])throw Error('Created On Ticket identity/date mismatch');seenTickets.add(r[0]);
    const matching=bucketsByTicket.get(e[0]+'|'+r[0])||[];
    if(matching.reduce((n,b)=>n+b.ids.length,0)!==r[12]||matching.some(b=>!r[10].includes(b.rank.code)||JSON.stringify(b.ticket)!==JSON.stringify(r.slice(0,10)))||(r[12]===0&&(r[10].length!==1||r[10][0]!==(summary.otherCategoryCode||'Z072'))))throw Error('Created On Ticket details do not reconcile');
    allTickets.push({type:e[0],row:r});
   }
  }}));
  if(allTickets.length!==api.top10(summary,scope,month,'tickets').total||ticketRows.some(b=>!seenTickets.has(b.ticket[0]))||allTickets.reduce((n,t)=>n+t.row[12],0)!==api.top10(summary,scope,month,'issues').total)throw Error('Export does not match the Created On total');
  allTickets.sort((a,b)=>a.type.localeCompare(b.type)||a.row[0].localeCompare(b.row[0]));
 }
 const months=[['Ticket type','Year',dimension,'Ticket creation month','Count by','Count','Data updated','Ticket Parts Amount (AUD)','Tickets with parts amount']];
 for(const category of displayed)for(const r of api.monthly(summary,scope,year,metric,category.code))months.push([scope==='both'?'Both':label(scope),year,category.name,r.month,metric,r.count,summary.generatedAt,r.partsAmountKnownTickets?r.partsAmountCents/100:'',r.partsAmountKnownTickets]);
 await Promise.all(pendingCache.map(([key,value])=>Promise.resolve(cache?.setPage(key,version,value)).catch(()=>{})));
 return workbook([
  {name:'Top 10',rows:api.exportRows(summary,scope,month,metric)},
  {name:'Monthly',rows:months},
  {name:'Tickets',rows:complete?[['Ticket type','Ticket ID','Ticket Created On','Status','Dealer','Repairer','Serial ID','Chassis','Registered Product','Product','Ticket Parts Amount (AUD)',dimension+' codes',dimension+' categories','Issue count'],...allTickets.map(({type,row:r})=>[label(type),...r.slice(0,9),r[9]===''?'':Number(r[9]),r[10].join('; '),r[11].join('; '),r[12]])]:[['Ticket type','Rank',dimension,'Ticket ID','Ticket Created On','Status','Dealer','Repairer','Serial ID','Chassis','Registered Product','Product','Ticket Parts Amount (AUD)','Matched Issue count','Issue IDs',dimension+' code'],...ticketRows.map(b=>[label(b.rank.type),b.rank.rank,b.rank.name,...b.ticket.slice(0,9),b.ticket[9]===''?'':Number(b.ticket[9]),b.ids.length,b.ids.join('; '),b.rank.code])]},
  {name:'Issues',rows:[['Ticket type','Rank',dimension,'Ticket ID','Ticket Created On','Issue ID','Issue position','Issue position text','Issues description','System Subcategory','System Subcategory text','Subcategory reason','Subcategory reason text','Classification source',dimension+' code'],...issues]},
  ...(complete?[{name:'Summary',rows:[['Ticket type','Ticket creation period','Created On Tickets','Issues','Date basis'],[scope==='both'?'Both':label(scope),api.periodLabel(month),allTickets.length,issues.length,'Ticket CreatedOn; unclassified and no-Issue Tickets included in Other']]}]:[])
 ]);
}
root.FailurePartsExport={build,workbook,unpack};
})(typeof window!=='undefined'?window:globalThis);
