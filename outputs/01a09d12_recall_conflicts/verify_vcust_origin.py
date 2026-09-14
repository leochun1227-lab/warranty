import json
from pathlib import Path
from probe_vcust import dsn, query, pyodbc

out=Path(__file__).resolve().parent
old=json.loads((out/'vcust_original.json').read_text(encoding='utf-8'))
ids=sorted({str(r[0]).zfill(10) for r in old['rows']})
marks=','.join('?' for _ in ids)
with pyodbc.connect(dsn, autocommit=True, timeout=15) as conn:
    rows=query(conn, '''SELECT k.MANDT,k.KUNNR,k.NAME1,k.MCOD1,k.ORT01,k.MCOD3,k.TELF1,k.PSTLZ,
    a.NAME1 AS ADDRESS_NAME1,a.MC_NAME1,a.STREET,a.HOUSE_NUM1,a.CITY1,a.MC_CITY1,a.POST_CODE1,a.TEL_NUMBER
    FROM SAPHANADB.KNA1 k LEFT JOIN SAPHANADB.ADRC a
    ON a.CLIENT=k.MANDT AND a.ADDRNUMBER=k.ADRNR AND a.NATION=''
    WHERE k.MANDT IN ('800','850') AND k.KUNNR IN ('''+marks+')',ids)
    sales=query(conn,'SELECT MANDT,KUNNR,VKORG,VTWEG,SPART FROM SAPHANADB.KNVV WHERE MANDT IN (\'800\',\'850\') AND KUNNR IN ('+marks+')',ids)
    result={'customer_comparison':rows,'sales':sales}
    (out/'vcust_origin_check.json').write_text(json.dumps(result,default=str,ensure_ascii=False),encoding='utf-8')
    for client in ['800','850']:
        by={r['KUNNR']:r for r in rows if r['MANDT']==client}
        counts={}
        for i,field in [(6,'MCOD1'),(6,'MC_NAME1'),(7,'MCOD3'),(7,'MC_CITY1'),(9,'NAME1'),(9,'ADDRESS_NAME1'),(12,'PSTLZ'),(12,'POST_CODE1'),(16,'TELF1'),(16,'TEL_NUMBER')]:
            counts[str(i)+':'+field]=sum(str(r[i] or '').strip()==str(by.get(str(r[0]).zfill(10),{}).get(field) or '').strip() for r in old['rows'])
        oldkeys={(str(r[0]).zfill(10),r[1],r[2],r[3]) for r in old['rows']}
        newkeys={(r['KUNNR'],r['VKORG'],r['VTWEG'],r['SPART']) for r in sales if r['MANDT']==client}
        print(json.dumps({'client':client,'field_matches_of_750':counts,'extra_sales_keys':len(newkeys-oldkeys),'missing_sales_keys':len(oldkeys-newkeys)}))
