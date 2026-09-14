"""Back up live recall enrichment and verify a normal model-refresh sync."""
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'outputs/recall_postcode_20260914'))
from latest_refresh import firebase_ref,clean

OUT=Path(__file__).resolve().parent/'sync_verification'
OUT.mkdir(exist_ok=True)
def save(file,data):
    (OUT/file).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def read(file):return json.loads((OUT/file).read_text(encoding='utf-8'))

snap=firebase_ref().get()
if sys.argv[1]=='before':
    save('firebase_before.json',snap)
    codes={tid:t['postcode'] for tid,t in snap['tickets'].items() if clean(t.get('postcode'))}
    save('postcodes_before.json',codes)
    print(json.dumps({'total':len(snap['tickets']),'postcodes':len(codes),'models_before':sum(bool(t.get('model')) for t in snap['tickets'].values())}))
else:
    save('firebase_after.json',snap)
    codes=read('postcodes_before.json')
    lost=[tid for tid,pc in codes.items() if snap['tickets'].get(tid,{}).get('postcode')!=pc]
    errors=[]
    counts=Counter()
    for tid,t in snap['tickets'].items():
        lookup=t.get('modelLookup',{})
        status=lookup.get('status','missing_status')
        counts[status]+=1
        material_codes={r['materialCode'] for r in lookup.get('candidates',[])}
        if status=='matched':
            if not t.get('model') or material_codes!={t['model']} or not lookup.get('salesOrders'):errors.append(tid)
        elif t.get('model'):errors.append(tid)
    lookup_meta=snap.get('meta',{}).get('modelLookup',{})
    assert lookup_meta.get('salesOrganization')=='3110' and lookup_meta.get('salesOrderItem')=='000010'
    assert lookup_meta['counts']==dict(counts)
    summary={'total_tickets':len(snap['tickets']),'models':sum(bool(t.get('model')) for t in snap['tickets'].values()),
        'model_statuses':dict(counts),'conflict_ticket_ids':sorted(tid for tid,t in snap['tickets'].items() if t.get('modelLookup',{}).get('status')=='conflict'),
        'without_model_ids':sorted(tid for tid,t in snap['tickets'].items() if not t.get('model')),
        'postcodes_preserved':len(codes)-len(lost),'postcode_losses':lost,'model_errors':errors,
        'verified_at_utc':datetime.now(timezone.utc).isoformat()}
    save('result_summary.json',summary)
    print(json.dumps(summary,indent=2))
    assert not lost and not errors
