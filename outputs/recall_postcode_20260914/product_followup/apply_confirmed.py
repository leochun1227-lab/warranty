"""Fill missing recall postcodes confirmed by product registration and SAP."""
import copy
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, db

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
matches = json.loads((BASE / 'match_results.json').read_text(encoding='utf-8'))
original = json.loads((BASE / 'firebase_before.json').read_text(encoding='utf-8'))['tickets']
sources = json.loads((BASE.parent / 'source_records.json').read_text(encoding='utf-8'))
plan = {r['ticket_id']: r for r in matches if r['status'] == 'matched'}
assert len(plan) == 5
for tid, row in plan.items():
    sap_codes = {s['postcode'] for s in sources if s['source'].startswith('SAP')
                 and set(row['ticket_ids']) & set(s.get('serial', [])) and s['postcode']}
    assert sap_codes == {row['postcode']}, 'SAP confirmation changed: ' + tid

out = BASE / ('applied_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
out.mkdir()

def save(name, value):
    (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

firebase_admin.initialize_app(credentials.Certificate(str(ROOT / 'firebase-service-account.json')),
    {'databaseURL': 'https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app', 'httpTimeout': 30})
ref = db.reference('recallClaim')
before = ref.get()
save('firebase_before.json', before)
save('write_plan.json', plan)

def apply(current):
    result = copy.deepcopy(current)
    for tid, row in plan.items():
        ticket = result['tickets'].get(tid)
        assert ticket and ticket.get('ticketType') == 'Z011', 'Ticket changed: ' + tid
        for field in ['serialId', 'chassisNumber']:
            assert ticket.get('product', {}).get(field) == original[tid].get('product', {}).get(field), 'Vehicle changed: ' + tid
        old = ticket.get('postcode')
        assert old is None or not str(old).strip() or old == row['postcode'], 'Postcode changed: ' + tid
        ticket['postcode'] = row['postcode']
    return result

committed = ref.transaction(apply)
after = ref.get()
save('firebase_after.json', after)
verified = [tid for tid, r in plan.items() if after['tickets'][tid].get('postcode') == r['postcode']]
missing = sorted(tid for tid, t in after['tickets'].items() if not str(t.get('postcode') or '').strip())
reasons = {r['ticket_id']: r['status'] for r in matches if r['ticket_id'] in missing}
summary = {
    'newly_written_and_verified': len([tid for tid in verified if not str(before['tickets'][tid].get('postcode') or '').strip()]),
    'verified_ticket_postcodes': {tid: plan[tid]['postcode'] for tid in verified},
    'write_failures': sorted(set(plan) - set(verified)),
    'total_tickets': len(after['tickets']),
    'tickets_with_postcode': len(after['tickets']) - len(missing),
    'tickets_without_postcode': len(missing),
    'remaining_ticket_ids': missing,
    'remaining_reasons': dict(Counter(reasons.values())),
    'other_fields_unchanged': apply(before) == committed,
    'readback_equals_committed': after == committed,
    'completed_at': datetime.now(timezone.utc).isoformat(),
    'audit_directory': str(out),
}
save('result_summary.json', summary)
(out / 'remaining_ticket_ids.txt').write_text('\n'.join(missing) + '\n', encoding='utf-8')
(out / 'remaining_tickets.csv').write_text('Ticket ID,Reason\n' + '\n'.join(tid + ',' + reasons.get(tid, 'New ticket after analysis') for tid in missing) + '\n', encoding='utf-8-sig')
print(json.dumps({k: v for k, v in summary.items() if k != 'remaining_ticket_ids'}, indent=2))
assert len(verified) == len(plan)
assert summary['other_fields_unchanged'] and summary['readback_equals_committed']
