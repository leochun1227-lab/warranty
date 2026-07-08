"""Find the field that identifies Nishi on E03 (Regent Spare Parts) POs.

E03 POs are regular purchase orders (not the C4C-generated ZCRM warranty
flow), so unlike warranty POs they should have a real human as creator or
requisitioner.  This probe dumps every plausible person-identifier field so
we can pick the one that reliably says "Nishi".
"""
from __future__ import annotations

import os

import pyodbc


DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)
MANDT = os.getenv("SAP_CLIENT", "800")


def section(title: str, sql: str, conn, cap: int = 30) -> None:
    print(f"\n=== {title} ===")
    try:
        rows = conn.execute(sql).fetchall()
        if not rows:
            print("  (no rows)")
        else:
            for r in rows[:cap]:
                print(" ", tuple(r))
            if len(rows) > cap:
                print(f"  ... and {len(rows) - cap} more")
    except Exception as e:
        print(f"  ERROR: {e}")


def main() -> None:
    with pyodbc.connect(DSN, autocommit=True) as c:

        section(
            "1. Top 30 ERNAM (creator) on E03 POs YTD 2026 -- Nishi should be here",
            f"""
            SELECT "ERNAM", COUNT(*) AS n
            FROM "SAPHANADB"."EKKO"
            WHERE "MANDT"='{MANDT}' AND "EKGRP"='E03' AND "AEDAT" >= '20260101'
            GROUP BY "ERNAM"
            ORDER BY n DESC
            LIMIT 30
            """,
            c,
        )

        section(
            "2. Top 30 EKPO.AFNAM (requisitioner) on E03 POs YTD 2026",
            f"""
            SELECT ekpo."AFNAM", COUNT(*) AS n
            FROM "SAPHANADB"."EKPO" ekpo
            INNER JOIN "SAPHANADB"."EKKO" ekko
                ON ekko."MANDT"=ekpo."MANDT" AND ekko."EBELN"=ekpo."EBELN"
            WHERE ekpo."MANDT"='{MANDT}' AND ekko."EKGRP"='E03' AND ekko."AEDAT" >= '20260101'
              AND ekpo."AFNAM" <> ''
            GROUP BY ekpo."AFNAM"
            ORDER BY n DESC
            LIMIT 30
            """,
            c,
        )

        section(
            "3. Try USR02 (user master) to see full names for E03 ERNAM users",
            f"""
            SELECT usr02."BNAME", usr21."PERSNUMBER"
            FROM "SAPHANADB"."USR02" usr02
            LEFT JOIN "SAPHANADB"."USR21" usr21
                ON usr21."MANDT"=usr02."MANDT" AND usr21."BNAME"=usr02."BNAME"
            WHERE usr02."MANDT"='{MANDT}'
              AND usr02."BNAME" IN (
                SELECT DISTINCT "ERNAM"
                FROM "SAPHANADB"."EKKO"
                WHERE "MANDT"='{MANDT}' AND "EKGRP"='E03' AND "AEDAT" >= '20260101'
              )
            """,
            c,
        )

        section(
            "4. Any ERNAM matching Nishi/Rajput/NRAJ prefix anywhere in EKKO",
            f"""
            SELECT "ERNAM", "EKGRP", COUNT(*) AS n
            FROM "SAPHANADB"."EKKO"
            WHERE "MANDT"='{MANDT}'
              AND (UPPER("ERNAM") LIKE '%NISHI%' OR UPPER("ERNAM") LIKE '%RAJPUT%' OR "ERNAM" LIKE 'NR%')
            GROUP BY "ERNAM", "EKGRP"
            ORDER BY n DESC
            LIMIT 30
            """,
            c,
        )

        section(
            "5. E03 PO doctype breakdown -- ZCRM or normal NB?",
            f"""
            SELECT "BSART", COUNT(*) AS n
            FROM "SAPHANADB"."EKKO"
            WHERE "MANDT"='{MANDT}' AND "EKGRP"='E03' AND "AEDAT" >= '20260101'
            GROUP BY "BSART"
            ORDER BY n DESC
            """,
            c,
        )

        section(
            "6. Total value of E03 POs YTD 2026 (rough size check)",
            f"""
            SELECT COUNT(DISTINCT ekpo."EBELN") AS po_count,
                   COUNT(*)                    AS item_count,
                   ROUND(SUM(ekpo."NETWR"), 2) AS netwr_sum,
                   ekko."WAERS"                AS currency
            FROM "SAPHANADB"."EKPO" ekpo
            INNER JOIN "SAPHANADB"."EKKO" ekko
                ON ekko."MANDT"=ekpo."MANDT" AND ekko."EBELN"=ekpo."EBELN"
            WHERE ekpo."MANDT"='{MANDT}'
              AND ekko."EKGRP"='E03'
              AND ekko."AEDAT" >= '20260101'
              AND (ekpo."LOEKZ" IS NULL OR ekpo."LOEKZ" = '')
              AND (ekko."LOEKZ" IS NULL OR ekko."LOEKZ" = '')
            GROUP BY ekko."WAERS"
            """,
            c,
        )


if __name__ == "__main__":
    main()
