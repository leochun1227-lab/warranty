"""Read-only audit: recall chassis -> HANA sales order -> item 000010 material."""
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, db

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
sys.path.insert(0,str(ROOT/'outputs/01a09d12_recall_conflicts'))
from probe_vcust import dsn,query,pyodbc

def save(file,data):
    (OUT/file).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def key(v):return re.sub('[^A-Z0-9]','',str(v or '').upper())
def fetch():
    firebase_admin.initialize_app(credentials.Certificate(str(ROOT/'firebase-service-account.json')),
        {'databaseURL':'https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app','httpTimeout':30})
    snap=db.reference('recallClaim').get()
    save('firebase_snapshot.json',snap)
    ticket_keys={}
    for tid,t in snap['tickets'].items():
        assert t.get('ticketType')=='Z011'
        p,raw=t.get('product',{}),t.get('ticket',{})
        values={key(p.get('chassisNumber')),key(p.get('serialId')),key(raw.get('ChassisNumber')),key(raw.get('SerialID'))}-{''}
        ticket_keys[tid]=sorted(values)
    save('ticket_vehicle_keys.json',ticket_keys)
    allkeys=sorted({v for values in ticket_keys.values() for v in values if len(v)>=8})
    rows=[]
    with pyodbc.connect(dsn,autocommit=True,timeout=15) as conn:
        for start in range(0,len(allkeys),200):
            batch=allkeys[start:start+200]
            inputs=' UNION ALL '.join('SELECT CAST(? AS NVARCHAR(50)) AS INPUT_ID FROM DUMMY' for _ in batch)
            sql='''WITH inputs AS ('''+inputs+'''), units AS (
                SELECT INPUT_ID,INPUT_ID AS SERIAL_ID,'Direct serial' AS MATCH_METHOD FROM inputs
                UNION
                SELECT i.INPUT_ID,z.SERNR AS SERIAL_ID,'VIN mapping' AS MATCH_METHOD
                FROM inputs i JOIN SAPHANADB.ZTSD002 z ON z.MANDT='800' AND z.WERKS='3091' AND z.SERNR2=i.INPUT_ID
            )
            SELECT DISTINCT u.INPUT_ID,u.SERIAL_ID,u.MATCH_METHOD,s.SDAUFNR AS SALES_ORDER,s.POSNR AS SERIAL_ITEM,
                v.VKORG AS SALES_ORG,v.AUART AS ORDER_TYPE,v.ERDAT AS ORDER_DATE,
                p.POSNR AS MODEL_ITEM,p.MATNR AS MATERIAL_CODE,p.ARKTX AS MATERIAL_DESCRIPTION,p.ABGRU AS REJECTION_REASON
            FROM units u
            JOIN SAPHANADB.OBJK o ON o.MANDT='800' AND o.SERNR=u.SERIAL_ID
            JOIN SAPHANADB.SER02 s ON s.MANDT=o.MANDT AND s.OBKNR=o.OBKNR
            JOIN SAPHANADB.VBAK v ON v.MANDT=s.MANDT AND v.VBELN=s.SDAUFNR
            LEFT JOIN SAPHANADB.VBAP p ON p.MANDT=v.MANDT AND p.VBELN=v.VBELN AND p.POSNR='000010'
            ORDER BY u.INPUT_ID,s.SDAUFNR,s.POSNR'''
            got=query(conn,sql,batch)
            rows.extend(got)
            print('Completed key batch',start//200+1,'of',(len(allkeys)+199)//200,'rows',len(got),flush=True)
    save('hana_sales_order_items.json',rows)
    save('query_context.json',{'client':'800','model_item':'000010','sales_org_filter':None,
        'vehicle_mapping_plant':'3091','read_at_utc':datetime.now(timezone.utc).isoformat(),
        'firebase_ticket_count':len(snap['tickets']),'unique_vehicle_keys':len(allkeys),'hana_result_rows':len(rows)})

def summarize():
    rows=json.loads((OUT/'hana_sales_order_items.json').read_text(encoding='utf-8'))
    keys=json.loads((OUT/'ticket_vehicle_keys.json').read_text(encoding='utf-8'))
    indexed=defaultdict(list)
    for r in rows:indexed[r['INPUT_ID']].append(r)
    results=[]
    for tid,values in sorted(keys.items()):
        evidence=list({json.dumps(r,sort_keys=True):r for v in values for r in indexed[v]}.values())
        def classify(e):
            codes=sorted({str(r['MATERIAL_CODE']).strip() for r in e if r['MATERIAL_CODE'] and str(r['MATERIAL_CODE']).strip()})
            status='unique_model' if len(codes)==1 else 'multiple_models' if len(codes)>1 else 'missing_0010_material' if e else 'no_sales_order'
            return {'status':status,'models':codes,'sales_orders':sorted({r['SALES_ORDER'] for r in e})}
        result={'ticket_id':tid,'vehicle_keys':values,'all_sales_orgs':classify(evidence),
            'sales_org_3110':classify([r for r in evidence if r['SALES_ORG']=='3110']),
            'non_rejected':classify([r for r in evidence if not r['REJECTION_REASON']]),'evidence':evidence}
        results.append(result)
    save('ticket_model_results.json',results)
    summary={'total_tickets':len(results),'all_sales_orgs':dict(Counter(r['all_sales_orgs']['status'] for r in results)),
        'sales_org_3110':dict(Counter(r['sales_org_3110']['status'] for r in results)),
        'non_rejected':dict(Counter(r['non_rejected']['status'] for r in results)),
        'order_type_rows':dict(Counter(r['ORDER_TYPE'] for r in rows)),
        'sales_org_rows':dict(Counter(r['SALES_ORG'] for r in rows)),
        'distinct_materials':sorted({r['MATERIAL_CODE'] for r in rows if r['MATERIAL_CODE']}),
        'no_order_ticket_ids':[r['ticket_id'] for r in results if r['all_sales_orgs']['status']=='no_sales_order']}
    save('summary.json',summary)
    print(json.dumps(summary,ensure_ascii=True,indent=2))
    print('Examples:',json.dumps([{k:r[k] for k in ['ticket_id','vehicle_keys','all_sales_orgs','sales_org_3110']} for r in results[:5]],ensure_ascii=True))

if __name__=='__main__':
    if sys.argv[1]=='fetch':fetch()
    elif sys.argv[1]=='summarize':summarize()
