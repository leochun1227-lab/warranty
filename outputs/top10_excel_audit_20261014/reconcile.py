import json, pathlib, urllib.request, base64, gzip, hashlib, collections, csv
import openpyxl
P=pathlib.Path(__file__).parent
def readurl(url):
    with urllib.request.urlopen(url, timeout=60) as r: return json.load(r)
s=json.loads((P/'live_startup.json').read_text())
if isinstance(s.get('data'),str): s=json.loads(s['data'])
m=json.loads((P/'live_manifest.json').read_text())
u='https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app/issueSubcategoryAnalysis/v1/c4cTickets_test/exports/'+s['exportVersion']+'/shards/tickets.json'
cache=P/'live_ticket_shards.json'
if not cache.exists(): cache.write_text(json.dumps(readurl(u)),encoding='utf-8')
shards=json.loads(cache.read_text()); live={}
for e in m['ticketShards']:
    raw=gzip.decompress(base64.b64decode(shards[e[2].split('/')[1]]))
    assert len(raw)==e[5] and hashlib.sha256(raw).hexdigest()==e[4]
    d=json.loads(raw)
    assert len(d['tickets'])==e[3]
    for r in d['tickets']:
        assert r[0] not in live
        live[r[0]]={'type':e[0],'month':e[1],'status':r[2],'parts':r[9],'codes':r[10],'categories':r[11],'issues':r[12],'approved':r[13],'created':r[1]}
w=openpyxl.load_workbook(r'C:\Users\Leo.Li\AppData\Local\Temp\ZF8C06456D7698BCB54F44D.xlsx',read_only=True,data_only=True)
rows=list(w.worksheets[0].iter_rows(min_row=11,values_only=True))
statuses={'Sales Order Approved','Partially Picked','Dispatch Parts','Repair in Progress','Repairer Invoiced Received','Repairer Invoiced Processed','Approved Claims Closed (Closed)','Approved Claims Closed'}
def date(x):
    v=str(x or '')
    return v[:10] if len(v)>9 and v[4]=='-' else ''
excel={}; duplicates=[]
for n,r in enumerate(rows,11):
    tid=str(r[1] or '').strip()
    if not tid or not tid.isdigit(): continue
    if tid in excel: duplicates.append(tid)
    excel[tid]={'row':n,'type':'Z005' if r[4]=='Pre Delivery Warranty Claims' else 'Z006','status':r[25],'approved':date(r[19]),'created':date(r[14]),'parts':round(float(r[30] or 0)+float(r[32] or 0),2),'changed':date(r[33])}
eligible={k:r for k,r in excel.items() if r['status'] in statuses and r['approved']}
liveids=set(live); ids=set(eligible)
diff=[]
for tid in sorted(liveids|ids):
    a=eligible.get(tid); b=live.get(tid); raw=excel.get(tid)
    if not a or not b:
        diff.append({'id':tid,'difference':'web_only' if b else 'excel_only','excel':raw,'web':b}); continue
    changes={k:[a[k],b[k]] for k in ['type','approved','status'] if a[k]!=b[k]}
    if b['parts']=='' or abs(a['parts']-float(b['parts']))>.005: changes['parts']=[a['parts'],b['parts']]
    if changes: diff.append({'id':tid,'difference':'fields','changes':changes,'excel_row':a['row']})
periods=[]
for year in ['2024','2025','2026']:
    for period in [year]+([f'{year}-{n:02}' for n in range(1,11)] if year=='2026' else []):
        for typ in ['both','Z006','Z005']:
            aa={k:r for k,r in eligible.items() if r['approved'].startswith(period) and (typ=='both' or r['type']==typ)}
            bb={k:r for k,r in live.items() if r['approved'].startswith(period) and (typ=='both' or r['type']==typ)}
            periods.append({'period':period,'type':typ,'excel':len(aa),'web':len(bb),'difference':len(bb)-len(aa),'excel_parts':round(sum(r['parts'] for r in aa.values()),2),'web_parts':round(sum(float(r['parts'] or 0) for r in bb.values()),2)})
rank=[]
for c,name in s['categories']:
    if c in s['excludedRankingCodes']:continue
    ts={k:r for k,r in live.items() if r['approved'].startswith('2026') and c in r['codes']}
    amount=round(sum(float(r['parts'] or 0) for r in ts.values()),2)
    matched=[excel[k] for k in ts if k in excel]
    rank.append({'code':c,'name':name,'tickets':len(ts),'web_parts':amount,'excel_parts_same_tickets':round(sum(r['parts'] for r in matched),2),'missing_in_excel':len(ts)-len(matched)})
rank.sort(key=lambda r:(-r['tickets'],r['name']))
out={'excel_unique':len(excel),'excel_duplicate_rows':len(duplicates),'excel_eligible':len(eligible),'web_total':len(live),'web_only':len(liveids-ids),'excel_only':len(ids-liveids),'status_counts':dict(collections.Counter(r['status'] for r in excel.values())),'difference_kinds':dict(collections.Counter(d['difference'] for d in diff)),'field_difference_counts':dict(collections.Counter(k for d in diff for k in d.get('changes',{}))),'periods':periods,'top10_2026':rank[:10]}
(P/'reconciliation_summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
(P/'ticket_differences.json').write_text(json.dumps(diff,ensure_ascii=False,indent=2),encoding='utf-8')
(P/'normalized_web_tickets.json').write_text(json.dumps(live),encoding='utf-8')
(P/'normalized_excel_tickets.json').write_text(json.dumps(excel),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
print('DIFF SAMPLES',json.dumps(diff[:12],ensure_ascii=False))
