import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pyodbc
from sap_material_prices import DEFAULT_DSN, DEFAULT_MANDT
conn = pyodbc.connect(DEFAULT_DSN, autocommit=True, timeout=20)
cur=conn.cursor()
where="MANDT=? AND WERKS='3111' AND (MATNR LIKE 'Z%' OR MATNR LIKE 'D%')"
queries={
 'mard':f'SELECT COUNT(*), SUM(LABST), SUM(INSME), SUM(SPEME) FROM SAPHANADB.MARD WHERE {where}',
 'current_stock':f'SELECT COUNT(*), COUNT(DISTINCT MATNR), SUM(LABST), SUM(INSME), SUM(SPEME) FROM SAPHANADB.NSDM_V_MARD WHERE {where}',
 'batch':f'SELECT COUNT(*), SUM(CLABS), SUM(CINSM), SUM(CSPEM) FROM SAPHANADB.NSDM_V_MCHB WHERE {where}',
 'sample':f'SELECT TOP 5 MATNR, LGORT, LABST, INSME, SPEME FROM SAPHANADB.NSDM_V_MARD WHERE {where} AND LABST<>0 ORDER BY MATNR, LGORT',
 'marc':f'SELECT COUNT(*), SUM(UMLMC), SUM(TRAME) FROM SAPHANADB.MARC WHERE {where}',
 'descriptions':"SELECT SPRAS, COUNT(*) FROM SAPHANADB.MAKT WHERE MANDT=? AND (MATNR LIKE 'Z%' OR MATNR LIKE 'D%') GROUP BY SPRAS",
 'special':f'SELECT SOBKZ, COUNT(*), SUM(KALAB), SUM(KAINS), SUM(KASPE) FROM SAPHANADB.MSKA WHERE {where} GROUP BY SOBKZ',
}
queries={
 'sales_order_stock':f'SELECT SOBKZ, COUNT(*), SUM(KALAB), SUM(KAINS), SUM(KASPE) FROM SAPHANADB.NSDM_V_MSKA WHERE {where} GROUP BY SOBKZ',
 'supplier_stock':f'SELECT SOBKZ, COUNT(*), SUM(LBLAB), SUM(LBINS) FROM SAPHANADB.NSDM_V_MSLB WHERE {where} GROUP BY SOBKZ',
 'customer_stock':f'SELECT SOBKZ, COUNT(*), SUM(KULAB), SUM(KUINS) FROM SAPHANADB.NSDM_V_MSKU WHERE {where} GROUP BY SOBKZ',
 'consignment_stock':f'SELECT SOBKZ, COUNT(*), SUM(KOLAB), SUM(KOINS), SUM(KOSPE) FROM SAPHANADB.NSDM_V_MKOL WHERE {where} GROUP BY SOBKZ',
 'project_stock':f'SELECT SOBKZ, COUNT(*), SUM(PRLAB), SUM(PRINS), SUM(PRSPE) FROM SAPHANADB.NSDM_V_MSPR WHERE {where} GROUP BY SOBKZ',
 'plant_stock':f'SELECT COUNT(*), SUM(UMLMC), SUM(TRAME) FROM SAPHANADB.NSDM_V_MARC WHERE {where}',
 'special_fields':"SELECT COLUMN_NAME FROM SYS.VIEW_COLUMNS WHERE SCHEMA_NAME='SAPHANADB' AND VIEW_NAME='NSDM_V_MSKA' ORDER BY POSITION",
 'warehouse_counts':f'SELECT LEFT(MATNR,1),COUNT(*), SUM(CASE WHEN LABST<>0 OR UMLME<>0 OR INSME<>0 OR EINME<>0 OR SPEME<>0 OR RETME<>0 THEN 1 ELSE 0 END) FROM SAPHANADB.NSDM_V_MARD WHERE {where} GROUP BY LEFT(MATNR,1)',
}
for name,sql in queries.items():
 try:
  cur.execute(sql, () if name=='special_fields' else (DEFAULT_MANDT,))
  print(name,json.dumps([list(x) for x in cur.fetchall()],default=str),flush=True)
 except Exception as exc:print(name,type(exc).__name__,str(exc),flush=True)
conn.close()
