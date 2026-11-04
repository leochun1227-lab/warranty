exec(__import__('pathlib').Path(__file__).with_name('probe.py').read_text().split("names=")[0])
queries={
 'count':"SELECT COUNT(*),COUNT(DISTINCT e.MATNR),COUNT(DISTINCT e.SERNR) FROM SAPHANADB.EQUI e JOIN SAPHANADB.EQBS b ON b.MANDT=e.MANDT AND b.EQUNR=e.EQUNR WHERE e.MANDT=? AND b.B_WERK='3111' AND (e.MATNR LIKE 'Z%' OR e.MATNR LIKE 'D%')",
 'status':"SELECT j.STAT,t.TXT04,t.TXT30,COUNT(*) FROM SAPHANADB.EQUI e JOIN SAPHANADB.EQBS b ON b.MANDT=e.MANDT AND b.EQUNR=e.EQUNR JOIN SAPHANADB.JEST j ON j.MANDT=e.MANDT AND j.OBJNR=e.OBJNR AND j.INACT='' LEFT JOIN SAPHANADB.TJ02T t ON t.ISTAT=j.STAT AND t.SPRAS='E' WHERE e.MANDT=? AND b.B_WERK='3111' AND (e.MATNR LIKE 'Z%' OR e.MATNR LIKE 'D%') GROUP BY j.STAT,t.TXT04,t.TXT30 ORDER BY j.STAT",
 'stock_types':"SELECT b.LBBSA,b.SOBKZ,COUNT(*) FROM SAPHANADB.EQUI e JOIN SAPHANADB.EQBS b ON b.MANDT=e.MANDT AND b.EQUNR=e.EQUNR WHERE e.MANDT=? AND b.B_WERK='3111' AND (e.MATNR LIKE 'Z%' OR e.MATNR LIKE 'D%') GROUP BY b.LBBSA,b.SOBKZ",
 'stock_domain':"SELECT ROLLNAME,DOMNAME FROM SAPHANADB.DD04L WHERE ROLLNAME='LBBSA' AND AS4LOCAL='A'",
}
for name,sql in queries.items():
 cur.execute(sql,() if name=='stock_domain' else (DEFAULT_MANDT,))
 print(name,json.dumps([list(r) for r in cur.fetchall()],default=str),flush=True)
conn.close()
