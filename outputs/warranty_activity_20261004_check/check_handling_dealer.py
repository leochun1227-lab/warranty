import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEALERS = {'3135': 'Toowoomba', '3133': 'Newcastle', '3138': 'Bundaberg', '204642': 'GRV', '204670': 'Brisbane'}
PERIODS = {'week': ('2026-09-28', '2026-10-05'), 'mtd': ('2026-10-01', '2026-10-22')}
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
counts = {dealer: {p: [0, 0] for p in PERIODS} for dealer in DEALERS}
details, excluded_status = [], []
seen = set()
for row in rows:
    dealer = str(row.get('WarrantyHandlingDealerID') or '').strip()
    if dealer not in DEALERS or row.get('TicketType') not in ('Z005', 'Z006'):
        continue
    tid = str(row['TicketID'])
    assert tid not in seen, f'Duplicate ticket {tid}'
    seen.add(tid)
    created = day(row.get('CreatedOn'))
    approved = day(row.get('ClaimApprovedOnDateTime'))
    eligible = row.get('TicketStatus') in APPROVED
    for period, (start, end) in PERIODS.items():
        submitted = int(start <= created < end)
        approval = int(start <= approved < end and eligible)
        counts[dealer][period][0] += submitted
        counts[dealer][period][1] += approval
        if start <= approved < end and not eligible:
            excluded_status.append({'period': period, 'ticket_id': tid, 'handling_dealer': dealer, 'status': row.get('TicketStatusText')})
        if submitted or approval:
            details.append({'period': period, 'dealership': DEALERS[dealer], 'warranty_handling_dealer_assign': dealer,
                            'ticket_id': tid, 'ticket_type': row.get('TicketTypeText'), 'created_on': created,
                            'claim_approved_on': approved, 'current_status': row.get('TicketStatusText'),
                            'submitted_count': submitted, 'approvals_count': approval})

totals = {p: [sum(c[p][i] for c in counts.values()) for i in (0, 1)] for p in PERIODS}
# Independently reconcile the saved detail contributions to each summary cell.
for dealer in DEALERS:
    for period in PERIODS:
        matching = [d for d in details if d['warranty_handling_dealer_assign'] == dealer and d['period'] == period]
        assert counts[dealer][period] == [sum(d['submitted_count'] for d in matching), sum(d['approvals_count'] for d in matching)]
result = {'source_sync_at': '2026-10-21T00:17:52+00:00', 'source': 'c4cTickets_test/tickets',
          'dealer_field': 'WarrantyHandlingDealerID', 'dealer_field_label': 'Warranty Handling Dealer(Assign)',
          'periods_start_inclusive_end_exclusive': PERIODS, 'dealers': DEALERS,
          'scope': ['Z005', 'Z006'], 'approved_status_codes': sorted(APPROVED),
          'counts': counts, 'totals': totals, 'excluded_approval_status': excluded_status}
(ROOT / 'handling_dealer_summary_20261021.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
with (ROOT / 'handling_dealer_counted_tickets_20261021.csv').open('w', encoding='utf-8-sig', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(details[0]))
    writer.writeheader()
    writer.writerows(details)
print(json.dumps(result, indent=2))
print('Unique included tickets:', len({d['ticket_id'] for d in details}))
