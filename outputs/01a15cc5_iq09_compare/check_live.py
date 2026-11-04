import sys,json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pyodbc
from sap_material_prices import DEFAULT_DSN,DEFAULT_MANDT
out=Path(__file__).parent
p=json.loads((out/'comparison.json').read_text(encoding='utf-8'))
ids=[str(r['Equipment']).zfill(18) for r in p['onlyUser']]
assert ids
conn=pyodbc.connect(DEFAULT_DSN,autocommit=True,timeout=20)
cur=conn.cursor()
marks=','.join('?' for _ in ids)
sql=f'''SELECT e.EQUNR,e.MATNR,e.SERNR,e.AEDAT,b.B_WERK,b.B_LAGER,b.LBBSA,b.SOBKZ,b.KDAUF,b.KDPOS,j.STAT,t.TXT04,t.TXT30
FROM SAPHANADB.EQUI e LEFT JOIN SAPHANADB.EQBS b ON b.MANDT=e.MANDT AND b.EQUNR=e.EQUNR
LEFT JOIN SAPHANADB.JEST j ON j.MANDT=e.MANDT AND j.OBJNR=e.OBJNR AND j.INACT=''
LEFT JOIN SAPHANADB.TJ02T t ON t.ISTAT=j.STAT AND t.SPRAS='E'
WHERE e.MANDT=? AND e.EQUNR IN ({marks}) ORDER BY e.EQUNR,j.STAT'''
cur.execute(sql,DEFAULT_MANDT,*ids)
live={'headers':[c[0] for c in cur.description],'rows':[list(r) for r in cur.fetchall()]}
cur.execute(f'''SELECT TOP 30 j.OBJNR,j.STAT,t.TXT04,j.UDATE,j.UTIME,j.INACT,j.CHGNR
FROM SAPHANADB.JCDS j LEFT JOIN SAPHANADB.TJ02T t ON t.ISTAT=j.STAT AND t.SPRAS='E'
WHERE j.MANDT=? AND j.OBJNR IN (SELECT OBJNR FROM SAPHANADB.EQUI WHERE MANDT=? AND EQUNR IN ({marks}))
ORDER BY j.UDATE DESC,j.UTIME DESC,j.CHGNR DESC''',DEFAULT_MANDT,DEFAULT_MANDT,*ids)
history={'headers':[c[0] for c in cur.description],'rows':[list(r) for r in cur.fetchall()]}
cur.execute("SELECT COUNT(*) FROM SAPHANADB.EQUI e JOIN SAPHANADB.EQBS b ON b.MANDT=e.MANDT AND b.EQUNR=e.EQUNR WHERE e.MANDT=? AND b.B_WERK='3111' AND (e.MATNR LIKE 'Z%' OR e.MATNR LIKE 'D%')",DEFAULT_MANDT)
payload={'queriedAt':datetime.now(ZoneInfo('Australia/Sydney')).isoformat(),'currentCount':cur.fetchone()[0],'live':live,'statusHistory':history}
(out/'live_check.json').write_text(json.dumps(payload,ensure_ascii=False,default=str),encoding='utf-8')
print(json.dumps(payload,ensure_ascii=False,default=str))
conn.close()
