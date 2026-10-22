"""Read-only, scoped monthly approval source extraction."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT / 'outputs/warranty_activity_20261004_check'))
import compare_c4c as c4c

GROUPS = {'Green RV': ['204642','204670'], 'Leisure Lion': ['3133','3135','3138'], 'Caravan Hub': ['200035','204677']}
c4c.TARGETS = {dealer for dealers in GROUPS.values() for dealer in dealers}
c4c.RETAIN = ['TicketID','TicketType','WarrantyHandlingDealerID','CreatedOn','ClaimApprovedOnDateTime','TicketStatus','AmountIncludingTax']

def relevant(value):
    text = str(value or '')
    if not text:
        return False
    try:
        day = datetime.strptime(text[:10], '%d/%m/%Y').date().isoformat() if '/' in text[:10] else text[:10]
        return '2025-01-01' <= day < '2026-10-22'
    except ValueError:
        return False

c4c.relevant_date = relevant

if __name__ == '__main__':
    # The existing reader immediately discards unrelated records and all customer/contact fields.
    with ThreadPoolExecutor(max_workers=3) as pool:
        scans = list(pool.map(lambda role: c4c.stream_role(role, set()), ['1001','40','43']))
    tickets, conflicts = {}, []
    for rows, metadata in scans:
        for row in rows:
            tid = str(row['TicketID'])
            if tid in tickets and tickets[tid] != row:
                conflicts.append({'ticket_id': tid, 'fields': [k for k in c4c.RETAIN if tickets[tid].get(k) != row.get(k)]})
            tickets[tid] = row
    payload = {'groups': GROUPS, 'source': c4c.ENDPOINT, 'scans': [m for _,m in scans],
               'conflicts': conflicts, 'tickets': list(tickets.values())}
    (OUT / 'c4c_scoped_tickets.json').write_text(json.dumps(payload), encoding='utf-8')
    print(json.dumps({'stage': 'C4C complete', 'relevant_tickets': len(tickets), 'conflicts': conflicts[:15], 'scans': payload['scans']}), flush=True)
    if conflicts:
        raise RuntimeError('C4C relevant fields changed during retrieval; review conflicts')
    # SAP amount lookup uses the same existing Approved Cost source as the dashboard.
    base = 'https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app'
    path = '/ctmTicketStatusMonitorV44/analytics/approvedCost/sapPoShortText/latest.json'
    with urlopen(Request(base + path, headers={'Accept':'application/json'}), timeout=120) as response:
        sap = json.load(response)
    matches = {}
    for key, record in (sap.get('byTicket') or {}).items():
        tid = str(record.get('ticketNumber') or record.get('ticketId') or key).removeprefix('ticket_')
        if tid in tickets:
            matches[tid] = {k: record.get(k) for k in ['ticketNumber','amount','netOrderValue','currencies','poItemCount','poDocuments']}
    result = {'source': base + path, 'generatedAt': sap.get('generatedAt'), 'metadata': sap.get('source'), 'byTicket': matches}
    (OUT / 'sap_scoped_approved_cost.json').write_text(json.dumps(result), encoding='utf-8')
    print(json.dumps({'stage': 'SAP complete', 'matched_records': len(matches), 'generatedAt': sap.get('generatedAt')}), flush=True)
