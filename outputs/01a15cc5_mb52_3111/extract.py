import sys, json
from decimal import Decimal
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pyodbc
from sap_material_prices import DEFAULT_DSN, DEFAULT_MANDT
out=Path(__file__).parent
conn=pyodbc.connect(DEFAULT_DSN,autocommit=True,timeout=20)
cur=conn.cursor()
scope="MANDT=? AND WERKS='3111' AND (MATNR LIKE 'Z%' OR MATNR LIKE 'D%')"
cur.execute(f'SELECT COUNT(*) FROM SAPHANADB.NSDM_V_MKOL WHERE {scope}',DEFAULT_MANDT)
assert cur.fetchone()[0]==0,'Additional consignment stocks require inclusion'
sql=f'''
WITH stock AS (
 SELECT MANDT, MATNR, WERKS, LGORT, '' CHARG, '' SOBKZ, '' VBELN, '' POSNR,
 LABST unrestricted, INSME quality, SPEME blocked, UMLME transfer, EINME restricted, RETME return_qty
 FROM SAPHANADB.NSDM_V_MARD WHERE {scope}
 AND (LABST<>0 OR INSME<>0 OR SPEME<>0 OR UMLME<>0 OR EINME<>0 OR RETME<>0)
 UNION ALL
 SELECT MANDT, MATNR, WERKS, LGORT, CHARG, SOBKZ, VBELN, POSNR,
 KALAB, KAINS, KASPE, 0, KAEIN, 0
 FROM SAPHANADB.NSDM_V_MSKA WHERE {scope}
 AND (KALAB<>0 OR KAINS<>0 OR KASPE<>0 OR KAEIN<>0)
)
SELECT s.MATNR "Material", COALESCE(e.MAKTX,z.MAKTX,'') "Material Description",
 COALESCE(z.MAKTX,'') "Material Description CN",s.WERKS "Plant",
 s.LGORT "Storage Location",COALESCE(l.LGOBE,'') "Storage Location Description",
 s.CHARG "Batch",s.SOBKZ "Special Stock",s.VBELN "Sales Order",s.POSNR "Sales Order Item",
 a.MEINS "Base Unit",s.unrestricted "Unrestricted",s.quality "Quality Inspection",
 s.blocked "Blocked",s.transfer "Stock in Transfer",s.restricted "Restricted Use",s.return_qty "Returns"
FROM stock s
LEFT JOIN SAPHANADB.MARA a ON a.MANDT=s.MANDT AND a.MATNR=s.MATNR
LEFT JOIN SAPHANADB.MAKT e ON e.MANDT=s.MANDT AND e.MATNR=s.MATNR AND e.SPRAS='E'
LEFT JOIN SAPHANADB.MAKT z ON z.MANDT=s.MANDT AND z.MATNR=s.MATNR AND z.SPRAS='1'
LEFT JOIN SAPHANADB.T001L l ON l.MANDT=s.MANDT AND l.WERKS=s.WERKS AND l.LGORT=s.LGORT
ORDER BY s.MATNR,s.LGORT,s.SOBKZ,s.VBELN,s.POSNR,s.CHARG
'''
cur.execute(sql,DEFAULT_MANDT,DEFAULT_MANDT)
headers=[c[0] for c in cur.description]
rows=[[float(v) if isinstance(v,Decimal) else v for v in row] for row in cur.fetchall()]
assert rows
assert all(r[0].startswith(('Z','D')) and r[3]=='3111' for r in rows)
assert len({(r[0],*r[3:5],*r[6:10]) for r in rows})==len(rows)
controls=[]
for table,fields in [('NSDM_V_MARD',['LABST','INSME','SPEME','UMLME','EINME','RETME']),('NSDM_V_MSKA',['KALAB','KAINS','KASPE','0','KAEIN','0'])]:
 cur.execute('SELECT '+','.join('COALESCE(SUM('+f+'),0)' for f in fields)+f' FROM SAPHANADB.{table} WHERE {scope}',DEFAULT_MANDT)
 controls.append([float(v) for v in cur.fetchone()])
actual=[sum(row[c] for row in rows) for c in range(11,17)]
expected=[sum(values[c] for values in controls) for c in range(6)]
assert actual==expected,(actual,expected)
payload={'headers':headers,'rows':rows,'meta':{'createdAt':datetime.now(ZoneInfo('Australia/Sydney')).isoformat(),'client':DEFAULT_MANDT,'plant':'3111','prefixes':['Z','D'],'zeroStockExcluded':True,'rows':len(rows),'materials':len({r[0] for r in rows}),'quantityControls':expected}}
(out/'stock.json').write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
(out/'query.sql').write_text(sql,encoding='utf-8')
print(json.dumps(payload['meta']))
conn.close()
