import json
from pathlib import Path
from urllib.request import urlopen
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

OUT=Path(__file__).resolve().parent
BASE='https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app'
TEAM='/ctmTicketStatusMonitorV44/analytics/team/views/all'
TARGETS={'204642','204670','3133','3135','3138','200035','204677'}
def get(path):
    with urlopen(BASE+path+'.json',timeout=180) as r: return json.load(r)
def values(x): return x.values() if isinstance(x,dict) else x or []
if __name__=='__main__':
    paths=['/ctmTicketStatusMonitorV44/analytics/team/generatedAt',TEAM+'/approvalTicketRows','/c4cTickets_test/tickets', '/ctmTicketStatusMonitorV44/analytics/approvedCost/sapPoShortText/latest/generatedAt']
    with ThreadPoolExecutor(max_workers=4) as p: stamp,rows,raw,sapstamp=list(p.map(get,paths))
    scoped={}
    for entry in values(raw):
        if not isinstance(entry,dict): continue
        t=entry.get('ticket') or entry
        if str(t.get('WarrantyHandlingDealerID') or '') not in TARGETS: continue
        scoped[str(t.get('TicketID'))]={k:t.get(k) for k in ['TicketID','WarrantyHandlingDealerID','TicketType','TicketStatus','ClaimApprovedOnDateTime','ClaimTotalAmount','AmountIncludingTax']}
    keep=[]
    for r in values(rows):
        if not isinstance(r,dict) or str(r.get('id')) not in scoped: continue
        if r.get('decisionKey')!='approved' or not '2025-01-01'<=str(r.get('decisionDate') or '')<'2026-10-22':continue
        keep.append({**{k:r.get(k) for k in ['id','claim','status','amount','exportAmount','decisionDate','decisionKey','decisionSource','claimApprovedOn']},'raw':scoped[str(r['id'])]})
    endstamp=get('/ctmTicketStatusMonitorV44/analytics/team/generatedAt')
    assert stamp,'Website source timestamp is missing'
    assert stamp==endstamp,'Website snapshot changed during extraction'
    result={'source':BASE+TEAM+'/approvalTicketRows.json','generatedAt':stamp,'retrievedAt':datetime.now(timezone.utc).isoformat(),'sapGeneratedAt':sapstamp,'rows':keep}
    (OUT/'website_scoped_approval_rows.json').write_text(json.dumps(result),encoding='utf-8')
    print(json.dumps({'generatedAt':stamp,'rows':len(keep),'rawMatched':len(scoped),'fields':list(rows[0]) if isinstance(rows,list) and rows else [],'statusCounts':{s:sum(r['raw']['TicketStatus']==s for r in keep) for s in sorted({r['raw']['TicketStatus'] for r in keep})}}))
