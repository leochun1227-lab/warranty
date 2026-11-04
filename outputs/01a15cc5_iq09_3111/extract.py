import sys,json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict,Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pyodbc
from sap_material_prices import DEFAULT_DSN,DEFAULT_MANDT
out=Path(__file__).parent
conn=pyodbc.connect(DEFAULT_DSN,autocommit=True,timeout=20)
cur=conn.cursor()
scope="e.MANDT=? AND b.B_WERK='3111' AND (e.MATNR LIKE 'Z%' OR e.MATNR LIKE 'D%')"
base=f'''FROM SAPHANADB.EQUI e JOIN SAPHANADB.EQBS b ON b.MANDT=e.MANDT AND b.EQUNR=e.EQUNR WHERE {scope}'''
sql=f'''
SELECT e.MATNR,COALESCE(me.MAKTX,mz.MAKTX,''),e.SERNR,e.EQUNR,
 COALESCE(qe.EQKTX,qz.EQKTX,''),b.B_WERK,b.B_LAGER,COALESCE(l.LGOBE,''),
 e.OBJNR,b.LBBSA,b.SOBKZ,b.B_CHARGE,b.KDAUF,b.KDPOS,b.KUNNR,b.LIFNR,e.SERGE,e.ERDAT,e.AEDAT
FROM SAPHANADB.EQUI e
JOIN SAPHANADB.EQBS b ON b.MANDT=e.MANDT AND b.EQUNR=e.EQUNR
LEFT JOIN SAPHANADB.MAKT me ON me.MANDT=e.MANDT AND me.MATNR=e.MATNR AND me.SPRAS='E'
LEFT JOIN SAPHANADB.MAKT mz ON mz.MANDT=e.MANDT AND mz.MATNR=e.MATNR AND mz.SPRAS='1'
LEFT JOIN SAPHANADB.EQKT qe ON qe.MANDT=e.MANDT AND qe.EQUNR=e.EQUNR AND qe.SPRAS='E'
LEFT JOIN SAPHANADB.EQKT qz ON qz.MANDT=e.MANDT AND qz.EQUNR=e.EQUNR AND qz.SPRAS='1'
LEFT JOIN SAPHANADB.T001L l ON l.MANDT=b.MANDT AND l.WERKS=b.B_WERK AND l.LGORT=b.B_LAGER
WHERE {scope}
ORDER BY e.MATNR,e.SERNR,e.EQUNR
'''
cur.execute(sql,DEFAULT_MANDT)
data=[list(r) for r in cur.fetchall()]
cur.execute('SELECT COUNT(*) '+base,DEFAULT_MANDT)
count=cur.fetchone()[0]
assert len(data)==count==len({r[3] for r in data})
assert all(r[0].startswith(('Z','D')) and r[5]=='3111' for r in data)
status_sql=f'''SELECT j.OBJNR,j.STAT,COALESCE(t.TXT04,u.TXT04,j.STAT),COALESCE(t.TXT30,u.TXT30,'')
FROM SAPHANADB.JEST j
LEFT JOIN SAPHANADB.TJ02T t ON t.ISTAT=j.STAT AND t.SPRAS='E'
LEFT JOIN SAPHANADB.JSTO o ON o.MANDT=j.MANDT AND o.OBJNR=j.OBJNR
LEFT JOIN SAPHANADB.TJ30T u ON u.MANDT=j.MANDT AND u.STSMA=o.STSMA AND u.ESTAT=j.STAT AND u.SPRAS='E'
WHERE j.MANDT=? AND j.INACT='' AND j.OBJNR IN (SELECT e.OBJNR {base}) ORDER BY j.OBJNR,j.STAT'''
cur.execute(status_sql,DEFAULT_MANDT,DEFAULT_MANDT)
statuses=defaultdict(list)
for obj,code,short,long in cur.fetchall():statuses[obj].append((code,short,long))
cur.execute("SELECT DOMVALUE_L,DDTEXT FROM SAPHANADB.DD07T WHERE DOMNAME='LBBSA' AND DDLANGUAGE='E' AND AS4LOCAL='A'")
types=dict(cur.fetchall())
vin_sql=f'''SELECT DISTINCT z.SERNR,z.SERNR2 FROM SAPHANADB.ZTSD002 z
WHERE z.MANDT=? AND z.WERKS='3091' AND z.SERNR2<>'' AND z.SERNR IN (SELECT e.SERNR {base}) ORDER BY z.SERNR,z.SERNR2'''
cur.execute(vin_sql,DEFAULT_MANDT,DEFAULT_MANDT)
vins=defaultdict(list)
for serial,vin in cur.fetchall():vins[serial].append(vin)
headers=['Material','Material Description','Serial Number','VIN','Equipment','Equipment Description','Plant','Storage Location','Storage Location Description','System Status','System Status Description','Stock Type','Stock Type Description','Special Stock','Batch','Sales Order','Sales Order Item','Customer','Supplier','Manufacturer Serial Number','Created On','Changed On']
def sapdate(v):
 return f'{v[:4]}-{v[4:6]}-{v[6:8]}' if v and v!='00000000' else None
rows=[]
for r in data:
 st=statuses[r[8]]
 rows.append(r[:3]+['; '.join(vins[r[2]])]+r[3:8]+[' '.join(s[1] for s in st),'; '.join(s[2] for s in st),r[9],types.get(r[9],''),*r[10:17],sapdate(r[17]),sapdate(r[18])])
 assert len(rows[-1])==len(headers)
payload={'headers':headers,'rows':rows,'meta':{'createdAt':datetime.now(ZoneInfo('Australia/Sydney')).isoformat(),'client':DEFAULT_MANDT,'plant':'3111','materialPrefixes':['Z','D'],'source':'EQUI + EQBS + active JEST','statusFilter':None,'rows':len(rows),'materials':len({r[0] for r in rows}),'equipment':len({r[4] for r in rows}),'prefixCounts':dict(Counter(r[0][0] for r in rows)),'statusCounts':dict(Counter(r[9] for r in rows)),'multiVinSerials':sum(len(v)>1 for v in vins.values())}}
(out/'iq09.json').write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
(out/'query.sql').write_text(sql+';\n'+status_sql+';\n'+vin_sql+';\n',encoding='utf-8')
print(json.dumps(payload['meta']))
conn.close()
