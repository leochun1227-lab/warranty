(() => {
  'use strict';
  const $=id=>document.getElementById(id), num=n=>new Intl.NumberFormat('en-AU').format(n);
  const pct=(n,d)=>d?(100*n/d).toFixed(1)+'%':'—';
  const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  let data,manifest,loadId=0,detailId=0;
  const approved='approved',system='',search='';
  const detailCache=new Map();
  function setLevel(level){
    document.body.dataset.level=level;
    $('l2-view').hidden=level!=='l2';$('l3-view').hidden=level!=='l3';
    $('show-l2').setAttribute('aria-pressed',String(level==='l2'));
    $('show-l3').setAttribute('aria-pressed',String(level==='l3'));
  }
  function selectLevel(level){setLevel(level);history.replaceState(null,'','#'+level);}
  $('show-l2').onclick=()=>selectLevel('l2');$('show-l3').onclick=()=>selectLevel('l3');
  $('show-definitions').onclick=()=>$('definitions').showModal();
  $('close-definitions').onclick=()=>$('definitions').close();
  const levelFromLocation=()=>setLevel(location.hash==='#l3'?'l3':'l2');
  window.addEventListener('hashchange',levelFromLocation);
  levelFromLocation();
  // Stable system colours; filtering changes ranking without changing a system's colour.
  const systemTones={'Electrical System':'blue','Others':'slate','External Fixtures':'teal','Interior Components':'violet',
    'Exterior Components':'teal','Mechanical':'slate','Appliances':'blue','Gas System':'amber','Plumbing':'teal',
    'Floor':'amber','Chassis & Running Gear':'slate','Repairer misc.':'slate','Openings':'violet',
    'Unclassified':'slate','Water System':'teal','Compliance':'violet'};
  const selectedMonths=()=>data.monthly.filter(r=>r.month.startsWith($('year').value+'-')&&($('month').value==='all'||r.month===$('month').value));
  const scopeGroups=()=>{const months=new Set(selectedMonths().map(r=>r.month));return data.groups.filter(r=>months.has(r[0])&&(approved==='all'||r[4]===1));};
  function metric(title,value,help){const card=el('div','metric');card.append(el('div','metric-title',title),el('strong','metric-value',value));if(help)card.title=help;return card;}
  function render(){
    if(!data)return;
    const rows=selectedMonths(),sum=k=>rows.reduce((n,r)=>n+r[k],0),claims=sum('claims'),issues=sum('issues'),unknown=sum('approvalUnknown');
    const metrics=$('metrics');metrics.replaceChildren(
      metric('Approved Claims',num(sum('approvedClaims'))),
      metric('Approved Issues / Defects',num(sum('approvedIssues')),data.approvedIssueBasis),
      metric('Issues per Claim',sum('approvedClaims')?(sum('approvedIssues')/sum('approvedClaims')).toFixed(2):'—'),
      metric('green SAP 60days','—','Green SAP production dates have not been connected to this dashboard.'));
    $('status').textContent=unknown?`${num(unknown)} Claims have no approval status in Firebase and are excluded.`:'';
    const groups=scopeGroups(),denom=groups.reduce((n,r)=>n+r[5],0);
    classification(groups,denom);
    $('export').disabled=false;
  }
  function totals(groups,index){const map=new Map();for(const r of groups)map.set(r[index],(map.get(r[index])||0)+r[5]);return [...map].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]));}
  function classification(groups,denom){
    const systems=totals(groups,1),modes=totals(groups,3),cards=$('system-cards');
    cards.replaceChildren();let componentCount=0;
    function countButton(name,count,predicate){
      const button=el('button','classification-count',num(count));button.type='button';
      button.disabled=count===0;
      button.setAttribute('aria-label',`${name}: ${num(count)} Issues, ${pct(count,denom)}. View Claim details`);
      button.onclick=()=>showDetails(predicate,name,count);return button;
    }
    for(const [name,count] of systems){
      const subset=groups.filter(r=>r[1]===name),components=totals(subset,2);
      componentCount+=components.length;
      const wide=components.length>=10;
      const card=el('article','system-card'+(wide?' system-card--wide':''));card.dataset.issues=String(count);card.dataset.order=String(cards.childElementCount);
      card.dataset.tone=systemTones[name]||'slate';
      const heading=el('div','system-heading');
      const title=el('h3','');title.append(el('span','system-rank',String(cards.childElementCount+1).padStart(2,'0')),document.createTextNode(name));
      heading.append(title,countButton(name,count,r=>r[2]===name),el('span','classification-share',pct(count,denom)));
      const list=el('div','component-list');
      // DOM, visual and keyboard order all follow descending count, including on mobile.
      for(const [component,n] of components){
        const row=el('div','component-row');row.dataset.issues=String(n);
        const label=el('span','component-label',component);
        row.append(label,countButton(name+' / '+component,n,r=>r[2]===name&&r[3]===component),el('span','classification-share',pct(n,denom)));
        list.append(row);
      }
      card.append(heading,list);cards.append(card);
    }
    if(!systems.length)cards.append(el('p','empty','No Approved Issues in this period.'));
    $('category-total').textContent=`${systems.length} systems · ${componentCount} components`;
    const failure=$('failure-modes');failure.replaceChildren();
    const guide=window.PDVFailureGuide,modeCounts=new Map(modes),referenceNames=new Set(guide.map(g=>g.name));
    for(const [index,item] of guide.entries()){
      const n=modeCounts.get(item.name)||0,card=el('article','failure-guide-card');card.dataset.issues=String(n);
      const heading=el('div','failure-guide-heading');
      heading.append(el('span','guide-number',String(index+1).padStart(2,'0')),el('h3','',item.name));
      const stat=el('div','guide-stat');stat.append(countButton(item.name,n,r=>r[4]===item.name),el('span','classification-share',pct(n,denom)));heading.append(stat);
      const body=el('div','failure-guide-body');body.append(el('p','guide-definition',item.definition));
      const descriptions=el('dl','guide-descriptions');
      for(const [label,text] of [['Identify by',item.identify],['Common examples',item.examples]]){
        descriptions.append(el('dt','',label),el('dd','',text));
      }
      body.append(descriptions);
      const exclude=el('p','guide-exclusion');exclude.append(el('strong','','Do not use: '),document.createTextNode(item.exclude));body.append(exclude);
      card.append(heading,body);failure.append(card);
    }
    const decision=el('article','failure-guide-card decision-card');
    decision.append(el('h3','','When failure modes overlap'),el('p','','Classify the primary defect identified by the assessor.'));
    const decisions=el('dl','decision-list');
    for(const [condition,mode] of [['Cracked + not working','Damage'],['Present but loose','Configuration / Fitment'],['Physically absent','Missing'],['Cracked + leaking','Use the primary issue identified']]){
      decisions.append(el('dt','',condition),el('dd','',mode));
    }
    decision.append(decisions);failure.append(decision);
    const other=$('other-failure-modes');other.replaceChildren();
    const otherModes=modes.filter(([name])=>!referenceNames.has(name));
    const otherTotal=otherModes.reduce((sum,[,n])=>sum+n,0);
    $('other-mode-total').textContent=`${num(otherTotal)} Issues · ${pct(otherTotal,denom)}`;
    for(const [name,n] of otherModes){
      const row=el('div','other-mode-row');row.dataset.issues=String(n);
      const heading=el('div','other-mode-heading');heading.append(el('h4','',name),countButton(name,n,r=>r[4]===name),el('span','classification-share',pct(n,denom)));
      const components=totals(groups.filter(r=>r[3]===name),2).slice(0,2);
      row.append(heading,el('p','other-mode-context','Leading components: '+components.map(([c,count])=>`${c} (${num(count)})`).join(' · ')));other.append(row);
    }
    if(!otherModes.length)other.append(el('p','empty','No other source categories in this period.'));
    const usage=$('position-usage');usage.replaceChildren();
    const months=new Set(selectedMonths().map(r=>r.month));
    for(const [code,label] of [['Z009','Other']]){
      const row=el('div','position-usage-item');row.append(el('h5','',label));
      if(Array.isArray(data.positionUsage)){
        const n=data.positionUsage.filter(r=>months.has(r[0])&&r[1]===code&&r[2]===1).reduce((sum,r)=>sum+r[3],0);
        row.append(countButton('L1 '+label+' ('+code+')',n,r=>r[6]===code),el('span','classification-share',pct(n,denom)));
      }else row.append(el('span','','Not available'));
      usage.append(row);
    }
  }
  async function json(url){return window.PDVData.detail(url);}
  async function showDetails(predicate,title,expected){
    const request=++detailId,months=selectedMonths().map(r=>r.month),scope=approved,base=manifest.detailBase;
    $('detail-title').textContent=title;$('detail-body').replaceChildren();$('detail-status').textContent='Loading Issue details…';$('details').showModal();
    try{
      const blocks=await Promise.all(months.map(async month=>{const key=base+month+'.json';if(!detailCache.has(key))detailCache.set(key,await json(key));return detailCache.get(key);}));
      if(request!==detailId)return;
      const rows=blocks.flat().filter(r=>(scope==='all'||r[5]===true)&&predicate(r));
      if(rows.length!==expected)throw Error('Detail count differs from summary. Refresh to load the completed update.');
      $('detail-status').textContent=`${num(rows.length)} Issues`+(rows.length>500?' · showing first 500':'');
      const frag=document.createDocumentFragment();for(const r of rows.slice(0,500)){const tr=el('tr');for(const v of [...r.slice(0,5),r[5]===null?'Unknown':r[5]?'Yes':'No'])tr.append(el('td','',v));frag.append(tr);}$('detail-body').append(frag);
    }catch(e){if(request===detailId)$('detail-status').textContent='Details unavailable: '+e.message;}
  }
  function setData(value,meta){
    if(value.schema!=='pdv-overview-v1'||!Array.isArray(value.groups)||!Array.isArray(value.monthly))throw Error('Invalid overview snapshot');
    const oldYear=$('year').value,oldMonth=$('month').value;data=value;manifest=meta;
    const years=[...new Set(data.monthly.filter(r=>/^\d{4}-\d{2}$/.test(r.month)).map(r=>r.month.slice(0,4)))].sort().reverse();
    $('year').replaceChildren(...years.map(y=>new Option(y,y)));$('year').value=years.includes(oldYear)?oldYear:years[0];setMonths(oldMonth);
    $('overview').hidden=false;$('status').textContent='';
    const format=s=>new Intl.DateTimeFormat('en-AU',{dateStyle:'medium',timeStyle:'short',timeZone:'Australia/Sydney'}).format(new Date(s));
    $('updated').textContent='Updated '+format(data.generatedAt);
    $('source-dates').textContent=`Issues: ${format(data.generatedAt)}. Firebase approval status: ${format(data.ticketSourceModifiedAt)}. Updated after the warranty 5pm job completes. Missing approval status is not counted as unapproved.`;
    $('production-note').textContent='Produced: Green SAP production dates have not been connected to this dashboard. The 60-day population and percentage cannot yet be calculated.';
    render();requestAnimationFrame(()=>document.documentElement.dataset.overviewReadyMs=performance.now().toFixed(1));
  }
  function setMonths(previous='all'){
    const options=data.monthly.filter(r=>r.month.startsWith($('year').value+'-')).map(r=>new Option(new Date(r.month+'-01T00:00:00').toLocaleString('en-AU',{month:'short'}),r.month));
    $('month').replaceChildren(new Option('All months','all'),...options);$('month').value=options.some(o=>o.value===previous)?previous:'all';
  }
  async function load(){
    const id=++loadId;$('refresh').disabled=true;
    try{await window.PDVData.load((value,meta,mode)=>{if(id!==loadId)return;if(manifest?.generation!==meta.generation)setData(value,meta);else if(mode==='fresh')render();if(mode==='offline')$('status').textContent='Firebase is unavailable; showing the last saved Firebase snapshot.';});}
    catch(e){$('status').textContent=(data?'Showing saved data. Latest update unavailable. ':'Overview unavailable. ')+e.message;}
    finally{if(id===loadId)$('refresh').disabled=false;}
  }
  $('year').onchange=()=>{setMonths();render();};$('month').onchange=render;$('refresh').onclick=load;
  $('close-details').onclick=()=>{detailId++;$('details').close();};$('details').addEventListener('close',()=>detailId++);
  $('export').onclick=async()=>{
    const rows=[['Month','Approved Claims','Approved Issues','Claims excluded: unknown approval','Produced'],...selectedMonths().map(r=>[r.month,r.approvedClaims,r.approvedIssues,r.approvalUnknown,'Unavailable']),[],['Month','Level 1','Level 2','Level 3','Approval','Issues'],...scopeGroups().filter(r=>(!system||r[1]===system)&&(!search||r[2].toLowerCase().includes(search))).map(r=>[...r.slice(0,4),'Approved',r[5]])];
    const period=$('month').value==='all'?$('year').value:$('month').value,base=manifest.detailBase,selection=selectedMonths().map(r=>r.month),selectedSystem=system,selectedSearch=search;
    $('export').disabled=true;
    try{
      const details=(await Promise.all(selection.map(m=>json(base+m+'.json')))).flat().filter(r=>r[5]===true&&(!selectedSystem||r[2]===selectedSystem)&&(!selectedSearch||r[3].toLowerCase().includes(selectedSearch)));
      const split=rows.findIndex(r=>r.length===0);
      await window.PDVExport([{name:'Scope',rows:[['Created On from (inclusive)','2026-09-09']]},{name:'Monthly',rows:rows.slice(0,split)},{name:'Categories',rows:rows.slice(split+1)},{name:'Approved Issues',rows:[['Ticket','Issue','Level 1','Level 2','Level 3'],...details.map(r=>r.slice(0,5))]}],`pdv-overview-${period}.xlsx`);
    }catch(e){$('status').textContent='Export failed: '+e.message;}finally{$('export').disabled=false;}
  };
  load();
  // Revalidate small snapshots when returning to an already open page.
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
  setInterval(()=>{if(!document.hidden)load();},5*60*1000);
})();
