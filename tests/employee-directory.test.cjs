const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
const exited=['Leanne Pulford','Salvino Briganti','Ford Hapuku','Michael Scordia'];
function directory(raw={}){
  const c=vm.createContext({clean:v=>v==null?'':String(v).trim(),validAssignName:v=>!!String(v||'').trim(),employeeStatusMapping:{}});
  vm.runInContext(html.slice(html.indexOf('const DEFAULT_ACTIVE_EMPLOYEES='),html.indexOf('function loadEmployeeStatusMapping()')),c);
  vm.runInContext(html.slice(html.indexOf('function employeeStatusKey('),html.indexOf('function fbUrl(')),c);
  c.employeeStatusMapping=c.normalizeEmployeeDirectory(raw);
  return c;
}
test('confirmed departures override stale cached and remote active entries',()=>{
  const c=directory(Object.fromEntries(exited.map(name=>[name.toLowerCase(),{name,status:'active'}])));
  for(const name of exited){
    assert.equal(c.isEmployeeExited(name),true);
    assert.equal(c.teamAssignDisplayName(name),'Other');
    assert.equal(c.employeeWorkloadExportGroup(name),'OTHER - Other');
    assert.match(c.employeeWorkloadOtherReason(name),/exited/);
  }
  assert.equal(c.teamAssignDisplayName('Kylie Clayton'),'Kylie Clayton');
});
test('departed employees aggregate into Other without dropping or duplicating metrics',()=>{
  const c=directory(),grouped=new Map();
  const metrics=['total','removed','approved','unapproved'];
  for(const field of metrics){
    c.addTeamAssignMetric(grouped,'Other',field,8);
    exited.forEach((name,i)=>c.addTeamAssignMetric(grouped,name,field,i+1));
    c.addTeamAssignMetric(grouped,'Kylie Clayton',field,20);
    assert.equal(grouped.get('other')[field],18);
    assert.equal([...grouped.values()].reduce((sum,r)=>sum+r[field],0),38);
  }
  assert.equal(grouped.size,2);
  const audit=c.employeeWorkloadExportPrefix('Leanne Pulford');
  assert.equal(audit[1],'OTHER - Other');
  assert.equal(audit[3],'Leanne Pulford');
});
