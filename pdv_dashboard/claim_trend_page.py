"""Apply the standalone PDV view on every warranty page refresh."""
import re


def enlarge_chart_numbers(page):
    replacements = {
        'ctx.font="500 11px Segoe UI Variable,Aptos,Segoe UI,Arial"':
            'ctx.font="500 13px Segoe UI Variable,Aptos,Segoe UI,Arial"',
        'ctx.font="500 10px Segoe UI Variable,Aptos,Segoe UI,Arial"':
            'ctx.font="500 12px Segoe UI Variable,Aptos,Segoe UI,Arial"',
        'const fontSize=groupW>=58?11:10;': 'const fontSize=groupW>=58?13:12;',
        'const left=18,right=18,top=20,bottom=': 'const left=28,right=28,top=20,bottom=',
        'Math.abs(labelY-primaryLabelY)<16': 'Math.abs(labelY-primaryLabelY)<18',
        'const labelTop=baseline-10,labelBottom=baseline+3;':
            'const labelTop=baseline-12,labelBottom=baseline+3;',
        'top:item.y-10,bottom:item.y+3': 'top:item.y-12,bottom:item.y+3',
        'let textY=py-10;\n    const monthLabelY':
            'let textY=py-10;\n    if(opts.secondaryKey&&r[opts.secondaryKey]!=null&&Math.abs(py-y(r[opts.secondaryKey]))<36)textY=Math.min(py,y(r[opts.secondaryKey]))-12;\n    const monthLabelY',
        'const primaryLabelY=Math.max(16,Math.min(y(row[key])-10,h-54));':
            'const primaryY=y(row[key]),nearPrimary=row[key]!=null&&Math.abs(py-primaryY)<36;\n      const primaryLabelY=Math.max(16,Math.min(nearPrimary?Math.min(py,primaryY)-12:primaryY-10,h-54));',
        'let labelY=py<y(row[key])?Math.max(16,py-10):Math.min(h-58,py+17);':
            'let labelY=nearPrimary?Math.min(h-58,Math.max(py,primaryY)+17):py<primaryY?Math.max(16,py-10):Math.min(h-58,py+17);',
    }
    for before, after in replacements.items():
        page = page.replace(before, after)
    return page


def remove_div(page, element_id):
    start = page.index('<div id="' + element_id + '"')
    depth = 0
    for tag in re.finditer(r'</?div\b[^>]*>', page[start:]):
        depth += -1 if tag.group().startswith('</') else 1
        if depth == 0:
            return page[:start] + page[start + tag.end():]
    raise ValueError('Unclosed chart: ' + element_id)


def simplify_chart_copy(page):
    page = page.replace('<span class="viewNote">Group by</span>', '')
    page = page.replace('<div class="dualChartTitle">Pre Delivery</div>', '')
    page = re.sub(r'(<div id="\w+Legend" class="legend">)<span><i[^>]*></i>Pre Delivery</span>', r'\1', page)
    # Only remove chart labels; retain the real scope in filters and exports.
    page = page.replace('{label:"Pre Delivery",', '{label:"",')
    return re.sub(r'^[ \t]+$', '', page, flags=re.M)


def use_published_trend(page):
    start = page.index('async function loadClaimStartup(){')
    end = page.index('\nloadClaimStartup();', start)
    page = page[:start] + 'async function loadClaimStartup(){return loadPublishedTrend();}\n' + page[end:]
    marker = 'async function setAmountDateBasis(basis){'
    direct = '''
  if(publishedTrendSnapshot&&["approved","created"].includes(basis)){
    amountDateBasis=basis;syncAmountDateControls();renderWithPageLoading(false);return;
  }'''
    if marker + direct not in page:
        page = page.replace(marker, marker + direct)
    return page


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
    # Keep Warranty's historical filters, year comparison, data and export loaders.
    # Only the other PDV pages use the fixed 9 September creation-date cohort.
    page = page.replace('charts-loading pdv-trend-page', 'charts-loading pdv-trend-page historical-trend-page')
    page = page.replace('    fetchClaimStartup("outputs/claim_trend_startup.json"),\n', '')
    page = page.replace('$("fromMonth").focus();\n      return;',
                        '$("fromMonth").focus();\n      renderWithPageLoading(false);return;')
    # Reuse Warranty's Pending status/quote loaders and drawing primitives, but
    # replace its all-fields/split renderers with the Pre Delivery-only view.
    start = page.index('function renderPendingApproved(')
    end = page.index('function drawSingleLineChart(', start)
    page = page[:start] + page[end:]
    page = page.replace('</head>', '<script src="claim-trend-pdv.js"></script></head>')
    # The standalone adapter owns the persistent Pending cache and loaders.
    start = page.index('function loadPendingTickets(){')
    end = page.index('function drawSingleLineChart(', start)
    page = page[:start] + page[end:]
    page = page.replace('  let shown=null;', '  await restorePendingSnapshot();\n  let shown=null;')
    page = page.replace('ctx.fillStyle="#667085"', 'ctx.fillStyle="#475569"')
    page = page.replace('ctx.font="12px Segoe UI Variable,Aptos,Segoe UI,Arial"',
                        'ctx.font="500 13px Segoe UI Variable,Aptos,Segoe UI,Arial"')
    page = page.replace('ctx.font="500 12px Segoe UI Variable,Aptos,Segoe UI,Arial"',
                        'ctx.font="500 13px Segoe UI Variable,Aptos,Segoe UI,Arial"')
    page = page.replace('approvedDateBasis="approved"', 'approvedDateBasis="created"')
    page = page.replace('amountDateBasis="approved"', 'amountDateBasis="created"', 1)
    for button in ('approvedOnBtn', 'amountApprovedOnBtn'):
        page = page.replace(f'id="{button}" class="miniBtn active" type="button" aria-pressed="true"',
                            f'id="{button}" class="miniBtn" type="button" aria-pressed="false"')
    for button in ('approvedCreatedOnBtn', 'amountCreatedOnBtn'):
        page = page.replace(f'id="{button}" class="miniBtn" type="button" aria-pressed="false"',
                            f'id="{button}" class="miniBtn active" type="button" aria-pressed="true"')
    page = page.replace('amountTicketRows=null;amountTicketsById=new Map();amountDateBasis="approved";',
                        'amountTicketRows=null;amountTicketsById=new Map();')
    page = page.replace('    claimMonthlyReady=true;', '    resetPdvTrendSources();\n    claimMonthlyReady=true;')
    page = page.replace('function render(){', 'function render(){\n  ensureCreatedAmounts();', 1)
    page = page.replace('    return;\n  }\n  const rows=rowsForRange();',
                        '    renderPendingComparison(approvedRows,amountRows);\n    return;\n  }\n  const rows=rowsForRange();', 1)
    page = page.replace('async function setAmountDateBasis(basis){', '''async function setAmountDateBasis(basis){
  if(basis==="created"&&amountDateBasis==="created"&&createdAmountsState.error){
    createdAmountsState={started:false,ready:false,error:false};renderWithPageLoading(false);return;
  }''')
    # Missing Created On amounts must not render as a fabricated zero while loading.
    page = page.replace('  rows.forEach((r,i)=>{const px=x(i),py=y(r[key]);if(i)ctx.lineTo(px,py);else ctx.moveTo(px,py)});',
                        '  let connected=false;\n  rows.forEach((r,i)=>{if(r[key]==null){connected=false;return;}const px=x(i),py=y(r[key]);if(connected)ctx.lineTo(px,py);else ctx.moveTo(px,py);connected=true;});')
    page = page.replace('  rows.forEach((r,i)=>{\n    const px=x(i),py=y(r[key]);',
                        '  rows.forEach((r,i)=>{\n    if(r[key]==null)return;\n    const px=x(i),py=y(r[key]);')
    # Export Pending separately so current open claims are never labelled Approved.
    page = page.replace('ensureRawTicketsLoaded(),ensureAmountTicketRowsLoaded()',
                        'ensureRawTicketsLoaded(),ensureAmountTicketRowsLoaded(),ensurePendingExport()')
    page = page.replace('{name:String(compareYearB),rows:exportDetailSheetRows(rowsB)}',
                        '{name:String(compareYearB),rows:exportDetailSheetRows(rowsB)},...pendingExportSheets()')
    page = page.replace('{name:"Tickets",rows:exportDetailSheetRows(rows)}',
                        '{name:"Tickets",rows:exportDetailSheetRows(rows)},...pendingExportSheets()')
    page = page.replace('Amount Including Tax is excluded.',
                        'Approved amounts use SAP amounts. Pending Claim Amount is reported separately in Pending Monthly.')
    return use_published_trend(simplify_chart_copy(enlarge_chart_numbers(page)))
