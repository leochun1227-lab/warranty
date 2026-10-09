"""Apply the standalone PDV view on every warranty page refresh."""
import re


def remove_div(page, element_id):
    start = page.index('<div id="' + element_id + '"')
    depth = 0
    for tag in re.finditer(r'</?div\b[^>]*>', page[start:]):
        depth += -1 if tag.group().startswith('</') else 1
        if depth == 0:
            return page[:start] + page[start + tag.end():]
    raise ValueError('Unclosed chart: ' + element_id)


def pdv_page(page):
    for prefix in ('newClaim', 'approved', 'amount', 'unapproved', 'claimClosed'):
        page = remove_div(page, prefix + 'SplitCard')
        page = remove_div(page, prefix + 'Totals')
    page = re.sub(r'<section class="panel" id="unapproved">[\s\S]*?</section>', '', page, count=1)
    page = page.replace('function syncUnapprovedDateControls(){', 'function syncUnapprovedDateControls(){\n  if(!$("unapproved"))return;')
    page = re.sub(r'^  \$\("unapproved(?:ClosedOn|CreatedOn)Btn"\)\.onclick=.*\n', '', page, flags=re.M)
    page = page.replace('["newclaim","approved","amount","unapproved"]', '["newclaim","approved","amount"]')
    page = page.replace('const unapproved=monthWithinRange(unapprovedGroupMonth,start,end)&&ticketUnapprovedClosedNow(ticket);', 'const unapproved=false;')
    page = page.replace(',"Unapproved Tickets Group By"', '').replace('      unapprovedDateLabel(),\n', '')
    page = page.replace('Created, Approved, Amount Claim, or Unapproved.', 'Created, Approved, or Amount Claim.')
    page = page.replace('_unapproved_by_${unapprovedDateBasis}', '')
    page = re.sub(r'<link rel="preload" href="outputs/claim_trend_startup.json"[^>]*>\n?', '', page)
    page = page.replace('fieldMode="all"', 'fieldMode="pre"')
    start = page.index('function fieldConfig(){')
    end = page.index('\nfunction syncFieldButtons', start)
    page = page[:start] + '''function fieldConfig(){
  return {mode:"pre",label:"Pre Delivery",shortLabel:"Pre Delivery",color:"#397eae",value:(r,_inKey,preKey)=>Number(r[preKey]||0)};
}
''' + page[end:]
    page = page.replace('total:Number(r[inKey]||0)+Number(r[preKey]||0)',
                        'total:Number(r[preKey]||0)')
    page = page.replace('All Fields', 'Pre Delivery')
    page = page.replace('<body class="charts-loading">', '<body class="charts-loading pdv-trend-page">')
    page = page.replace('</head>', '<script src="firebase-data.js" defer></script><script src="pdv-trend.js" defer></script></head>')
    page = page.replace('<div class="dashboardGrid">', '''<div class="overview-charts pdv-monthly-overview">
      <section class="panel"><div class="panel-heading"><h2>Monthly Approved Claims</h2><span class="muted">Created On</span></div><div class="pdv-monthly-scroll"><div id="pdv-monthly-claims" class="monthly-chart" role="img" aria-label="Monthly approved Pre Delivery Claims"></div></div></section>
      <section class="panel"><div class="panel-heading"><h2>Monthly Approved Issues / Defects</h2><span class="muted">Created On</span></div><div class="pdv-monthly-scroll"><div id="pdv-monthly-issues" class="monthly-chart" role="img" aria-label="Monthly approved Pre Delivery Issues"></div></div></section>
    </div><p id="pdv-monthly-status" class="muted" role="status">Loading approved monthly totals…</p>
    <div class="dashboardGrid">''', 1)
    page = page.replace('function render(){', '''function render(){
  window.PDVTrend?.render({viewMode,activeStart,activeEnd,rangePreset,compareYearA,compareYearB});''', 1)
    # Replace year comparison with a real Tickets / Issues view switch.
    page = remove_div(page, 'compareControls')
    page = page.replace('>Trend</button>', '>Tickets</button>').replace('>Year Compare</button>', '>Issues</button>')
    page = page.replace('function toggleModeButtons(){', 'function toggleModeButtons(){\n  window.PDVTrend?.syncView();return;')
    for name in ('compareYearA', 'compareYearB'):
        page = re.sub(r'^  \$\("'+name+r'"\)\.(?:innerHTML|value)=.*\n', '', page, flags=re.M)
    start = page.index('  $("singleModeBtn").onclick=')
    end = page.index('  $("exportTicketsBtn").onclick=', start)
    page = page[:start] + '''  $("singleModeBtn").onclick=()=>{window.PDVTrend?.setView("tickets");renderWithPageLoading(false);};
  $("compareModeBtn").onclick=()=>{window.PDVTrend?.setView("issues");};
''' + page[end:]
    page = page.replace('$("exportTicketsBtn").onclick=()=>exportClaimTrendDetails();',
                        '$("exportTicketsBtn").onclick=()=>document.documentElement.dataset.trendMetric==="issues"?window.PDVTrend.exportIssues():exportClaimTrendDetails();')
    page = page.replace('$("fromMonth").focus();\n      return;', '$("fromMonth").focus();\n      renderWithPageLoading(false);return;')
    # All views use one immutable cohort: Claims created on/after 9 September 2026.
    page = page.replace('const HARD_MIN="2025-01";', 'const HARD_MIN="2026-09";\nlet PDV_CLAIM_SNAPSHOT=null,pdvClaimDetailsPromise=null;')
    page = page.replace('approvedDateBasis="approved"', 'approvedDateBasis="created"')
    page = page.replace('amountDateBasis="approved"', 'amountDateBasis="created"')
    page = page.replace('claim-trend-startup:v1', 'pdv-claim-startup:created-2026-09-09:v1')
    page = page.replace('snapshot?.schema==="claim-startup-v1"&&', 'snapshot?.scopeStart==="2026-09-09"&&snapshot?.schema==="claim-startup-v1"&&')
    page = page.replace('    const first=!shown;', '    const first=!shown;\n    PDV_CLAIM_SNAPSHOT=snapshot;pdvClaimDetailsPromise=null;')
    page = page.replace(',\n    fetchClaimStartup(`${FIREBASE}/${ROOT}/analytics/claimTrendStartup.json`)', '')
    start=page.index('async function ensureAmountTicketRowsLoaded(){')
    end=page.index('\nfunction withCreatedAmountMonthly',start)
    page=page[:start]+'''async function scopedClaimDetails(){
  if(!PDV_CLAIM_SNAPSHOT?.detailPath)throw Error("Scoped Claim details are not ready");
  if(!pdvClaimDetailsPromise){
    const path=PDV_CLAIM_SNAPSHOT.detailPath;
    pdvClaimDetailsPromise=readJson(path,15000).then(value=>{
      const data=JSON.parse(value.data);
      if(data.scopeStart!=="2026-09-09"||data.tickets.some(t=>dateKey(ticketCreatedOn(t))<"2026-09-09"))throw Error("Incorrect Claim date scope");
      return data;
    }).catch(error=>{pdvClaimDetailsPromise=null;throw error;});
  }
  return pdvClaimDetailsPromise;
}
async function ensureAmountTicketRowsLoaded(){
  if(amountTicketRows!==null)return amountTicketRows;
  const data=await scopedClaimDetails();
  amountTicketRows=data.amountRows;amountTicketsById=new Map(amountTicketRows.map(r=>[ticketId(r),r]));
  return amountTicketRows;
}
''' +page[end:]
    start=page.index('async function ensureRawTicketsLoaded(){')
    end=page.index('\nfunction summarySheetRowsSingle',start)
    page=page[:start]+'''async function ensureRawTicketsLoaded(){
  if(rawTicketsLoaded)return RAW_TICKETS;
  const data=await scopedClaimDetails();RAW_TICKETS=data.tickets;rawTicketsLoaded=true;
  return RAW_TICKETS;
}
''' +page[end:]
    page=page.replace('Detail export includes only tickets matched by this page:', 'Claims created on or after 2026-09-09 (inclusive). Detail export includes only tickets matched by this page:')
    return page
