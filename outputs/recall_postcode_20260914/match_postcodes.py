import json
import re
from pathlib import Path
from collections import defaultdict, Counter
from openpyxl import load_workbook

OUT = Path(__file__).resolve().parent
DOWNLOADS = Path('C:/Users/Leo.Li/Downloads')

def clean(v):
    if v is None: return ''
    if isinstance(v, float) and v.is_integer(): v = int(v)
    s = str(v).strip()
    return '' if s.lower() in {'', '#', 'none', 'null', 'n/a', 'not assigned', '-'} else s

def ident(v): return re.sub(r'[^A-Z0-9]', '', clean(v).upper())
def name(v): return ' '.join(re.findall(r'[A-Z0-9]+', clean(v).upper().replace('&', ' AND ')))
def names_compatible(a,b):
    # Support repeated SAP names, middle names and joint customer names;
    # this is only corroboration for an exact email/phone match.
    aa=set(a.split())-{'AND'}; bb=set(b.split())-{'AND'}
    return len(aa)>=2 and len(bb)>=2 and (aa<=bb or bb<=aa or (' '+a+' ') in (' '+b+' ') or (' '+b+' ') in (' '+a+' '))
def email(v): return clean(v).lower() if '@' in clean(v) else ''
def phone(v):
    p = re.sub(r'\D', '', clean(v))
    if p.startswith('61') and len(p) == 11: p = '0' + p[2:]
    return p if len(p) >= 8 else ''
def postcode(v):
    s = clean(v)
    if re.fullmatch(r'\d{3,4}', s): return s.zfill(4) if int(s) else ''
    return ''

records=[]
def add(source,sheet,row,pc,country,**keys):
    records.append(dict(source=source,sheet=sheet,row=row,postcode=postcode(pc),raw_postcode=clean(pc),country=clean(country),**{k:sorted(set(x for x in v if x)) for k,v in keys.items()}))

f='VCUST.XLSX'
w=load_workbook(DOWNLOADS/f,read_only=True,data_only=True)
for s in w:
    for i,r in enumerate(s.iter_rows(min_row=2,values_only=True),2):
        add(f,s.title,i,r[12],r[14],sap_id=[ident(r[0])],name=[name(r[6]),name(r[9])],email=[email(r[35])],phone=[phone(r[16])])
w.close()
f='Product_Registrations_Till_17072026.xlsx'
w=load_workbook(DOWNLOADS/f,read_only=True,data_only=True)
for s in w:
    for i,r in enumerate(s.iter_rows(min_row=2,values_only=True),2):
        add(f,s.title,i,r[13],r[4],serial=[ident(r[3])],vin=[ident(r[17])],sf_id=[ident(r[18])],name=[name(clean(r[7])+' '+clean(r[9]))],email=[email(r[6])],phone=[phone(r[10]),phone(r[12])])
w.close()
f='SAPAnalyticsReport(Z578DB6F34E1CDB92C9D1CE).xlsx'
w=load_workbook(DOWNLOADS/f,read_only=True,data_only=True)
for s in w:
    for i,r in enumerate(s.iter_rows(min_row=8,values_only=True),8):
        add(f,s.title,i,r[19],r[17],serial=[ident(r[1]),ident(r[2])],sap_id=[ident(r[8])],name=[name(r[7])],email=[email(r[11])],phone=[phone(r[9]),phone(r[10])])
w.close()
indexes={k:defaultdict(set) for k in ['serial','vin','sap_id','sf_id','name','email','phone']}
for i,r in enumerate(records):
    for kind,idx in indexes.items():
        for key in r.get(kind,[]): idx[key].add(i)

data=json.loads((OUT/'firebase_before.json').read_text(encoding='utf-8'))['tickets']
results=[]
for tid,t in sorted(data.items()):
    raw=t.get('ticket',{}); p=t.get('product',{}); c=t.get('customer',{})
    roles=t.get('roles',{})
    customer_roles=[v for k,v in roles.items() if k=='1001']
    vals={
        'serial':{ident(p.get('serialId')),ident(raw.get('SerialID'))},
        'vin':{ident(p.get('chassisNumber')),ident(raw.get('ChassisNumber'))},
        'sap_id':{ident(v.get(f)) for v in customer_roles for f in ['InvolvedPartyBusinessPartnerID','InvolvedPartyID']},
        'name':{name(v.get('InvolvedPartyName')) for v in customer_roles},
        'email':{email(c.get('email')),email(raw.get('ServiceRequesterEmail'))},
        'phone':{phone(c.get(f)) for f in ['phone','mobile','serviceRequesterPhone']} | {phone(raw.get(f)) for f in ['ServiceRequesterPhone','ServiceRequesterMobile']}
    }
    vals={k:v-{''} for k,v in vals.items()}
    title_name=name(raw.get('TicketName'))
    for vehicle_id in vals['serial']|vals['vin']:
        title_name=re.sub(r'\b'+re.escape(vehicle_id)+r'\b','',title_name)
    title_name=' '.join(title_name.split())
    if title_name: vals['name'].add(title_name)
    hits={kind:set().union(*(indexes[kind].get(v,set()) for v in keys)) for kind,keys in vals.items()}
    strong=set().union(*(hits[k] for k in ['serial','vin','sap_id']))
    contact=hits['email']|hits['phone']
    chosen=strong if strong else contact
    method='exact_vehicle_or_customer_id' if strong else 'exact_email_or_phone'
    valid=[i for i in chosen if records[i]['postcode']]
    codes=sorted({records[i]['postcode'] for i in valid})
    # Unique contact matches must agree with the customer's full name.
    if not strong:
        valid=[i for i in valid if any(names_compatible(a,b) for a in vals['name'] for b in records[i].get('name',[]))]
        # A repairer's general inbox alone does not identify the vehicle owner.
        if any(re.match(r'^(service|warranty|sales|customercare|admin|info)@',e) for e in vals['email']):
            valid=[]
        codes=sorted({records[i]['postcode'] for i in valid})
        method='exact_contact_and_corroborated_customer_name'
    # Reviewed conflicts caused by reused VINs or a different registered owner.
    # Both serial + customer contact corroborate the selected source rows.
    resolved_other_owner={'39648':'3930','39849':'4420','39876':'7315','40001':'4455','40063':'7310','40136':'3178','40236':'4870'}
    if tid in resolved_other_owner:
        target=resolved_other_owner[tid]
        support=[i for i in valid if records[i]['postcode']==target and i in hits['serial'] and i in hits['email']]
        assert support and target in codes
        assert all(i not in hits['email'] and i not in hits['name'] for i in valid if records[i]['postcode']!=target)
        valid=support; codes=[target]; method='reviewed_serial_and_customer_email_excluding_other_owner'
    status='matched' if len(codes)==1 else ('conflicting_postcodes' if len(codes)>1 else ('missing_postcode' if strong else ('unconfirmed_contact_identity' if chosen else 'no_match')))
    result=dict(ticket_id=tid,status=status,postcode=codes[0] if status=='matched' else '',method=method,keys={k:sorted(v) for k,v in vals.items()},existing_postcode=t.get('postcode'),candidate_postcodes=codes,evidence=[dict(records[i],selected=i in valid,matched_on=[k for k,h in hits.items() if i in h]) for i in sorted(chosen)],name_only_candidates=[records[i] for i in sorted(hits['name']-chosen)])
    results.append(result)
(OUT/'match_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'source_records.json').write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'source_rows':dict(Counter(r['source'] for r in records)),'statuses':dict(Counter(r['status'] for r in results)),'methods':dict(Counter(r['method'] for r in results if r['status']=='matched')),'existing_postcode_count':sum(bool(r['existing_postcode']) for r in results),'failed_ids':[r['ticket_id'] for r in results if r['status']!='matched']},indent=2))
