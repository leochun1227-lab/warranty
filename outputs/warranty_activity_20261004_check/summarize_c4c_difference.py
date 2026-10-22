import csv
import json
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
DEALERS = {'3135': 'Toowoomba', '3133': 'Newcastle', '3138': 'Bundaberg', '204642': 'GRV', '204670': 'Brisbane'}
PERIODS = {'week': ('2026-09-28', '2026-10-05'), 'mtd': ('2026-10-01', '2026-10-22')}
APPROVED = {'Z9','Y0','Y1','Y2','Y4','YB','Y7'}

def day(value):
    value = str(value or '').strip()
    if not value or value == '#':
        return ''
    if '/' in value[:10]:
        return datetime.strptime(value[:10], '%d/%m/%Y').date().isoformat()
    return datetime.fromisoformat(value.replace('Z', '+00:00')).date().isoformat()

def contributions(row):
    if not row or row.get('TicketType') not in {'Z005','Z006'}:
        return set()
    dealer = str(row.get('WarrantyHandlingDealerID') or '').strip()
    if dealer not in DEALERS:
        return set()
    created = day(row.get('CreatedOn'))
    approved = day(row.get('ClaimApprovedOnDateTime'))
    result = set()
    for period, (start, end) in PERIODS.items():
        if start <= created < end:
            result.add((dealer, period, 'submitted'))
        if start <= approved < end and row.get('TicketStatus') in APPROVED:
            result.add((dealer, period, 'approvals'))
    return result

old_nodes = json.loads((OUT / 'live_tickets.json').read_text(encoding='utf-8'))
old = {str(n['ticket']['TicketID']): n['ticket'] for n in old_nodes if isinstance(n, dict) and isinstance(n.get('ticket'), dict)}
fresh = json.loads((OUT / 'c4c_relevant_tickets.json').read_text(encoding='utf-8'))
assert not fresh['conflicts'], 'Conflicting relevant C4C fields across roles; resolve first'
new = {str(r['TicketID']): r for r in fresh['tickets']}
counts = {source: {dealer: {p: {'submitted': 0, 'approvals': 0} for p in PERIODS} for dealer in DEALERS} for source in ['firebase', 'c4c']}
details = []
changes = []
for tid in sorted(old.keys() | new.keys()):
    old_c = contributions(old.get(tid))
    new_c = contributions(new.get(tid))
    if not old_c and not new_c:
        continue
    for source, row, cs in [('firebase', old.get(tid), old_c), ('c4c', new.get(tid), new_c)]:
        for dealer, period, metric in sorted(cs):
            counts[source][dealer][period][metric] += 1
            details.append({'source': source, 'ticket_id': tid, 'handling_dealer': dealer, 'period': period,
                            'metric': metric, 'created_on': day(row.get('CreatedOn')),
                            'approved_on': day(row.get('ClaimApprovedOnDateTime')), 'status': row.get('TicketStatus')})
    if old_c != new_c:
        keys = ['TicketType','WarrantyHandlingDealerID','CreatedOn','ClaimApprovedOnDateTime','TicketStatus']
        before = {k: old.get(tid, {}).get(k) for k in keys}
        after = {k: new.get(tid, {}).get(k) for k in keys}
        changes.append({'ticket_id': tid, 'before': before, 'after': after,
                        'removed': sorted(old_c-new_c), 'added': sorted(new_c-old_c),
                        'absent_from_c4c': tid not in new, 'absent_from_firebase': tid not in old})

baseline = json.loads((OUT / 'handling_dealer_summary_20261021.json').read_text(encoding='utf-8'))
for dealer in DEALERS:
    for period in PERIODS:
        c = counts['firebase'][dealer][period]
        assert [c['submitted'],c['approvals']] == baseline['counts'][dealer][period]
totals = {source: {period: {metric: sum(counts[source][d][period][metric] for d in DEALERS)
                           for metric in ['submitted','approvals']} for period in PERIODS} for source in counts}
report = {'counts': counts, 'totals': totals, 'changed_ticket_count': len(changes), 'changes': changes,
          'c4c_scans': fresh['scans'], 'firebase_sync_at': baseline['source_sync_at'],
          'handling_dealer_field': 'WarrantyHandlingDealerID', 'periods': PERIODS,
          'retrieval_limitation': 'Read-only offset pagination, no transaction snapshot guarantee.'}
(OUT/'c4c_vs_firebase_summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
with (OUT/'c4c_vs_firebase_counted_tickets.csv').open('w', encoding='utf-8-sig', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(details[0]))
    writer.writeheader()
    writer.writerows(details)
print(json.dumps(report, indent=2))
