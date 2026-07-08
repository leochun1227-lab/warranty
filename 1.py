import pyodbc, os

dsn = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)

sample_pos = ['7000022996', '7000022995', '7000023009', '7000023008', '7000022998', '7000023027']
sample_mats = ['Y15101617', 'T27300343', 'Y15100211', 'Y15100528', 'T12101214']

def section(title, sql, conn):
    print(f"\n=== {title} ===")
    try:
        for r in conn.execute(sql).fetchall(): print(" ", r)
    except Exception as e:
        print(f"  ERROR: {e}")

with pyodbc.connect(dsn, autocommit=True) as c:
    po_list  = "','".join(sample_pos)
    mat_list = "','".join(sample_mats)

    section("A. EKPO detail for the AU warranty POs (looking for CN reference fields)", f"""
        SELECT "EBELN","EBELP","MATNR","MENGE","NETPR","PEINH","NETWR","LIFNR","IHREZ","BEDNR","BANFN","BNFPO"
        FROM "SAPHANADB"."EKPO"
        WHERE "EBELN" IN ('{po_list}')
    """, c)

    section("B. Any 3090 VBAP rows for these materials? (per-material CN sell price)", f"""
        SELECT vbap."MATNR", vbap."VBELN", vbap."POSNR", vbak."VKORG", vbak."WAERK",
               vbap."NETWR", vbap."NETPR", vbap."KPEIN", vbap."KMEIN", vbap."ERDAT"
        FROM "SAPHANADB"."VBAP" vbap
        INNER JOIN "SAPHANADB"."VBAK" vbak
            ON vbak."MANDT"=vbap."MANDT" AND vbak."VBELN"=vbap."VBELN"
        WHERE vbap."MANDT"='800'
          AND vbak."VKORG"='3090'
          AND vbap."MATNR" IN ('{mat_list}')
        ORDER BY vbap."MATNR", vbap."ERDAT" DESC
        LIMIT 50
    """, c)

    section("C. Count of 3090 SO lines per material (how much history is there?)", f"""
        SELECT vbap."MATNR", COUNT(*) AS n, MIN(vbap."ERDAT") AS first_date, MAX(vbap."ERDAT") AS last_date
        FROM "SAPHANADB"."VBAP" vbap
        INNER JOIN "SAPHANADB"."VBAK" vbak
            ON vbak."MANDT"=vbap."MANDT" AND vbak."VBELN"=vbap."VBELN"
        WHERE vbap."MANDT"='800' AND vbak."VKORG"='3090'
          AND vbap."MATNR" IN ('{mat_list}')
        GROUP BY vbap."MATNR"
    """, c)

    section("D. LIFNR (vendor) on the AU warranty POs — is it a CN intercompany vendor?", f"""
        SELECT DISTINCT "LIFNR" FROM "SAPHANADB"."EKKO"
        WHERE "MANDT"='800' AND "EBELN" IN ('{po_list}')
    """, c)