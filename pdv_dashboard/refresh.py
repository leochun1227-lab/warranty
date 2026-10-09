"""Build the standalone PDV site after the existing warranty daily job succeeds.

Reads existing warranty sources; publishes compact dashboard snapshots to Firebase.
No SAP/C4C fetch or AI call. Publish startup only after details are complete.
"""
from __future__ import annotations
import argparse, base64, concurrent.futures, csv, gzip, hashlib, json, os, re
import shutil, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from claim_trend_page import pdv_page
from pdv_scope import START_DATE, day, scoped_status, build_claim

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / 'public'
DB = 'https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app'
ISSUE = 'issueSubcategoryAnalysis/v1/c4cTickets_test'
SITE = ISSUE + '/pdvDashboard'
APPROVED = {'sales order approved','partially picked','dispatch parts','repair in progress',
            'repairer invoiced received','repairer invoiced processed','approved claims closed',
            'approved claims closed (closed)'}

def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_bytes(content)
    temp.replace(path)

def read(path):
    request=urllib.request.Request(DB + '/' + path + '.json',headers={'Accept-Encoding':'gzip'})
    with urllib.request.urlopen(request, timeout=120) as response:
        raw=response.read()
        return json.loads(gzip.decompress(raw) if response.headers.get('Content-Encoding')=='gzip' else raw)

def approved(row):
    return bool(row.get('Claim Approved On', '').strip()) and (
        row.get('Status','').strip().lower() in APPROVED or
        row.get('StatusCode','') in {'Z9','Y0','Y1','Y2','Y4','YB','Y7'})

def compile_overview(ticket_rows, issue_rows, master, names, generated_at):
    ticket_rows = [r for r in ticket_rows if day(r[1]) >= START_DATE]
    eligible_ids = {str(r[0]) for r in ticket_rows}
    issue_rows = [(str(tid), row) for tid, row in issue_rows if str(tid) in eligible_ids]
    monthly = defaultdict(lambda: {'claims':0, 'approvedClaims':0,'issues':0,'approvedIssues':0,'approvalUnknown':0})
    tickets = {}
    for row in ticket_rows:
        tid, created = str(row[0]), row[1]
        if tid in tickets:
            raise ValueError('Duplicate Pre Delivery ticket: ' + tid)
        month = created[:7] if re.match(r'^\d{4}-\d{2}', created) else 'unknown'
        source = master.get(tid)
        # The approval date and current status use the same rule as Claim Trend.
        is_approved = approved(source) if source is not None else None
        tickets[tid] = (month, is_approved)
        monthly[month]['claims'] += 1
        monthly[month]['approvedClaims'] += int(bool(is_approved))
        monthly[month]['approvalUnknown'] += source is None
    groups = Counter()
    position_usage = Counter()
    details = defaultdict(list)
    seen = set()
    missing = [0,0,0]
    for tid, issue in issue_rows:
        key = (tid, str(issue[1]))
        if key in seen:
            raise ValueError('Duplicate issue: ' + repr(key))
        seen.add(key)
        if tid not in tickets:
            raise ValueError('Issue does not belong to a published ticket')
        month, is_approved = tickets[tid]
        # Keep source hierarchy; never infer missing categories from descriptions.
        l1 = issue[3] or issue[2] or 'Unclassified'
        l2 = issue[6] or names.get(issue[5], {}).get('name') or issue[5] or 'Unclassified'
        l3 = issue[8] or issue[7] or 'Unclassified'
        for i, label in enumerate((l1,l2,l3)):
            missing[i] += label == 'Unclassified'
        monthly[month]['issues'] += 1
        monthly[month]['approvedIssues'] += int(bool(is_approved))
        groups[(month,l1,l2,l3, -1 if is_approved is None else int(is_approved))] += 1
        position = str(issue[2] or '').strip()
        position_usage[(month,position, -1 if is_approved is None else int(is_approved))] += 1
        details[month].append([tid,issue[1],l1,l2,l3,is_approved,position])
    if sum(r['claims'] for r in monthly.values()) != len(tickets):
        raise ValueError('Claim totals do not reconcile')
    if sum(groups.values()) != len(seen):
        raise ValueError('Issue totals do not reconcile')
    ranking = defaultdict(lambda: {'issues':0,'tickets':set()})
    for month, records in details.items():
        for row in records:
            if row[5] is not True:continue
            for level in (1,2,3):
                entry=ranking[(month,level,row[level+1])]
                entry['issues']+=1;entry['tickets'].add(row[0])
    overview = {'schema':'pdv-overview-v1','generatedAt':generated_at,
        'scopeStart':START_DATE,'scopeBasis':'created_on',
        'monthly':[{'month':m,**v} for m,v in sorted(monthly.items())],
        'groups':[[*k,n] for k,n in sorted(groups.items())],
        'positionUsage':[[*k,n] for k,n in sorted(position_usage.items())],
        'missingCategories':missing,
        'rankings':[[*k,v['issues'],len(v['tickets'])] for k,v in sorted(ranking.items())],
        'production':{'available':False,'reason':'Green SAP production dates have not been connected to this dashboard.'},
        'approvedIssueBasis':'Issues belonging to approved Claims; grouped by Claim Created On.'}
    return overview, details

def assemble_pages(warranty):
    PUBLIC.mkdir(exist_ok=True)
    for name in ('browser-page-cache.js','claim-trend-ticket-metrics.js','claim-overview.css',
                 'flow-polish.css','sidebar-light.css','dashboard-theme.css',
                 'parts-export.js'):
        shutil.copyfile(warranty/name, PUBLIC/name)
    for name in ('infieldpredelivery.html',):
        page = (warranty/name).read_text(encoding='utf-8')
        page = re.sub(r'<aside class="sidebar">[\s\S]*?</aside>', '', page, count=1)
        page = page.replace('</head>', '<link rel="stylesheet" href="site.css"><script src="navigation.js" defer></script></head>')
        page = page.replace('fetchClaimStartup("outputs/claim_trend_startup.json")',
                            'fetchClaimStartup("'+DB+'/'+SITE+'/claimTrendStartup.json")')
        start = page.index('async function readClaimStartupVersion(){')
        end = page.index('\nfunction validClaimStartup',start)
        page = page[:start]+'''async function readClaimStartupVersion(){
  try { return await readJson("'''+SITE+'''/claimTrendStartup/sourceVersion",4000); }
  catch { return ""; }
}'''+page[end:]
        atomic(PUBLIC/name, pdv_page(page).encode('utf-8'))

def resolve_warranty_root(explicit=None):
    # Resolve from the script location, never the scheduled task's working directory.
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    for candidate in (ROOT.parent, ROOT.parent/'warranty'):
        if (candidate/'run_daily_5pm_mandt800_rejection_filter.bat').is_file():
            return candidate.resolve()
    raise ValueError('Warranty folder not found beside this script. Use --warranty-root PATH.')

def check_environment(warranty, publish_only=False):
    required=['firebase-service-account.json','issue_subcategories.json']
    if not publish_only:
        required += ['browser-page-cache.js','claim-trend-ticket-metrics.js','claim-overview.css',
                     'flow-polish.css','sidebar-light.css','dashboard-theme.css','parts-export.js',
                     'infieldpredelivery.html']
    missing=[str(warranty/name) for name in required if not (warranty/name).is_file()]
    if missing:
        raise ValueError('Missing Warranty files: '+ '; '.join(missing))
    import firebase_admin
    categories=json.loads((warranty/'issue_subcategories.json').read_text(encoding='utf-8'))
    if not isinstance(categories.get('categories'),dict):
        raise ValueError('Invalid issue_subcategories.json: categories must be an object')
    print('PDV Warranty folder: '+str(warranty),flush=True)
    print('PDV updater folder: '+str(ROOT),flush=True)

def refresh(warranty, publish_only=False):
    check_environment(warranty,publish_only)
    print('Reading published warranty generations...', flush=True)
    startup = read(ISSUE+'/startup')
    issue = json.loads(startup['data'])
    if issue.get('categoryDimension') != 'issue_position':
        raise ValueError('Expected direct Issue Position data')
    version = issue['exportVersion']
    manifest = json.loads(read(ISSUE+'/exports/'+version+'/manifest'))
    if manifest['version'] != version:
        raise ValueError('Export generation mismatch')
    status_version=read('ctmTicketStatusMonitorV44/automation/lastRunFinishedAt')
    if not status_version:raise ValueError('Firebase approval generation is unavailable')
    status_cache=ROOT/'.cache'/'scope-status.json'
    status_payload=json.loads(status_cache.read_bytes()) if status_cache.exists() else {}
    if status_payload.get('version') != status_version:
        current=read('ctmTicketStatusMonitorV44/currentStatus')
        current=current.values() if isinstance(current,dict) else current or []
        status_payload={'version':status_version,'rows':[r for r in current if isinstance(r,dict) and r.get('claimType')=='Pre Delivery Warranty Claims']}
        atomic(status_cache,encode(status_payload))
    selected=scoped_status(status_payload['rows'])
    if not selected:raise ValueError('No Pre Delivery Claims in the requested date range')
    master={str(r['id']):{'Status':r.get('statusText',''),'StatusCode':r.get('statusCode',''),
        'Claim Approved On':day(r.get('claimApprovedOnDateTime') or r.get('claimApprovedOn'))} for r in selected}
    tickets=[[str(r['id']),day(r['created'])] for r in selected]
    amount_version=read('ctmTicketStatusMonitorV44/analytics/team/generatedAt')
    amount_cache=ROOT/'.cache'/'scope-amounts.json'
    amount_payload=json.loads(amount_cache.read_bytes()) if amount_cache.exists() else {}
    if not amount_version:raise ValueError('Amount source version unavailable')
    if amount_payload.get('version') != amount_version:
        values=read('ctmTicketStatusMonitorV44/analytics/team/views/all/approvalTicketRows')
        if not isinstance(values,(dict,list)):raise ValueError('Amount detail source unavailable')
        amount_payload={'version':amount_version,'rows':list(values.values()) if isinstance(values,dict) else values}
        atomic(amount_cache,encode(amount_payload))
    claim,claim_details=build_claim(selected,amount_payload['rows'],approved,
        encode([issue['sourceVersion'],status_version,amount_version,START_DATE]).decode(),issue['generatedAt'])
    names = json.loads((warranty/'issue_subcategories.json').read_text(encoding='utf-8'))['categories']
    cache = ROOT/'.cache'/version
    cache.mkdir(parents=True,exist_ok=True)
    prefix = ISSUE+'/exports/'+version
    shards = [r for r in manifest['shards'] if r[0]=='Z005' and r[1]>=START_DATE[:7]]
    periods = sorted({r[1] for r in shards})
    def cached_get(path):
        target = cache/(hashlib.sha256(path.encode()).hexdigest()+'.json')
        if target.exists():return json.loads(target.read_bytes())
        value = read(prefix+'/'+path)
        if value is None:raise ValueError('Missing export shard: '+path)
        atomic(target, encode(value))
        return value
    def unpack(packed, checksum):
        raw = gzip.decompress(base64.b64decode(packed))
        if hashlib.sha256(raw).hexdigest()!=checksum:
            raise ValueError('Export checksum mismatch')
        return json.loads(raw)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        period_data = dict(zip(periods,pool.map(lambda m:cached_get('periods/Z005_'+m),periods)))
    issues=[]
    for row in shards:
        block=unpack(period_data[row[1]][row[3].replace('/','_')],row[5])
        issues.extend((str(block['tickets'][i[0]][0]),i) for i in block['issues'])
    print(f'Building {len(tickets):,} Pre Delivery Claims and {len(issues):,} Issues...',flush=True)
    overview,details = compile_overview(tickets,issues,master,names,issue['generatedAt'])
    for row in overview['monthly']:
        matching=next((m for m in claim['monthly'] if m['month']==row['month']),{})
        if row['claims']!=matching.get('createdPre') or row['approvedClaims']!=matching.get('approvedCreatedPre'):
            raise ValueError('Overview and Claim Trend populations differ')
    if amount_version != read('ctmTicketStatusMonitorV44/analytics/team/generatedAt'):
        raise ValueError('Amount source changed during build')
    confirmed = read(ISSUE+'/startup/sourceVersion')
    if confirmed != issue['sourceVersion'] or status_version != read('ctmTicketStatusMonitorV44/automation/lastRunFinishedAt'):
        raise ValueError('Source changed during build; previous site retained')
    overview['ticketSourceModifiedAt']=max(r.get('lastSeenAt','') for r in selected)
    overview['approvalSource']='Firebase currentStatus'
    overview['approvalSourceVersion']=status_version
    overview['claimSourceVersion']=claim['sourceVersion']
    content = encode(overview)
    generation = hashlib.sha256(content).hexdigest()[:20]
    if not publish_only:
        assemble_pages(warranty)
    publish_dashboard(warranty,overview,details,claim,generation,issue['sourceVersion'],claim_details)
    print(f'PASS: {len(content):,} byte overview; complete generation {generation}',flush=True)

def publish_dashboard(warranty,overview,details,claim,generation,source_version,claim_details):
    from firebase_admin import credentials,db,initialize_app,delete_app
    app=initialize_app(credentials.Certificate(str(warranty/'firebase-service-account.json')),
                       {'databaseURL':DB},name='pdv-dashboard-publisher')
    try:
        ref=db.reference(SITE,app=app)
        # Store JSON strings to preserve empty arrays and null approval values in RTDB.
        detail_payload={month:{'data':encode([r for r in rows if r[5] is True]).decode('utf-8')}
                        for month,rows in details.items()}
        target=ref.child('generations').child(generation)
        if target.child('ready').get() is not True:
            target.set({'details':detail_payload,'claimDetails':{'data':encode(claim_details).decode()},'ready':True})
        if read(ISSUE+'/startup/sourceVersion')!=source_version:
            raise ValueError('Sources changed before publication; previous startup retained')
        startup={'schema':'pdv-site-v1','generation':generation,'generatedAt':overview['generatedAt'],
                 'builtAt':datetime.now(timezone.utc).isoformat(),'data':encode(overview).decode('utf-8'),
                 'detailBase':DB+'/'+SITE+'/generations/'+generation+'/details/',
                 'sourceVersion':source_version,'claimGeneratedAt':claim['generatedAt']}
        claim_envelope={k:claim[k] for k in ('schema','generatedAt','sourceVersion','scopeStart','scopeBasis')}
        claim_envelope['detailPath']=SITE+'/generations/'+generation+'/claimDetails'
        claim_envelope['data']=encode({k:claim[k] for k in ('monthly','closedIndex')}).decode('utf-8')
        ref.update({'startup':startup,'claimTrendStartup':claim_envelope})
        if read(SITE+'/startup/generation')!=generation:
            raise ValueError('Firebase publication verification failed')
        print('PASS: Firebase dashboard publication verified',flush=True)
    finally:
        delete_app(app)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--warranty-root',type=Path)
    parser.add_argument('--publish-only',action='store_true',help='Update Firebase data without rebuilding local website files')
    parser.add_argument('--check',action='store_true',help='Check local paths and Python dependencies without network or data updates')
    args=parser.parse_args()
    try:
        warranty=resolve_warranty_root(args.warranty_root)
        if args.check:
            check_environment(warranty,args.publish_only)
            print('PASS: PDV local paths and dependencies; no Firebase connection tested')
        else:
            refresh(warranty,args.publish_only)
    except Exception as exc:
        parser.exit(1,'ERROR: PDV update failed: '+str(exc)+'\n')
