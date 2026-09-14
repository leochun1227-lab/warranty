"""Apply the user's confirmed SAP precedence to the 38 conflict tickets."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, db

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent.parent
plan=json.loads((BASE/'sap_confirmed_plan.json').read_text(encoding='utf-8'))
original=json.loads((BASE/'firebase_before.json').read_text(encoding='utf-8'))['tickets']
assert len(plan)==38
assert all(re.fullmatch(r'\d{4}',v['postcode']) for v in plan.values())
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
out=BASE/('sap_confirmed_'+stamp)
out.mkdir()
def save(name,value):
    (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
firebase_admin.initialize_app(credentials.Certificate(str(ROOT/'firebase-service-account.json')),{'databaseURL':'https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app','httpTimeout':30})
ref=db.reference('recallClaim')
before=ref.get()
save('firebase_before.json',before)
tickets=before['tickets']
for tid in plan:
    assert tid in tickets, 'Ticket no longer exists: '+tid
    assert tickets[tid].get('ticketId')==tid and tickets[tid].get('ticketType')=='Z011', 'Ticket identity/type changed: '+tid
    for f in ['serialId','chassisNumber']:
        assert tickets[tid].get('product',{}).get(f)==original[tid].get('product',{}).get(f), 'Vehicle changed: '+tid
updates={f'tickets/{tid}/postcode':v['postcode'] for tid,v in plan.items()}
save('applied_values.json',plan)
ref.update(updates)
after=ref.get()
save('firebase_after.json',after)
failed=[tid for tid,v in plan.items() if after.get('tickets',{}).get(tid,{}).get('postcode')!=v['postcode']]
expected=json.loads(json.dumps(before))
for tid,v in plan.items(): expected['tickets'][tid]['postcode']=v['postcode']
other_unchanged=expected==after
remaining=sorted(tid for tid,v in after['tickets'].items() if not str(v.get('postcode','')).strip())
summary={'completed_at':datetime.now(timezone.utc).isoformat(),'rule':'User confirmed: SAP spreadsheet is authoritative for all 38 conflict tickets.','requested':len(plan),'written_and_verified':len(plan)-len(failed),'failed_ticket_ids':failed,'other_fields_unchanged':other_unchanged,'database_ticket_count':len(after['tickets']),'tickets_with_postcode':len(after['tickets'])-len(remaining),'tickets_without_postcode':len(remaining),'remaining_ticket_ids':remaining,'audit_directory':str(out)}
save('result_summary.json',summary)
(out/'remaining_ticket_ids.txt').write_text('\n'.join(remaining)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k!='remaining_ticket_ids'},ensure_ascii=False,indent=2))
assert not failed, 'Some values did not pass read-back verification.'
assert other_unchanged, 'Concurrent or unrelated differences require review.'
