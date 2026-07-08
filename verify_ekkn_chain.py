"""Verify EKKN.VBELN gives us the 3090 SO for a warranty CRM PO,
and that VBAP has real prices under it."""
from __future__ import annotations
import os
import pyodbc

DSN = os.getenv("SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;")
MANDT = os.getenv("SAP_CLIENT", "800")

SAMPLE_POS = ['7000022996', '7000022995', '7000023009',
              '7000023008', '7000022998', '7000023027']


def section(title, sql, conn, cap=40):
    print(f"\n=== {title} ===")
    try:
        cur = conn.execute(sql)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        if not rows:
            print("  (no rows)")
            return
        header = " | ".join(cols)
        print("  " + header)
        print("  " + "-" * len(header))
        for r in rows[:cap]:
            print("  " + " | ".join("" if v is None else str(v).strip() for v in r))
    except Exception as e:
        print(f"  ERROR: {e}")


def main():
    po_list = "','".join(SAMPLE_POS)
    with pyodbc.connect(DSN, autocommit=True) as c:

        section(
            "1. EKKN for warranty sample POs -- do they all have VBELN linkage?",
            f"""
            SELECT ekpo."EBELN", ekpo."EBELP", ekpo."MATNR",
                   ekkn."VBELN"  AS "SO",
                   ekkn."VBELP"  AS "SO_Item",
                   ekkn."SAKTO"  AS "GL_Acct"
            FROM "SAPHANADB"."EKPO" ekpo
            LEFT JOIN "SAPHANADB"."EKKN" ekkn
                ON ekkn."MANDT"=ekpo."MANDT"
               AND ekkn."EBELN"=ekpo."EBELN"
               AND ekkn."EBELP"=ekpo."EBELP"
            WHERE ekpo."MANDT"='{MANDT}' AND ekpo."EBELN" IN ('{po_list}')
            ORDER BY ekpo."EBELN", ekpo."EBELP"
            """,
            c,
        )

        section(
            "2. Full chain: warranty PO -> EKKN -> VBAP (real 3090 SO price)",
            f"""
            SELECT ekpo."EBELN"  AS "PO",
                   ekpo."EBELP"  AS "POItem",
                   ekpo."MATNR"  AS "Material",
                   ekpo."NETPR"  AS "PO_UnitPrice",
                   ekpo."NETWR"  AS "PO_NetValue",
                   ekko."WAERS"  AS "PO_Currency",
                   ekkn."VBELN"  AS "SO",
                   ekkn."VBELP"  AS "SOItem",
                   vbak."VKORG"  AS "SO_VKORG",
                   vbap."NETPR"  AS "SO_UnitPrice",
                   vbap."NETWR"  AS "SO_NetValue",
                   vbak."WAERK"  AS "SO_Currency",
                   vbap."ERDAT"  AS "SO_Date"
            FROM "SAPHANADB"."EKPO" ekpo
            INNER JOIN "SAPHANADB"."EKKO" ekko
                ON ekko."MANDT"=ekpo."MANDT" AND ekko."EBELN"=ekpo."EBELN"
            INNER JOIN "SAPHANADB"."EKKN" ekkn
                ON ekkn."MANDT"=ekpo."MANDT"
               AND ekkn."EBELN"=ekpo."EBELN"
               AND ekkn."EBELP"=ekpo."EBELP"
            LEFT JOIN "SAPHANADB"."VBAP" vbap
                ON vbap."MANDT"=ekkn."MANDT"
               AND vbap."VBELN"=ekkn."VBELN"
               AND vbap."POSNR"=ekkn."VBELP"
            LEFT JOIN "SAPHANADB"."VBAK" vbak
                ON vbak."MANDT"=vbap."MANDT" AND vbak."VBELN"=vbap."VBELN"
            WHERE ekpo."MANDT"='{MANDT}' AND ekpo."EBELN" IN ('{po_list}')
            ORDER BY ekpo."EBELN", ekpo."EBELP"
            """,
            c,
        )


if __name__ == "__main__":
    main()
