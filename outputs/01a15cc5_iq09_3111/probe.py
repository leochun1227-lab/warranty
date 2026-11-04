import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pyodbc
from sap_material_prices import DEFAULT_DSN,DEFAULT_MANDT
conn=pyodbc.connect(DEFAULT_DSN,autocommit=True,timeout=20)
cur=conn.cursor()
names=['EQUI','EQBS','EQKT','EQUZ','ILOA','JEST','JSTO','TJ02T','TJ30T']
for name in names:
 cur.execute("SELECT COLUMN_NAME FROM SYS.TABLE_COLUMNS WHERE SCHEMA_NAME='SAPHANADB' AND TABLE_NAME=? ORDER BY POSITION",name)
 print(name,','.join(r[0] for r in cur.fetchall()),flush=True)
cur.execute("SELECT TOP 5 e.EQUNR,e.MATNR,e.SERNR,e.OBJNR,b.* FROM SAPHANADB.EQUI e JOIN SAPHANADB.EQBS b ON b.MANDT=e.MANDT AND b.EQUNR=e.EQUNR WHERE e.MANDT=? AND (e.MATNR LIKE 'Z%' OR e.MATNR LIKE 'D%')",DEFAULT_MANDT)
print('sample',json.dumps({'headers':[c[0] for c in cur.description],'rows':[list(r) for r in cur.fetchall()]},default=str))
conn.close()
