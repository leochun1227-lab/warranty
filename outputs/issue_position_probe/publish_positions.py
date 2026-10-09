"""Rebuild only from the verified complete source snapshot; no model calls."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from issue_ai import load_master,ticket_id,summarize
from issue_positions import classify_positions
from build_failure_reporting import build_reporting_view
from build_issue_exports import load_export_tickets,enrich_parts_amounts
from build_issue_startup import save_issue_startup
from sync_issue_subcategories import FirebaseStore
OUT=ROOT/'outputs/issue_ai_web_rollout'
master={}
live=json.loads((OUT/'approved_ticket_master.json').read_text(encoding='utf-8'))
for tid,r in live['tickets'].items():master[ticket_id(tid)]={'createdOn':r.get('CreatedOn') or '', 'typeText':r.get('TicketTypeText') or '', 'statusCode':r.get('TicketStatus') or '', 'statusText':r.get('TicketStatusText') or '', 'approvedOn':next((r[k] for k in ['ClaimApprovedOnDateTime','ClaimApprovedOnDate','ClaimApprovedOn','Claim Approved On'] if r.get(k)), '')}
tickets=[];scans={};fetched=[]
for typ in ['Z005','Z006']:
    data=json.loads((OUT/f'{typ}.json').read_text(encoding='utf-8'))
    assert data['scan']['retrievalComplete'] and len(data['tickets'])==data['scan']['uniqueTickets']
    tickets.extend(data['tickets']);scans[typ]=data['scan'];fetched.append(data['fetchedAt'])
results,meta=classify_positions(tickets,master)
summary={**summarize(results),**meta,'sourceRoot':'c4cTickets_test','scans':scans,'retrievalComplete':True,'sourceScanGeneratedAt':max(fetched),'masterSnapshotModifiedAt':live['fetchedAt']}
details=load_export_tickets(ROOT/'outputs/analysis_ticket_base.csv')
for tid,r in master.items():
    details.setdefault(tid,['']*8)[0]=r['statusText']
report_results,report,universe=build_reporting_view(results,summary,master)
(OUT/'approved_report_check.json').write_text(json.dumps({'master':master,'report':report},ensure_ascii=False),encoding='utf-8')
print(json.dumps({'sourceTickets':len(tickets),'issues':len(results),'approvedTickets':len(universe),'approvedIssues':len(report_results),'aiCalls':0}),flush=True)
if '--dry-run' in sys.argv:raise SystemExit(0)
# A separate read-only check compares every month with the actual shared Claim Trend rules.
import subprocess
subprocess.run(['node',str(OUT/'compare-approved.mjs')],cwd=ROOT,check=True)
store=FirebaseStore('https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app',str(ROOT/'firebase-service-account.json'),'c4cTickets_test')
try:
    store.acquire();snapshot=store.publish(results,summary,{},details,master)
    save_issue_startup(ROOT,snapshot)
    (OUT/'live_summary.json').write_text(json.dumps(summary,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'published':True,'snapshotBytes':len(json.dumps(snapshot)),'aiCalls':0,'dimension':snapshot['categoryDimension'],'exportVersion':snapshot['exportVersion']}),flush=True)
finally:store.close()
