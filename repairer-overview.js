/* Presentation layer: reuse the established period, cost and repeat calculations. */
function overviewRankRows(rows,metric,stateCode,limit=8){
  return rows.filter(row=>!stateCode||row.state===stateCode).slice()
    .sort((a,b)=>Number(b[metric]||0)-Number(a[metric]||0)||String(a.repairName||a.state).localeCompare(String(b.repairName||b.state)))
    .slice(0,limit);
}
function overviewRepeatDetails(rows,category,repairId){
  return rows.filter(row=>row.categoryLabel===category&&(!repairId||String(row.repairId)===String(repairId)))
    .map(row=>{const t=row.ticket||{};return [String(t.ticketId||''),row.chassis||t.chassisNumber||'',t.created||'',t.ticketStatus||'',Number(t.approvedCost||0)];});
}
function overviewRepairMatches(info,row){
  if(info.repairId&&info.repairId===row.repairId)return true;
  const state=value=>String(value.state||String(value.repairId||'').split('|').slice(1).pop()||'').trim().toUpperCase();
  const name=value=>String(value.repairName||'').replace(/\s*\((?:QLD|NSW|VIC|WA|SA|TAS|ACT|NT|NZ|AU)\)\s*$/i,'').trim().toLowerCase();
  return !!name(info)&&!!state(info)&&state(info)===state(row)&&name(info)===name(row);
}
(function(){
  'use strict';
  const main=document.querySelector('main.main');
  if(!main)return;
  document.body.classList.add('repairer-overview-page');
  const header=main.querySelector('.repairPageHeader');
  for(const child of [...main.children])if(child!==header&&child.id!=='loadError')child.classList.add('repairLegacy');
  header.querySelector('h1').textContent='Repairer';
  header.querySelector('h1').title='Excludes Customer / Self-Repair';
  const overview=document.createElement('div');overview.className='roGrid';
  overview.innerHTML=`
    <section class="roPanel"><div class="roHead"><h2>State Comparison</h2><div class="roActions"><button id="roAllStates" type="button">All states</button><button id="roMapToggle" type="button" aria-pressed="true">Bars</button><select id="roStateMetric" aria-label="State comparison metric"><option value="confirmed_cost">Cost</option><option value="ticket_count">Tickets</option><option value="avg_confirmed_cost">Avg Cost</option></select></div></div><div class="roCaption">Select a state to filter Top Repairers</div><div id="roStateBars" class="roBars" hidden></div><div id="roMap"></div></section>
    <section class="roPanel"><div class="roHead"><h2>Top Repairers <span id="roStateScope"></span></h2><select id="roShopMetric" aria-label="Repairer ranking metric"><option value="confirmed_cost">Cost</option><option value="ticket_count">Tickets</option><option value="avg_confirmed_cost">Avg Cost</option></select></div><div class="roCaption" id="roShopCaption">Top 8 · select a repairer for ticket details</div><div id="roShopBars" class="roBars"></div></section>
    <section class="roPanel"><div class="roHead"><h2>Repeated Repair Scenarios</h2><button id="roRepeatExport" type="button">Export Excel</button></div><div class="roCaption">All states · select a scenario to compare repairers</div><div id="roScenarios" class="roScenarios"></div><p class="roNote">Repeated requests are a review signal; they do not by themselves indicate poor repair quality.</p></section>
    <section class="roPanel"><div class="roHead"><h2>Repairers with Repeat Requests</h2></div><div id="roRepeatScope" class="roCaption"></div><div id="roRepeatBars" class="roBars"></div><p class="roNote">Repeat / total requests · rate. Ranked by repeat count.</p></section>`;
  main.append(overview);
  const map=document.getElementById('weekMapCard');if(map)document.getElementById('roMap').append(map);
  const dialog=document.createElement('dialog');dialog.className='roDialog';
  dialog.innerHTML=`<div class="roHead"><div><h2 id="roDetailTitle">Ticket details</h2><p id="roDetailScope" class="roCaption"></p></div><div class="roActions"><button id="roDetailExport" type="button" disabled>Download Excel</button><button id="roDetailClose" type="button" aria-label="Close ticket details">Close</button></div></div><div id="roDetailBody" class="roDetailBody" aria-live="polite"></div>`;
  document.body.append(dialog);
  const el=id=>document.getElementById(id);
  let stateCode='',stateMetric='confirmed_cost',shopMetric='confirmed_cost',scenarioKey='',queued=false,detailVersion=0,detailRows=[],detailName='';
  const amount=(v,metric)=>metric==='ticket_count'?num(v):money(v);
  const nameOf=row=>repairerDisplayNameFromRow(row)||row.repairName||'Unknown repairer';
  const empty=(box,message)=>{box.innerHTML=`<p class="roEmpty">${esc(message)}</p>`;};
  function bars(box,rows,{value,label,formatted,meta=()=>'',active=()=>false,onClick}){
    if(!rows.length){empty(box,repairInitialLoading?'Loading…':'No matching data for this period.');return;}
    const max=Math.max(1,...rows.map(value));
    box.innerHTML=rows.map((row,i)=>`<button type="button" class="roBar ${active(row)?'selected':''}" data-index="${i}" title="${esc(label(row))}"><span class="roBarName">${esc(label(row))}</span><span class="roBarTrack"><span style="width:${Math.max(0,value(row))/max*100}%"></span></span><span class="roBarNumber">${esc(formatted(row))}</span>${meta(row)?`<small class="roBarMeta">${esc(meta(row))}</small>`:''}</button>`).join('');
    box.querySelectorAll('button').forEach((btn,i)=>btn.onclick=()=>onClick(rows[i]));
  }
  function render(){
    const states=repairOverviewStateRows();
    if(stateCode&&!states.some(row=>row.state===stateCode))stateCode='';
    el('roStateScope').textContent=stateCode?`(${stateCode})`:'(All states)';
    el('roAllStates').classList.toggle('selected',!stateCode);
    bars(el('roStateBars'),overviewRankRows(states,stateMetric,'',20),{
      value:r=>Number(r[stateMetric]||0),label:r=>stateLabel(r.state),formatted:r=>amount(r[stateMetric]||0,stateMetric),active:r=>r.state===stateCode,
      onClick:r=>{stateCode=stateCode===r.state?'':r.state;selectedRegionCode=r.state;renderWeekPanel();schedule();}
    });
    const shops=repairOverviewRepairerRows();
    bars(el('roShopBars'),overviewRankRows(shops,shopMetric,stateCode),{
      value:r=>Number(r[shopMetric]||0),label:nameOf,formatted:r=>amount(r[shopMetric]||0,shopMetric),
      meta:r=>shopMetric==='avg_confirmed_cost'?`${num(r.ticket_count||0)} tickets`:'',onClick:r=>openDetails(r)
    });
    const categories=Array.isArray(workingChassis?.categories)?workingChassis.categories:[];
    if(!repairDetailsLoaded||!claimTrendUnapprovedCounts.loaded){
      const message=repairDetailsError?'Repeat details could not be loaded.':!claimTrendUnapprovedCounts.loaded&&!claimTrendCountsPromise?'Repeat-rate totals are unavailable.':'Loading repeat request details…';
      empty(el('roScenarios'),message);empty(el('roRepeatBars'),message);
      el('roRepeatScope').textContent='';
      if(repairDetailsError||(!claimTrendUnapprovedCounts.loaded&&!claimTrendCountsPromise)){
        const retry=document.createElement('button');retry.type='button';retry.textContent='Retry';retry.onclick=()=>{refreshRepairClaimCounts();repairDetailsError='';chassisRepeatDetailRefreshStarted=false;requestChassisRepeatDetailRefresh();schedule();};el('roScenarios').append(retry);
      }
      return;
    }
    if(!categories.length){empty(el('roScenarios'),'No repeated requests for this period.');empty(el('roRepeatBars'),'No repeated requests for this period.');el('roRepeatScope').textContent='';return;}
    if(!categories.some(row=>row.key===scenarioKey))scenarioKey=categories[0].key;
    const totals=categories.map(row=>chassisRepeatCategoryTotals(row,workingChassis));
    const max=Math.max(1,...totals.map(t=>t.repeatTickets));
    el('roScenarios').innerHTML=categories.map((row,i)=>{
      const t=totals[i],short=row.key==='all_approved'?'All Approved':row.key==='unapproved_until_approved'?'Partially Approved':row.label;
      const denominator=row.key==='all_approved'?'approved tickets':'approved + unapproved tickets';
      return `<button type="button" class="roScenario ${row.key===scenarioKey?'selected':''}" aria-pressed="${row.key===scenarioKey}"><span class="roScenarioHead"><span>${esc(short)}</span><b>${num(t.repeatTickets)}</b></span><span class="roBarTrack"><span style="width:${t.repeatTickets/max*100}%"></span></span><span class="roScenarioMeta">${num(t.repeatTickets)} / ${num(t.totalTickets)} · ${num(t.rate,1)}%</span><small>Denominator: ${esc(denominator)}</small></button>`;
    }).join('');
    el('roScenarios').querySelectorAll('button').forEach((b,i)=>b.onclick=()=>{scenarioKey=categories[i].key;render();});
    const scenario=categories.find(row=>row.key===scenarioKey);
    el('roRepeatScope').textContent=`${scenario.key==='all_approved'?'All Approved':'Partially Approved'} · All states · Top 6`;
    const repeaters=overviewRankRows(scenario.repairers||[],'repeatedTickets','',6);
    bars(el('roRepeatBars'),repeaters,{
      value:r=>Number(r.repeatedTickets||0),label:nameOf,formatted:r=>num(r.repeatedTickets||0),
      meta:r=>`${num(r.repeatedTickets||0)} / ${num(r.totalTickets||0)} requests · ${num(r.repeatRate||0,1)}%`,onClick:r=>openDetails(r,scenario)
    });
  }
  function schedule(){if(queued)return;queued=true;queueMicrotask(()=>{queued=false;render();});}
  async function openDetails(row,scenario){
    const version=++detailVersion,period=repairPeriodText();detailRows=[];detailName=nameOf(row);
    el('roDetailTitle').textContent=detailName;el('roDetailScope').textContent=`${scenario?scenario.label:'Approved cost tickets'} · ${period}`;
    el('roDetailExport').disabled=true;empty(el('roDetailBody'),'Loading ticket details…');if(!dialog.open)dialog.showModal();
    try{
      await ensureRepairDetailsLoaded();
      if(version!==detailVersion||!dialog.open)return;
      if(period!==repairPeriodText())throw Error('The period changed. Close this panel and select the repairer again.');
      if(scenario){
        detailRows=overviewRepeatDetails(workingChassis?.detailRows||[],scenario.label,row.repairId);
      }else{
        const entries=repairCostTicketEntries().filter(entry=>overviewRepairMatches(repairInfo(entry),row));
        detailRows=entries.map(entry=>[String(entry.id||''),normalizedChassis(entry)||'',repairEntryDateKey(entry)||'',clean(entry.ticket?.TicketStatusText||entry.ticket?.statusText||''),Number(openPoRepairCostFromTicket(entry.ticket)||0)]);
      }
      if(!detailRows.length){empty(el('roDetailBody'),'No matching ticket details found.');return;}
      el('roDetailScope').textContent+=` · ${num(detailRows.length)} ${detailRows.length===1?'ticket':'tickets'}`;
      const exactMoney=new Intl.NumberFormat('en-AU',{style:'currency',currency:'AUD'});
      el('roDetailBody').innerHTML=`<table><thead><tr>${['Ticket','Chassis','Decision Date','Status','Approved Cost'].map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${detailRows.map(row=>`<tr>${row.map((v,i)=>`<td>${esc(i===4?exactMoney.format(v):i===2?String(v).replace(/^(\d{4})-(\d{2})-(\d{2})$/,'$3/$2/$1'):v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
      el('roDetailExport').disabled=false;
    }catch(error){empty(el('roDetailBody'),error.message||'Unable to load details. Please close and try again.');}
  }
  el('roDetailExport').onclick=()=>exportWorkbook([{name:'Tickets',rows:[['Ticket','Chassis','Decision Date','Status','Approved Cost'],...detailRows]}],`repairer-${safeFileName(detailName)}-${repairPeriodFileSuffix()}`);
  el('roDetailClose').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{detailVersion++;});
  el('roStateMetric').onchange=e=>{stateMetric=e.target.value;weeklyMetric=stateMetric;renderWeekPanel();schedule();};
  el('roShopMetric').onchange=e=>{shopMetric=e.target.value;schedule();};
  el('roAllStates').onclick=()=>{stateCode='';schedule();};
  el('roMapToggle').onclick=()=>{const show=el('roMap').hidden;el('roMap').hidden=!show;el('roStateBars').hidden=show;el('roMapToggle').textContent=show?'Bars':'Map';el('roMapToggle').setAttribute('aria-pressed',String(show));if(show)renderWeekPanel();};
  map?.addEventListener('click',event=>{const target=event.target.closest('[data-state]');if(target){stateCode=target.dataset.state;schedule();}});
  el('roRepeatExport').onclick=()=>exportChassisRepeatExcel();
  // Existing asynchronous loaders keep owning calculations and failure/retry state.
  const oldWeek=renderWeekPanel,oldTop=renderTop20Panel,oldRepeat=renderChassisRepeatPanel;
  renderWeekPanel=function(...args){const result=oldWeek.apply(this,args);schedule();return result;};
  renderTop20Panel=function(...args){const result=oldTop.apply(this,args);schedule();return result;};
  renderChassisRepeatPanel=function(...args){const result=oldRepeat.apply(this,args);schedule();return result;};
  new MutationObserver(schedule).observe(el('loadError'),{attributes:true,childList:true,characterData:true,subtree:true});
  schedule();
})();
