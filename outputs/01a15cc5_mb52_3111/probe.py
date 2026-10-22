import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pyodbc
from sap_material_prices import DEFAULT_DSN, DEFAULT_MANDT

conn = pyodbc.connect(DEFAULT_DSN, autocommit=True, timeout=20)
cur = conn.cursor()
queries = {
    'objects': "SELECT SCHEMA_NAME, OBJECT_NAME, OBJECT_TYPE FROM SYS.OBJECTS WHERE SCHEMA_NAME='SAPHANADB' AND OBJECT_NAME IN ('MARD','MCHB','MARA','MAKT','MARC','T001W','T001L','NSDM_V_MARD','NSDM_V_MCHB','MBEW','MSKA','MSKU','MSLB','MSPR')",
    'plant': "SELECT WERKS, NAME1, BWKEY FROM SAPHANADB.T001W WHERE MANDT=? AND WERKS='3111'",
    'fields': "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE_NAME FROM SYS.TABLE_COLUMNS WHERE SCHEMA_NAME='SAPHANADB' AND TABLE_NAME IN ('MARD','MCHB','MBEW') ORDER BY TABLE_NAME, POSITION",
}
for name, sql in queries.items():
    try:
        cur.execute(sql, (DEFAULT_MANDT,) if name=='plant' else ())
        print(name, json.dumps([list(x) for x in cur.fetchall()], default=str))
    except Exception as exc:
        print(name, type(exc).__name__, str(exc).split(';')[0])
conn.close()
