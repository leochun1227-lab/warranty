import json
from datetime import datetime, timezone
from pathlib import Path
from probe_vcust import dsn, query, pyodbc

OUT = Path(__file__).resolve().parent
original = json.loads((OUT / 'vcust_original.json').read_text(encoding='utf-8'))
ids = sorted({str(r[0]).zfill(10) for r in original['rows']})
with pyodbc.connect(dsn, autocommit=True, timeout=15) as conn:
    customers = query(conn, '''
      SELECT k.KUNNR,k.NAME1,k.NAME2,k.MCOD1,k.ORT01,k.PSTLZ,k.REGIO,k.LAND1,k.SORTL,k.TELF1,
             k.ADRNR,k.KTOKD,k.ERDAT,k.AEDAT,k.AUFSD,k.LIFSD,k.FAKSD,k.LOEVM,k.NODEL,
             k.KUKLA,k.BRSCH,k.CVP_XBLCK,k.BEGRU,
             a.STREET,a.HOUSE_NUM1,a.POST_CODE1,a.CITY1,a.COUNTRY,a.REGION,
             a.DATE_FROM,a.DATE_TO,a.NATION,
             e.SMTP_ADDR,e.FLGDEFAULT,e.FLG_NOUSE
      FROM SAPHANADB.KNA1 k
      LEFT JOIN SAPHANADB.ADRC a ON a.CLIENT=k.MANDT AND a.ADDRNUMBER=k.ADRNR AND a.NATION=''
      LEFT JOIN SAPHANADB.ADR6 e ON e.CLIENT=k.MANDT AND e.ADDRNUMBER=k.ADRNR AND e.PERSNUMBER=''
      WHERE k.MANDT='800' AND k.LAND1='AU'
      ORDER BY k.KUNNR,a.DATE_FROM,e.CONSNUMBER
    ''')
    sales = query(conn, 'SELECT * FROM SAPHANADB.KNVV WHERE MANDT=\'800\' AND KUNNR IN (' + ','.join('?' for _ in ids) + ') ORDER BY KUNNR,VKORG,VTWEG,SPART', ids)
    result = {'extracted_at_utc':datetime.now(timezone.utc).isoformat(),'client':'800','customers':customers,'original_customer_sales':sales}
    (OUT / 'vcust_live.json').write_text(json.dumps(result,default=str,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'customer_address_email_rows':len(customers),'unique_customers':len({r['KUNNR'] for r in customers}),'original_customer_sales_rows':len(sales),'extracted_at_utc':result['extracted_at_utc']}))
