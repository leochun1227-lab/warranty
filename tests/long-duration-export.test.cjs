const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
test('long duration export uses snapshot ticket age, preserves IDs, and excludes the 60-day boundary',()=>{
  const context=vm.createContext({
    clean:v=>String(v??'').trim(),periodEndDate:()=> '2026-09-30',
    rawCreatedOnDateByTicketId:()=>new Map([['0007','2026-07-30']]),
    teamRowsAtDate:end=>{
      assert.equal(end,'2026-09-30');
      return [
        {id:'0007',owner:'Employee A',entered:'2026-09-29'},
        {id:'0008',created:'2026-08-01'},
        {id:'0009',createdOn:'2026-07-31'},
        {id:'0010'},
        {id:'0007',owner:'Duplicate'}
      ];
    },
    criticalCreatedOnDateForRow:(r,map)=>r.createdOn||map.get(r.id)||'',
    daysBetweenISO:(start,end)=>start?(Date.parse(end)-Date.parse(start))/86400000:null
  });
  vm.runInContext(html.slice(html.indexOf('function longDurationTicketRows(){'),html.indexOf('async function exportLongDurationTickets(')),context);
  const rows=JSON.parse(JSON.stringify(context.longDurationTicketRows()));
  assert.deepEqual(rows.map(r=>[r.id,r.days]),[['0007',62],['0009',61]]);
  assert.equal(rows[0].owner,'Employee A');
});
