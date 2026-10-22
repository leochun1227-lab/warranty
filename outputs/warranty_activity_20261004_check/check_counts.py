import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEALERS = {'3135': 'Toowoomba', '3133': 'Newcastle', '3138': 'Bundaberg', '204642': 'GRV', '204670': 'Brisbane'}
APPROVED = {'Z9', 'Y0', 'Y1', 'Y2', 'Y4', 'YB', 'Y7'}

def day(value):
    value = str(value or '').strip()
    if not value or value == '#':
        return ''
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d/%m/%Y %H:%M:%S', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f'Unexpected date format: {value}')

nodes = json.loads((ROOT / 'live_tickets.json').read_text(encoding='utf-8'))
rows = [n.get('ticket', n) for n in (nodes.values() if isinstance(nodes, dict) else nodes) if isinstance(n, dict)]
selected = [r for r in rows if str(r.get('DealerID')) in DEALERS]
print('Source ticket rows:', len(rows))
print('Selected dealer types:', Counter(r.get('TicketTypeText') for r in selected))
print('Sample dates:', [(r.get('CreatedOn'), r.get('ClaimApprovedOnDateTime')) for r in selected[:3]])
seen = set()
counts = {k: {p: [0, 0] for p in ('week', 'mtd')} for k in DEALERS}
details = []
excluded_status = []
for r in selected:
    if r.get('TicketType') not in ('Z005', 'Z006'):
        continue
    tid = str(r['TicketID'])
    if tid in seen:
        raise ValueError(f'Duplicate ticket {tid}')
    seen.add(tid)
    created, approved = day(r.get('CreatedOn')), day(r.get('ClaimApprovedOnDateTime'))
    eligible = r.get('TicketStatus') in APPROVED
    dealer = str(r['DealerID'])
    if '2026-09-28' <= approved < '2026-10-05' and not eligible:
        excluded_status.append({'ticket': tid, 'dealer': dealer, 'approved': approved, 'status': r.get('TicketStatusText')})
    for period, start in [('week', '2026-09-28'), ('mtd', '2026-10-01')]:
        submitted = start <= created < '2026-10-05'
        approved_in_period = start <= approved < '2026-10-05' and eligible
        counts[dealer][period][0] += int(submitted)
        counts[dealer][period][1] += int(approved_in_period)
        if submitted or approved_in_period:
            details.append({'period': period, 'dealer': DEALERS[dealer], 'dealer_id': dealer, 'ticket_id': tid,
                            'type': r.get('TicketTypeText'), 'created_on': created, 'approved_on': approved,
                            'status': r.get('TicketStatusText'), 'submitted_count': int(submitted), 'approved_count': int(approved_in_period)})
print('LIVE COUNTS:', json.dumps(counts))
print('Excluded current status:', json.dumps(excluded_status))
totals = {p: [sum(c[p][i] for c in counts.values()) for i in (0, 1)] for p in ('week', 'mtd')}
print('TOTALS:', totals)
result = {'period_end': '2026-10-04', 'dealers': DEALERS, 'counts': counts, 'totals': totals,
          'source': 'c4cTickets_test/tickets', 'source_sync_date': '2026-10-21',
          'scope': ['Z005', 'Z006'], 'approved_status_codes': sorted(APPROVED), 'excluded_status': excluded_status}
(ROOT / 'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
with (ROOT / 'counted_tickets.csv').open('w', encoding='utf-8-sig', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(details[0]))
    writer.writeheader()
    writer.writerows(details)

with (ROOT.parent / 'analysis_ticket_base.csv').open(encoding='utf-8-sig', newline='') as f:
    old = list(csv.DictReader(f))
old_status = {'Sales Order Approved', 'Partially Picked', 'Dispatch Parts', 'Repair in Progress', 'Repairer Invoiced Received', 'Repairer Invoiced Processed', 'Approved Claims Closed', 'Approved Claims Closed (Closed)'}
old_counts = {}
for dealer in DEALERS:
    matching = [r for r in old if r['Dealer'] == dealer and r['Ticket Type'] in ('In Field Warranty Claims', 'Pre Delivery Warranty Claims')]
    old_counts[dealer] = {p: [sum(start <= r['Created On'] < '2026-10-05' for r in matching),
                              sum(start <= r['Claim Approved On'] < '2026-10-05' and r['Status'] in old_status for r in matching)]
                         for p, start in [('week', '2026-09-28'), ('mtd', '2026-10-01')]}
print('OCT 5 SNAPSHOT:', json.dumps(old_counts))
print('Historical snapshot matches live:', old_counts == counts)
live_by_id = {str(r.get('TicketID')): r for r in rows}
for dealer in DEALERS:
    for metric, field in [('submitted', 'Created On'), ('approved', 'Claim Approved On')]:
        old_ids = {r['C4C Ticket ID'] for r in old if r['Dealer'] == dealer and r['Ticket Type'] in ('In Field Warranty Claims', 'Pre Delivery Warranty Claims')
                   and '2026-09-28' <= r[field] < '2026-10-05' and (metric == 'submitted' or r['Status'] in old_status)}
        new_ids = {r['ticket_id'] for r in details if r['period'] == 'week' and r['dealer_id'] == dealer and r[metric + '_count']}
        for tid in sorted(old_ids ^ new_ids):
            r = live_by_id.get(tid, {})
            print('DIFFERENCE:', dealer, metric, tid, 'old' if tid in old_ids else 'new',
                  {k: r.get(k) for k in ('DealerID', 'DealerName', 'TicketTypeText', 'CreatedOn', 'ClaimApprovedOnDateTime', 'TicketStatusText')})
