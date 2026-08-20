from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any


DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def fetch_all(cursor, sql: str) -> list[dict[str, Any]]:
    cursor.execute(sql)
    headers = [col[0] for col in cursor.description]
    return [{headers[i]: jsonable(row[i]) for i in range(len(headers))} for row in cursor.fetchall()]


def main() -> None:
    import pyodbc  # type: ignore

    po = os.getenv("TARGET_PO", "7000013310").replace("'", "''")
    queries = {
        "ekko_ekpo": f"""
            SELECT
                h.EBELN AS "PO",
                h.AEDAT AS "PO Created",
                h.BEDAT AS "Document Date",
                h.EKORG AS "Purchasing Org",
                h.EKGRP AS "Purchasing Group",
                h.BUKRS AS "Company Code",
                h.LIFNR AS "Vendor",
                l.NAME1 AS "Vendor Name",
                p.EBELP AS "Item",
                p.TXZ01 AS "Short Text",
                p.MATNR AS "Material",
                p.WERKS AS "Plant",
                p.NETWR AS "Net Value",
                p.MENGE AS "Qty",
                p.MEINS AS "UoM",
                p.LOEKZ AS "Deletion Indicator"
            FROM "SAPHANADB"."EKKO" h
            LEFT JOIN "SAPHANADB"."EKPO" p
                ON p.MANDT = h.MANDT
               AND p.EBELN = h.EBELN
            LEFT JOIN "SAPHANADB"."LFA1" l
                ON l.MANDT = h.MANDT
               AND l.LIFNR = h.LIFNR
            WHERE h.MANDT = 800
              AND h.EBELN = '{po}'
            ORDER BY p.EBELP
        """,
        "ekbe": f"""
            SELECT
                EBELN AS "PO",
                EBELP AS "Item",
                BEWTP AS "PO History Category",
                BELNR AS "Document",
                BUDAT AS "Posting Date",
                BWART AS "Movement Type",
                SHKZG AS "Debit Credit",
                DMBTR AS "Amount"
            FROM "SAPHANADB"."EKBE"
            WHERE MANDT = 800
              AND EBELN = '{po}'
            ORDER BY BUDAT, BELNR, EBELP
        """,
    }
    with pyodbc.connect(DSN, autocommit=True, timeout=60) as conn:
        cursor = conn.cursor()
        payload = {name: fetch_all(cursor, sql) for name, sql in queries.items()}
    out = Path(__file__).with_name("sap_po_7000013310_diagnostic.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
