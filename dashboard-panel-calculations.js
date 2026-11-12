// Server build only. This file is never loaded by index.html.
function buildRemainingStatusColumns(rows){
  const total=rows.reduce((sum,row)=>sum+Number(row.count||0),0);
  const maximum=Math.max(1,...rows.map(row=>Number(row.count||0)));
  return rows.map(row=>({...row,percent:total?row.count/total*100:0,heightPct:row.count/maximum*100}));
}
function buildDashboardPanelCache(){
  const previousClaim=activeClaim,previousMonth=activeMonth;
  const views={};
  try{
    for(const key of Object.keys(team.views||{})){
      activeClaim=key;
      const view=currentView(),latest=latestDateForView();
      const monthlyApprovals=buildEmployeeMonthlyApprovalData();
      const remainingStatus={};
      for(const {month,end} of monthlyApprovals.columns){
        const snapshot=periodSnapshotForBounds(monthStartISO(month),end);
        const daily=view.dailyCriticalRows||{};
        let source=null;
        if(Object.prototype.hasOwnProperty.call(daily,end))source=arrayFromMaybe(daily[end]);
        else if(snapshot?.currentCriticalRows!=null)source=arrayFromMaybe(snapshot.currentCriticalRows);
        else if(end===latest&&view.currentCriticalRows!=null)source=arrayFromMaybe(view.currentCriticalRows);
        let rows=null;
        if(source!==null){
          const counts=new Map(),ids=new Set();
          source.forEach(row=>{
            const id=clean(row.id||row.ticketId||row.TicketID);
            if(id&&ids.has(id))return;
            if(id)ids.add(id);
            const label=clean(row.status)||"Unknown";
            counts.set(label,(counts.get(label)||0)+1);
          });
          rows=Array.from(counts,([label,count])=>({label,count}));
        }else if(end===latest&&view.statusMix!=null){
          rows=arrayFromMaybe(view.statusMix).map(row=>({label:clean(row.label||row.status)||"Unknown",count:Number(row.count||row.value||0)}));
        }
        if(rows!==null){
          rows=rows.filter(row=>row.count>0).sort((a,b)=>b.count-a.count||a.label.localeCompare(b.label));
          remainingStatus[month]={end,rows:buildRemainingStatusColumns(rows)};
        }
      }
      views[key]={monthlyApprovals,remainingStatus};
    }
  }finally{activeClaim=previousClaim;activeMonth=previousMonth;}
  return {schema:1,generatedAt:team.generatedAt,views};
}
function buildEmployeeMonthlyApprovalData(){
  // Read calendar-month snapshots directly: never use the selected-period
  // fallback or cumulative employee totals for historical monthly counts.
  const latest=latestDateForView(),current=monthKeyFromDate(latest);
  const previous=monthKeyFromDate(addDaysISO(monthStartISO(current),-1));
  const prior=monthKeyFromDate(addDaysISO(monthStartISO(previous),-1));
  const months=new Set([...availableDashboardMonths(),current,previous,prior]);
  Object.keys(currentView().periodSnapshots||currentView().period_summaries||{}).forEach(key=>{
    const match=key.match(/^(\d{4}-\d{2})-01[|_](\d{4}-\d{2}-\d{2})$/);
    if(match&&match[2]===monthEndISO(match[1]))months.add(match[1]);
  });
  const names=employeeDirectoryNames().filter(name=>!isEmployeeExited(name)&&!isOtherTeamAssignRow(name));
  const employees=new Map(names.map(name=>[employeeStatusKey(name),{name,values:{}}]));
  const columns=Array.from(months).filter(month=>month<=current).sort().reverse().map(month=>{
    const end=month===current?latest:monthEndISO(month);
    const snapshot=periodSnapshotForBounds(monthStartISO(month),end);
    const known=!!snapshot&&(snapshot.employeeApprovalRows!=null||snapshot.summary?.approvedTickets===0);
    let total=0;
    if(known)arrayFromMaybe(snapshot.employeeApprovalRows).forEach(row=>{
      const name=teamAssignDisplayName(row.name||row.employee||row.owner),key=employeeStatusKey(name);
      const approved=Number(row.approved??row.approvedTickets??0)||0;
      if(!employees.has(key))employees.set(key,{name,values:{}});
      const employee=employees.get(key);
      employee.values[month]=(employee.values[month]||0)+approved;
      total+=approved;
    });
    return {month,end,known,total:known?total:null,average:known&&names.length?total/names.length:null,
      tone:month===current?"current":month===previous?"previous":month===prior?"prior":""};
  });
  const rows=Array.from(employees.values()).filter(row=>!isOtherTeamAssignRow(row.name)||Object.values(row.values).some(value=>value>0));
  rows.sort((a,b)=>Number(isOtherTeamAssignRow(a.name))-Number(isOtherTeamAssignRow(b.name))||a.name.localeCompare(b.name));
  rows.forEach(row=>columns.forEach(col=>{row.values[col.month]=col.known?(row.values[col.month]||0):null;}));
  const totals={name:"Team total",values:Object.fromEntries(columns.map(col=>[col.month,col.total]))};
  const average={name:"Average / employee",values:Object.fromEntries(columns.map(col=>[col.month,col.average]))};
  [...rows,totals,average].forEach(row=>{row.change=employeeMonthlyChange(row.values[current],row.values[previous]);});
  const data={columns,rows,current,previous,prior,latest,headcount:names.length,totals,average};
  data.exportRows=buildEmployeeMonthlyApprovalExportRows(data);
  return data;
}
function employeeMonthlyChange(current,previous){
  if(current==null||previous==null)return {text:"—",tone:"flat"};
  if(previous===0)return {text:current===0?"0.0%":"N/A",tone:"flat"};
  const value=(current-previous)/previous*100;
  return {text:`${value>0?"+":""}${value.toFixed(1)}%`,tone:value>0?"up":value<0?"down":"flat"};
}
function buildEmployeeMonthlyApprovalExportRows(data){
  const makeRow=(name,values)=>[name,...data.columns.map(col=>values[col.month]??""),employeeMonthlyChange(values[data.current],values[data.previous]).text];
  const totals=Object.fromEntries(data.columns.map(col=>[col.month,col.total]));
  const averages=Object.fromEntries(data.columns.map(col=>[col.month,col.average]));
  return [["Employee",...data.columns.map(col=>`${col.month}${col.month===data.current?" (MTD)":""}`),`${data.current} vs ${data.previous} %`],
    ...data.rows.map(row=>makeRow(row.name,row.values)),makeRow("Team total",totals),makeRow(`Average / employee (${data.headcount} active employees)`,averages)];
}
