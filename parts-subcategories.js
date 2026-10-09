(function(){
  'use strict';
  const api=window.IssueSubcategoryData;
  const database='https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app/';
  const root='c4cTickets_test';
  const cache=window.WarrantyPageCache, cacheKey='issue-position-claim-startup:v3:'+root;
  const dataPath=api.basePath(root);
  const $=id=>document.getElementById(id), number=n=>new Intl.NumberFormat('en-AU').format(n);
  const money=cents=>new Intl.NumberFormat('en-AU',{style:'currency',currency:'AUD'}).format(cents/100);
  const shortMoney=cents=>new Intl.NumberFormat('en-AU',{style:'currency',currency:'AUD',notation:'compact',maximumFractionDigits:1}).format(cents/100);
  const amountText=(row,compact=false)=>row.partsAmountKnownTickets?(compact?shortMoney:money)(row.partsAmountCents)+(row.partsAmountKnownTickets<row.tickets?'*':''):'—';
  const amountHelp=row=>`Factory Parts + Repairer Parts; no labour. ${row.partsAmountKnownTickets} of ${row.tickets} Tickets have amounts. Counted once per Ticket within this category; amounts overlap across categories.`;
  let summary=null,snapshot=null,automation={},selectedMonth=String(api.reportingCalendar().year),scope='both',metric='tickets',loadId=0,cacheMode='checking',currentPending=0,trendYear='',exporting=false;
  const labels={both:'Both',Z006:'In Field',Z005:'Pre Delivery'};
  function element(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;}
  const tip=element('div','chart-tooltip');tip.id='chart-tooltip';tip.setAttribute('role','tooltip');tip.hidden=true;document.body.append(tip);
  function hideTooltip(){tip.hidden=true;document.querySelectorAll('[aria-describedby="chart-tooltip"]').forEach(b=>b.removeAttribute('aria-describedby'));}
  function showTooltip(button,row,month,event){
    tip.replaceChildren(element('div','tooltip-month',new Intl.DateTimeFormat('en-AU',{month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(month.month+'-01T00:00:00Z'))),element('div','tooltip-name',row.name));
    const line=element('div','tooltip-row');line.append(element('span','',metric==='tickets'?'Tickets':'Issues'),element('strong','',number(month.count)));tip.append(line);
    const amount=element('div','tooltip-row');amount.append(element('span','','Parts (AUD)'),element('strong','',amountText(month)));tip.append(amount);
    if(month.partsAmountKnownTickets<month.tickets)tip.append(element('div','tooltip-month',`${month.tickets-month.partsAmountKnownTickets} Tickets without amount`));
    tip.hidden=false;button.setAttribute('aria-describedby','chart-tooltip');
    const box=button.getBoundingClientRect(),x=event?.clientX??(box.left+box.width/2),y=event?.clientY??box.top;
    let left=x+14,top=y-tip.offsetHeight-12;if(left+tip.offsetWidth>innerWidth-8)left=x-tip.offsetWidth-14;if(top<8)top=y+18;
    tip.style.left=Math.max(8,Math.min(left,innerWidth-tip.offsetWidth-8))+'px';tip.style.top=Math.max(8,Math.min(top,innerHeight-tip.offsetHeight-8))+'px';
  }
  document.addEventListener('keydown',e=>{if(e.key==='Escape')hideTooltip();});window.addEventListener('scroll',hideTooltip,{passive:true});window.addEventListener('resize',hideTooltip);
  function render(){
    if(!summary)return;hideTooltip();
    const view=api.top10(summary,scope,selectedMonth,metric),target=$('rankings');target.replaceChildren();
    currentPending=view.pending;
    $('view-total').textContent=`${labels[scope]} · ${number(view.total)} ${metric}${metric==='tickets'?' approved':' on approved tickets'} · ${api.periodLabel(selectedMonth)}`;
    $('view-total').title=metric==='tickets'?'Approved Tickets by Ticket Claim Approved On, including approved closed, Other and Tickets without Issues':'Classified Issues';
    $('pending-count').textContent=view.pending?`${number(view.pending)} pending`:'';$('pending-count').hidden=!view.pending;
    if(!view.rows.length)target.append(element('p','empty',view.pending?'Classification is still in progress.':'No classified issues in this period.'));
    view.rows.forEach((row,i)=>{
      const card=element('section','failure-card');card.dataset.category=row.code;
      const head=element('div','card-head'),title=element('h2','',`${i+1}. ${row.name}`);title.title=row.name;head.append(title);card.append(head);
      const stats=element('div','card-stats'),total=element('div');total.append(element('span','stat-label',metric==='tickets'?'Tickets':'Issues'),element('strong','card-count',number(row.count)));
      const share=element('div');share.append(element('span','stat-label','Share'),element('span','card-share',(row.share*100).toFixed(1)+'%'));share.title=metric==='tickets'?'Share of all approved Tickets in this approval period; categories may overlap':'Share of classified Issues';const amount=element('div','amount-stat');amount.append(element('span','stat-label','Parts (AUD)'),element('span','card-amount',amountText(row,true)));amount.title=(row.partsAmountKnownTickets?money(row.partsAmountCents)+'. ':'')+amountHelp(row);stats.append(total,share,amount);card.append(stats);
      const rows=api.monthly(summary,scope,trendYear,metric,row.code),calendar=api.reportingCalendar();
      const last=selectedMonth.length===7?Number(selectedMonth.slice(5)):rows.length;
      const complete=Number(trendYear)===calendar.year?Math.min(last,calendar.month-1):last;
      if(complete>=6){
        const recent=rows.slice(complete-3,complete).reduce((n,r)=>n+r.count,0),prior=rows.slice(complete-6,complete-3).reduce((n,r)=>n+r.count,0);
        const delta=prior?(recent-prior)/prior*100:null;
        const badge=element('span','trend-badge'+(delta>0?' rising':delta<0?' falling':''),delta===null?(recent?'New':'—'):(delta>0?'+':'')+delta.toFixed(1)+'%');
        badge.title=`${rows[complete-3].month}–${rows[complete-1].month}: ${number(recent)} ${metric}; previous 3 months: ${number(prior)}`;head.append(badge);
      }
      const chart=element('div','card-chart');chart.setAttribute('aria-label',`${row.name}, ${labels[scope]}, monthly ${metric}, ${trendYear}, Ticket approval date`);
      const max=Math.max(1,...rows.map(r=>r.count)),axis=element('div','chart-axis');axis.append(element('span','',number(max)),element('span','',number(Math.round(max/2))),element('span','','0'));chart.append(axis);
      const grid=element('div','month-bars');grid.style.setProperty('--months',rows.length);
      rows.forEach((r,index)=>{
        const button=element('button','month-bar'+(selectedMonth===r.month?' selected':''));button.type='button';button.dataset.month=r.month;button.dataset.count=r.count;
        const tooltip=`${r.month} · ${number(r.count)} ${metric}`;button.setAttribute('aria-label',tooltip);button.setAttribute('aria-pressed',String(selectedMonth===r.month));
        const track=element('span','month-column'),bar=element('span','month-fill');bar.style.height=(r.count/max*100)+'%';track.append(bar);
        if(index===rows.findIndex(v=>v.count===max)||index===rows.length-1||selectedMonth===r.month){const value=element('span','month-value',number(r.count));value.style.bottom=(r.count/max*100)+'%';track.append(value);}
        button.append(track,element('span','month-label',String(index+1)));
        button.addEventListener('pointerenter',e=>showTooltip(button,row,r,e));button.addEventListener('pointermove',e=>showTooltip(button,row,r,e));button.addEventListener('pointerleave',hideTooltip);button.addEventListener('focus',()=>showTooltip(button,row,r));button.addEventListener('blur',hideTooltip);
        button.addEventListener('click',()=>{selectedMonth=selectedMonth===r.month?trendYear:r.month;$('month').value=selectedMonth;render();});grid.append(button);
      });chart.append(grid);card.append(chart);
      const caption=element('div','chart-caption');caption.append(element('span','',`Jan ${trendYear}`),element('span','',new Intl.DateTimeFormat('en-AU',{month:'short',timeZone:'UTC'}).format(new Date(rows.at(-1).month+'-01T00:00:00Z'))+' '+trendYear));card.append(caption);target.append(card);
    });renderNotice();
  }
  function renderNotice(partial=currentPending){
    const message=automation.status==='failed'?'Update failed; showing saved results.':automation.status==='running'?'Update in progress.':automation.pauseReason==='ai_credit_balance_exhausted'?'AI classification paused: API credit unavailable.':`${number(partial)} ${metric} have pending classification.`;
    const state=cacheMode==='offline'?'Saved data':automation.status==='failed'?'Update failed':automation.status==='running'?'Updating…':cacheMode==='checking'?'Checking…':partial&&automation.pauseReason==='ai_credit_balance_exhausted'?'AI paused':'';
    $('run-status').textContent=state;$('run-status').hidden=!state;$('run-status').title=cacheMode==='offline'?'Saved data; update unavailable.':message;
  }
  function showCacheState(mode){cacheMode=mode;renderNotice();}
  let exportScript;
  function loadExportScript(){
    if(window.FailurePartsExport)return Promise.resolve();
    if(!exportScript)exportScript=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='parts-export.js?v=20261009-claim-aligned';script.onload=resolve;script.onerror=()=>{script.remove();exportScript=null;reject(Error('Export module could not be loaded.'));};document.head.append(script);});
    return exportScript;
  }
  async function exportCsv(){
    if(!summary||exporting)return;
    const view={summary,scope,month:selectedMonth,metric,year:trendYear};
    exporting=true;$('export').disabled=true;$('export-status').textContent='Preparing Ticket details…';
    try{
      await loadExportScript();
      const workbook=await window.FailurePartsExport.build({...view,api,cache,read:path=>smallJson(database+dataPath+'/'+path+'.json',4*1024*1024),onProgress:(n,total)=>{$('export-status').textContent=`Loading details ${n}/${total}…`;}});
      const url=URL.createObjectURL(workbook);
      const type=view.scope==='both'?'both':view.scope==='Z006'?'in-field':'pre-delivery';
      const a=element('a');a.href=url;a.download=`top10-failure-parts-${type}-${view.month}-${view.metric}.xlsx`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      $('export-status').textContent='Export ready';
    }catch(error){$('export-status').textContent='Export failed: '+error.message;}
    finally{exporting=false;$('export').disabled=false;}
  }
  async function smallJson(url,limit=512*1024){
    const response=await fetch(url,{cache:'no-cache',signal:AbortSignal.timeout(8000)});
    if(!response.ok)throw new Error('Data request failed ('+response.status+')');
    if(Number(response.headers.get('Content-Length'))>limit){await response.body?.cancel();throw new Error('Page data exceeds its size limit');}
    const reader=response.body.getReader(),chunks=[];let bytes=0;
    while(true){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>limit){await reader.cancel();throw new Error('Page data exceeds its size limit');}chunks.push(value);}
    const data=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.byteLength;}
    return JSON.parse(new TextDecoder().decode(data));
  }
  const readVersion=()=>smallJson(database+dataPath+'/startup/sourceVersion.json',1024);
  function applySnapshot(value,mode='checking'){
    const decoded=api.decodeStartup(value,root),candidate=decoded.snapshot;
    if(decoded.summary.categoryDimension!=='issue_position'||decoded.summary.reportingBasis!=='approved_on'||!decoded.summary.claimSourceVersion)throw Error('Approved Issue Position snapshot is not available yet.');
    if(snapshot&&(Date.parse(candidate.generatedAt)<Date.parse(snapshot.generatedAt)||(candidate.generatedAt===snapshot.generatedAt&&candidate.exportVersion===snapshot.exportVersion)))return;
    const first=!summary;
    snapshot=candidate;summary=decoded.summary;automation=decoded.automation;
    const calendar=api.reportingCalendar(),years=[0,1,2].map(offset=>String(calendar.year-offset));
    const select=$('month');select.replaceChildren();
    years.forEach((year,index)=>{
      const group=document.createElement('optgroup');group.label=['This year','Last year','Two years ago'][index]+' · '+year;
      group.append(new Option(api.periodLabel(year),year));
      const last=index===0?calendar.month:12;
      for(let n=1;n<=last;n++){const month=year+'-'+String(n).padStart(2,'0');group.append(new Option(month,month));}
      select.append(group);
    });
    if(![...select.options].some(o=>o.value===selectedMonth))selectedMonth=years[0];select.value=selectedMonth;
    trendYear=selectedMonth.slice(0,4);
    $('updated').textContent='Ticket data '+new Intl.DateTimeFormat('en-AU',{dateStyle:'medium',timeStyle:'short',timeZone:'Australia/Sydney'}).format(new Date(summary.ticketDataAsOf));
    $('content').hidden=false;$('loading').hidden=true;$('export').disabled=exporting;render();
    showCacheState(mode);
    if(first)requestAnimationFrame(()=>{document.documentElement.dataset.partsReadyMs=performance.now().toFixed(1);});
  }
  async function load(force=false){
    const thisLoad=++loadId;
    $('reload').disabled=true;$('error').hidden=true;
    const versionPromise=readVersion();
    const useLocal=value=>{if(thisLoad===loadId&&value)applySnapshot(value);};
    const cached=force?Promise.resolve():Promise.resolve(cache?.getPageRecord(cacheKey)).then(r=>useLocal(r?.value)).catch(()=>{});
    const local=force?Promise.resolve():smallJson('outputs/issue_subcategory_startup.json').then(useLocal).catch(()=>{});
    // Small operational status is independent of rendering the saved snapshot.
    const statusPromise=smallJson(database+dataPath+'/automation.json',16*1024).catch(()=>null);
    try{
      const version=await versionPromise;
      await cached;
      if(!version){await local;throw new Error('No published Issue Position snapshot is available yet.');}
      if(force||!snapshot||snapshot.sourceVersion!==version){
        const value=await smallJson(database+dataPath+'/startup.json');
        const decoded=api.decodeStartup(value,root);
        const confirmed=await readVersion();
        if(decoded.snapshot.sourceVersion!==confirmed)throw new Error('The dataset changed during refresh. Please refresh again.');
        applySnapshot(value,'fresh');
        if(snapshot.sourceVersion!==confirmed)throw new Error('The saved dataset is newer than the server publication.');
      }
      showCacheState('cached');
      void cache?.setPage(cacheKey,snapshot.sourceVersion,snapshot);
    }catch(error){
      await Promise.allSettled([cached,local]);
      $('loading').hidden=true;$('error').hidden=!!summary;
      $('error').textContent=summary?'':`Results unavailable. ${error.message}`;
      if(summary)showCacheState('offline');
    }
    finally{$('reload').disabled=false;}
    const status=await statusPromise;
    if(thisLoad===loadId&&status){automation=status;renderNotice();}
  }
  $('month').addEventListener('change',e=>{selectedMonth=e.target.value;trendYear=selectedMonth.slice(0,4);render();});
  document.querySelectorAll('[data-scope]').forEach(btn=>btn.addEventListener('click',()=>{scope=btn.dataset.scope;document.querySelectorAll('[data-scope]').forEach(b=>{b.classList.toggle('active',b===btn);b.setAttribute('aria-pressed',String(b===btn));});render();}));
  document.querySelectorAll('[data-metric]').forEach(btn=>btn.addEventListener('click',()=>{metric=btn.dataset.metric;document.querySelectorAll('[data-metric]').forEach(b=>{b.classList.toggle('active',b===btn);b.setAttribute('aria-pressed',String(b===btn));});render();}));
  $('reload').addEventListener('click',()=>load(true));$('export').addEventListener('click',exportCsv);
  $('navigation-toggle').addEventListener('click',()=>{const open=document.querySelector('.sidebar').classList.toggle('nav-open');$('navigation-toggle').setAttribute('aria-expanded',String(open));});
  load();
})();
