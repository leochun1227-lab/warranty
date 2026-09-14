"""Read HANA customer records and audit missing recall postcode matches."""
import json
import sys
import re
import copy
from collections import defaultdict, Counter
from pathlib import Path
from datetime import datetime, timezone
from latest_refresh import ticket_keys, clean, name, email, phone, postcode, compatible, firebase_ref

BASE=Path(__file__).resolve().parent/'hana_followup'
BASE.mkdir(exist_ok=True)
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'01a09d12_recall_conflicts'))
from probe_vcust import dsn,query,pyodbc

def save(file,data):
    (BASE/file).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def read(file):return json.loads((BASE/file).read_text(encoding='utf-8'))

def fetch():
    snap=firebase_ref().get()
    save('firebase_before.json',snap)
    with pyodbc.connect(dsn,autocommit=True,timeout=15) as conn:
        rows=query(conn,'''SELECT k.KUNNR,k.NAME1,k.NAME2,k.ORT01,k.PSTLZ,k.REGIO,k.LAND1,k.SORTL,k.TELF1,k.ADRNR,k.KTOKD,k.LOEVM,
          a.STREET,a.HOUSE_NUM1,a.POST_CODE1,a.CITY1,a.COUNTRY,a.REGION,a.DATE_FROM,a.DATE_TO,
          e.SMTP_ADDR,e.FLGDEFAULT,e.FLG_NOUSE
          FROM SAPHANADB.KNA1 k
          LEFT JOIN SAPHANADB.ADRC a ON a.CLIENT=k.MANDT AND a.ADDRNUMBER=k.ADRNR AND a.NATION=''
          LEFT JOIN SAPHANADB.ADR6 e ON e.CLIENT=k.MANDT AND e.ADDRNUMBER=k.ADRNR AND e.PERSNUMBER=''
          WHERE k.MANDT='800' AND k.LAND1 IN ('AU','NZ') ORDER BY k.KUNNR,a.DATE_FROM,e.CONSNUMBER''')
        save('customers.json',rows)
        metadata={table:query(conn,'SELECT COLUMN_NAME FROM SYS.TABLE_COLUMNS WHERE SCHEMA_NAME=? AND TABLE_NAME=? ORDER BY POSITION',['SAPHANADB',table]) for table in ['EQUI','EQBS','ILOA','ZTSD002','BUT000','CVI_CUST_LINK']}
        save('vehicle_table_columns.json',metadata)
        print(json.dumps({'tickets':len(snap['tickets']),'missing':sum(not clean(t.get('postcode')) for t in snap['tickets'].values()),'customer_rows':len(rows)}))

def plan():
    rows=read('customers.json')
    ix={k:defaultdict(set) for k in ['customer_id','email','name','phone']}
    for i,r in enumerate(rows):
        keys={'customer_id':[r['KUNNR'].lstrip('0')],'email':[email(r['SMTP_ADDR'])] if not clean(r['FLG_NOUSE']) else [],
              'name':[name(r['NAME1']),name(clean(r['NAME1'])+' '+clean(r['NAME2']))],'phone':[phone(r['TELF1'])]}
        for key,values in keys.items():
            for value in values:
                if value:ix[key][value].add(i)
    results=[]
    for tid,t in sorted(read('firebase_before.json')['tickets'].items()):
        if clean(t.get('postcode')):continue
        keys=ticket_keys(t)
        hits={k:set().union(*(ix[k].get(v,set()) for v in keys[k])) for k in ix}
        candidates=hits['customer_id'] | hits['email'] | hits['phone'] | hits['name']
        selected=hits['customer_id'] or {i for i in hits['email']|hits['phone'] if any(compatible(n,name(rows[i]['NAME1']+' '+clean(rows[i]['NAME2']))) for n in keys['name'])}
        selected={i for i in selected if not clean(rows[i]['LOEVM'])}
        pcs={postcode(rows[i]['POST_CODE1']) or postcode(rows[i]['PSTLZ']) for i in selected}-{''}
        status='matched' if len(pcs)==1 else 'conflict' if len(pcs)>1 else 'candidate_only' if candidates else 'no_match'
        evidence=[dict(rows[i],matched_on=[k for k in hits if i in hits[k]],selected=i in selected) for i in sorted(candidates)]
        results.append({'ticket_id':tid,'ticket_name':t.get('ticket',{}).get('TicketName'),'keys':keys,'status':status,'postcode':next(iter(pcs)) if len(pcs)==1 else '', 'evidence':evidence})
    save('match_results.json',results)
    print('statuses',dict(Counter(r['status'] for r in results)))
    for r in results:
        if r['evidence']:
            summary=[{'id':e['KUNNR'],'name':e['NAME1'],'postcode':e['POST_CODE1'],'email':e['SMTP_ADDR'],'on':e['matched_on'],'selected':e['selected']} for e in r['evidence']]
            print(json.dumps({'ticket':r['ticket_id'],'name':r['ticket_name'],'status':r['status'],'postcode':r['postcode'],'evidence':summary[:6],'evidence_count':len(summary)},ensure_ascii=True))

def vehicles():
    ids=sorted({v for r in read('match_results.json') for v in r['keys']['vehicle'] if len(v)>=8})
    marks=','.join('?' for _ in ids)
    with pyodbc.connect(dsn,autocommit=True,timeout=15) as conn:
        equipment=query(conn,'''SELECT DISTINCT e.EQUNR,e.SERNR,z.SERNR2 AS VIN,e.KUNDE,b.KUNNR,b.KDAUF,b.KDPOS,e.MATNR
          FROM SAPHANADB.EQUI e
          LEFT JOIN SAPHANADB.EQBS b ON b.MANDT=e.MANDT AND b.EQUNR=e.EQUNR
          LEFT JOIN SAPHANADB.ZTSD002 z ON z.MANDT=e.MANDT AND z.SERNR=e.SERNR
          WHERE e.MANDT='800' AND (e.SERNR IN ('''+marks+') OR z.SERNR2 IN ('+marks+'))',ids+ids)
        save('equipment_links.json',equipment)
        sales=query(conn,'''SELECT DISTINCT o.SERNR,z.SERNR2 AS VIN,s.SDAUFNR,s.POSNR,v.KUNNR,v.VKORG,v.VTWEG,v.SPART,v.AUART,v.ERDAT
          FROM SAPHANADB.OBJK o
          JOIN SAPHANADB.SER02 s ON s.MANDT=o.MANDT AND s.OBKNR=o.OBKNR
          JOIN SAPHANADB.VBAK v ON v.MANDT=s.MANDT AND v.VBELN=s.SDAUFNR
          LEFT JOIN SAPHANADB.ZTSD002 z ON z.MANDT=o.MANDT AND z.SERNR=o.SERNR
          WHERE o.MANDT='800' AND (o.SERNR IN ('''+marks+') OR z.SERNR2 IN ('+marks+'))',ids+ids)
        save('sales_links.json',sales)
        ids_missing=sorted({v for r in read('match_results.json') for v in r['keys']['customer_id']})
        bp=query(conn,'''SELECT b.PARTNER,b.ZCRM_ID,b.BPEXT,b.NAME_ORG1,b.NAME_FIRST,b.NAME_LAST,l.CUSTOMER
          FROM SAPHANADB.BUT000 b LEFT JOIN SAPHANADB.CVI_CUST_LINK l ON l.CLIENT=b.CLIENT AND l.PARTNER_GUID=b.PARTNER_GUID
          WHERE b.CLIENT='800' AND (b.ZCRM_ID IN ('''+','.join('?' for _ in ids_missing)+') OR b.BPEXT IN ('+','.join('?' for _ in ids_missing)+'))',ids_missing+ids_missing)
        save('bp_links.json',bp)
        print(json.dumps({'equipment_rows':len(equipment),'sales_rows':len(sales),'bp_links':bp},ensure_ascii=True))

def partners():
    orders=sorted({r['SDAUFNR'] for r in read('sales_links.json')})
    with pyodbc.connect(dsn,autocommit=True,timeout=15) as conn:
        rows=query(conn,'''SELECT p.VBELN,p.POSNR,p.PARVW,p.KUNNR,p.ADRNR,a.NAME1,a.NAME2,a.STREET,a.HOUSE_NUM1,a.POST_CODE1,a.CITY1,a.REGION,a.COUNTRY
          FROM SAPHANADB.VBPA p LEFT JOIN SAPHANADB.ADRC a ON a.CLIENT=p.MANDT AND a.ADDRNUMBER=p.ADRNR AND a.NATION=''
          WHERE p.MANDT='800' AND p.VBELN IN ('''+','.join('?' for _ in orders)+')',orders)
        save('order_partners.json',rows)
        print('Order partner/address rows:',len(rows))

def reviewed_plan():
    results=read('match_results.json')
    customers=read('customers.json')
    sales=read('sales_links.json')
    by_customer=defaultdict(list)
    for c in customers:by_customer[c['KUNNR']].append(c)
    plan={}
    def add(r,cids,method,urls=()):
        evidence=[c for cid in cids for c in by_customer[cid]]
        assert evidence and all(not clean(c['LOEVM']) for c in evidence)
        codes={postcode(c['POST_CODE1']) for c in evidence}
        master_codes={postcode(c['PSTLZ']) for c in evidence}
        assert len(codes)==1 and '' not in codes and codes==master_codes
        ids=set(r['keys']['vehicle'])
        vehicle_sales=[s for s in sales if (s['SERNR'] in ids or s['VIN'] in ids) and s['KUNNR'] in cids]
        plan[r['ticket_id']]={'postcode':next(iter(codes)),'keys':r['keys'],'method':method,'customer_ids':cids,
            'customer_evidence':evidence,'vehicle_sales_evidence':vehicle_sales,'branch_confirmation_urls':list(urls)}

    # Exact vehicle plus corroborating private contact handles short names and spelling differences.
    reviewed_contacts={'39775':'0000205503','39860':'0000515731','39896':'0000210339',
        '40061':'0000209793','40175':'0000500619','40663':'0000211577','41556':'0000502314'}
    for r in results:
        tid=r['ticket_id']; ids=set(r['keys']['vehicle']); ns=r['keys']['name']
        linked={s['KUNNR'] for s in sales if s['SERNR'] in ids or s['VIN'] in ids}
        if ns==['SNOWY RIVER']:
            # A brand alone does not identify a dealership, owner or headquarters.
            continue
        if r['status']=='matched':
            add(r,sorted({e['KUNNR'] for e in r['evidence'] if e['selected']}),'Exact contact and matching customer name')
            continue
        if tid in reviewed_contacts:
            cid=reviewed_contacts[tid]
            assert cid in linked
            assert any(email(c['SMTP_ADDR']) in r['keys']['email'] for c in by_customer[cid])
            add(r,[cid],'Exact vehicle and private customer email; reviewed name variation')
            continue
        name_linked={cid for cid in linked if any(compatible(n,name(c['NAME1'])) for n in ns for c in by_customer[cid])}
        if name_linked:
            add(r,sorted(name_linked),'Exact vehicle sales link and matching customer name')
            continue
        if tid=='40930':
            assert '0000502123' in linked and ns==['CHRISTOPHER AVENT']
            assert all(name(c['NAME1'])=='CHRIS AVENT CHRIS AVENT' for c in by_customer['0000502123'])
            add(r,['0000502123'],'Exact vehicle sales link; Chris/Christopher Avent name variation')
            continue
        if ns==['SNOWY RIVER WANGARATTA']:
            assert any(e.endswith('@snowyriverwangaratta.com.au') for e in r['keys']['email'])
            add(r,['0000504302'],'Exact named dealership; HANA address and official branch identity confirmed',
                ['https://snowyriverwangaratta.com.au/'])
            continue
        if ns==['SNOWY RIVER NEWCASTLE STOCK']:
            assert any(e.endswith('@snowyrivernewcastle.com.au') for e in r['keys']['email'])
            add(r,['0000003130','0000003133'],'Explicit Newcastle stock ticket; HANA Newcastle address matches official dealership',
                ['https://snowyrivernewcastle.com.au/','https://snowyrivercaravans.com.au/contact-and-dealers/'])
            continue
        if tid in ['40596','40601','40602']:
            assert 'STOCK' in name(r['ticket_name']) and '0000200032' in linked
            add(r,['0000200032'],'Explicit Coffs Harbour stock ticket; exact vehicle/dealer sales link and branch confirmation',
                ['https://abcocaravans.com.au/terms-of-use/','https://snowyrivercoffsharbour.com.au/caravan/src-20/'])
    save('write_plan.json',plan)
    remaining=[r for r in results if r['ticket_id'] not in plan]
    save('remaining_review.json',remaining)
    print(json.dumps({'confirmed':len(plan),'remaining':len(remaining),'methods':dict(Counter(p['method'] for p in plan.values())),
        'remaining_tickets':[{'id':r['ticket_id'],'name':r['ticket_name']} for r in remaining]},ensure_ascii=True,indent=2))

def apply():
    ref=firebase_ref()
    before=ref.get()
    save('firebase_prewrite.json',before)
    plan=read('write_plan.json')
    def update(current):
        result=copy.deepcopy(current)
        for tid,p in plan.items():
            t=result['tickets'].get(tid)
            assert t and t.get('ticketType')=='Z011' and ticket_keys(t)==p['keys'],'Ticket changed: '+tid
            assert not clean(t.get('postcode')) or t['postcode']==p['postcode'],'Postcode already changed: '+tid
            t['postcode']=p['postcode']
        return result
    committed=ref.transaction(update)
    save('firebase_committed.json',committed)
    after=ref.get()
    save('firebase_after.json',after)
    failures=[tid for tid,p in plan.items() if after['tickets'].get(tid,{}).get('postcode')!=p['postcode']]
    missing=sorted(tid for tid,t in after['tickets'].items() if not clean(t.get('postcode')))
    old_codes={tid:t['postcode'] for tid,t in before['tickets'].items() if clean(t.get('postcode'))}
    preserved=all(after['tickets'].get(tid,{}).get('postcode')==pc for tid,pc in old_codes.items())
    summary={'filled_and_verified':len(plan)-len(failures),'failed_ticket_ids':failures,'total_tickets':len(after['tickets']),
        'remaining_missing':len(missing),'missing_ticket_ids':missing,'previous_postcodes_preserved':preserved,
        'other_fields_unchanged':update(before)==committed,'readback_equals_committed':after==committed,
        'completed_at_utc':datetime.now(timezone.utc).isoformat()}
    save('result_summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='missing_ticket_ids'},indent=2))
    assert not failures and preserved and summary['other_fields_unchanged'] and summary['readback_equals_committed']

if __name__=='__main__':
    {'fetch':fetch,'plan':plan,'vehicles':vehicles,'partners':partners,'review':reviewed_plan,'apply':apply}[sys.argv[1]]()
