"""Restore previously approved postcodes lost in a snapshot refresh."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, db

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent.parent
plan=json.loads((BASE/'restore_confirmed_plan.json').read_text(encoding='utf-8'))
sap=json.loads((BASE/'sap_confirmed_plan.json').read_text(encoding='utf-8'))
original=json.loads((BASE/'firebase_before.json').read_text(encoding='utf-8'))['tickets']
assert len(plan)==651 and not set(plan)&set(sap)
out=BASE/('restored_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
out.mkdir()
def save(name,value):
    (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
firebase_admin.initialize_app(credentials.Certificate(str(ROOT/'firebase-service-account.json')),{'databaseURL':'https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app','httpTimeout':30})
ref=db.reference('recallClaim')
before=ref.get()
save('firebase_before.json',before)
def restore(current):
    result=copy.deepcopy(current)
    for tid,postcode in plan.items():
        t=result['tickets'].get(tid)
        assert t and t.get('ticketType')=='Z011', 'Missing or changed ticket: '+tid
        for field in ['serialId','chassisNumber']:
            assert t.get('product',{}).get(field)==original[tid].get('product',{}).get(field), 'Changed vehicle: '+tid
        if not str(t.get('postcode','')).strip():
            t['postcode']=postcode
    return result
ref.transaction(restore)
after=ref.get()
save('firebase_after.json',after)
restored=[tid for tid,pc in plan.items() if after['tickets'][tid].get('postcode')==pc]
sap_verified=[tid for tid,v in sap.items() if after['tickets'][tid].get('postcode')==v['postcode']]
missing=sorted(tid for tid,v in after['tickets'].items() if not str(v.get('postcode','')).strip())
summary={'restored_and_verified':len(restored),'sap_conflicts_verified':len(sap_verified),'total_tickets':len(after['tickets']),'tickets_with_postcode':len(after['tickets'])-len(missing),'tickets_without_postcode':len(missing),'remaining_ticket_ids':missing,'other_fields_unchanged':restore(before)==after,'completed_at':datetime.now(timezone.utc).isoformat(),'audit_directory':str(out)}
save('result_summary.json',summary)
(out/'remaining_ticket_ids.txt').write_text('\n'.join(missing)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k!='remaining_ticket_ids'},indent=2))
assert len(restored)==651 and len(sap_verified)==38
assert summary['other_fields_unchanged']
