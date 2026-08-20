from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "outputs" / "sap_po_history_pure_ekbe_20260820"
DEFAULT_DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)


BASE_QUERY = """
WITH LATEST_GR AS (
    SELECT
        EKBE.EBELN,
        EKBE.EBELP,
        EKBE.BELNR,
        EKBE.BUDAT,
        EKBE.BWART,
        ROW_NUMBER() OVER (
            PARTITION BY EKBE.EBELN, EKBE.EBELP
            ORDER BY EKBE.BUDAT DESC, EKBE.BELNR DESC
        ) AS RN
    FROM "SAPHANADB"."EKBE" EKBE
    WHERE EKBE.MANDT = 800
      AND EKBE.BEWTP = 'E'
)

SELECT
    EKBE.EBELN AS "PO Number",
    LTRIM(EKBE.EBELP, '0') AS "PO Item",

    CASE
        WHEN LG.BWART = '101' THEN LG.BELNR
        ELSE NULL
    END AS "Latest Valid GR",

    CASE
        WHEN LG.BWART = '101' THEN LG.BUDAT
        ELSE NULL
    END AS "Latest Valid GR Posting Date",

    SUM(
        CASE
            WHEN EKBE.BEWTP = 'E' AND EKBE.SHKZG = 'S' THEN EKBE.DMBTR
            WHEN EKBE.BEWTP = 'E' AND EKBE.SHKZG = 'H' THEN -EKBE.DMBTR
            ELSE 0
        END
    ) AS "GR Amt",

    SUM(
        CASE
            WHEN EKBE.BEWTP = 'Q' AND EKBE.SHKZG = 'S' THEN EKBE.DMBTR
            WHEN EKBE.BEWTP = 'Q' AND EKBE.SHKZG = 'H' THEN -EKBE.DMBTR
            ELSE 0
        END
    ) AS "Invoice Amt",

    SUM(
        CASE
            WHEN EKBE.BEWTP = 'A' AND EKBE.SHKZG = 'S' THEN EKBE.DMBTR
            WHEN EKBE.BEWTP = 'A' AND EKBE.SHKZG = 'H' THEN -EKBE.DMBTR
            ELSE 0
        END
    ) AS "Down Payment Amt",

    SUM(
        CASE
            WHEN EKBE.BEWTP = '3' AND EKBE.SHKZG = 'S' THEN EKBE.DMBTR
            WHEN EKBE.BEWTP = '3' AND EKBE.SHKZG = 'H' THEN -EKBE.DMBTR
            ELSE 0
        END
    ) AS "Down Payment Clearing"

FROM "SAPHANADB"."EKBE" EKBE

LEFT JOIN "SAPHANADB"."RBKP" RBKP
    ON EKBE.BELNR = RBKP.BELNR
   AND EKBE.MANDT = RBKP.MANDT

LEFT JOIN LATEST_GR LG
    ON EKBE.EBELN = LG.EBELN
   AND EKBE.EBELP = LG.EBELP
   AND LG.RN = 1

WHERE EKBE.MANDT = 800
  AND COALESCE(RBKP.STBLG, '') = ''

GROUP BY
    EKBE.EBELN,
    EKBE.EBELP,
    LG.BELNR,
    LG.BUDAT,
    LG.BWART
"""


def jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def fetch_rows(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import pyodbc  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"pyodbc import failed: {exc}") from exc

    count_query = f"""
WITH LATEST_GR AS (
    SELECT
        EKBE.EBELN,
        EKBE.EBELP,
        EKBE.BELNR,
        EKBE.BUDAT,
        EKBE.BWART,
        ROW_NUMBER() OVER (
            PARTITION BY EKBE.EBELN, EKBE.EBELP
            ORDER BY EKBE.BUDAT DESC, EKBE.BELNR DESC
        ) AS RN
    FROM "SAPHANADB"."EKBE" EKBE
    WHERE EKBE.MANDT = 800
      AND EKBE.BEWTP = 'E'
)
SELECT COUNT(*) AS "Row Count"
FROM (
    SELECT
        EKBE.EBELN,
        EKBE.EBELP,
        LG.BELNR,
        LG.BUDAT,
        LG.BWART
    FROM "SAPHANADB"."EKBE" EKBE
    LEFT JOIN "SAPHANADB"."RBKP" RBKP
        ON EKBE.BELNR = RBKP.BELNR
       AND EKBE.MANDT = RBKP.MANDT
    LEFT JOIN LATEST_GR LG
        ON EKBE.EBELN = LG.EBELN
       AND EKBE.EBELP = LG.EBELP
       AND LG.RN = 1
    WHERE EKBE.MANDT = 800
      AND COALESCE(RBKP.STBLG, '') = ''
    GROUP BY
        EKBE.EBELN,
        EKBE.EBELP,
        LG.BELNR,
        LG.BUDAT,
        LG.BWART
) X
"""
    with pyodbc.connect(args.dsn, autocommit=True, timeout=args.timeout) as conn:
        cursor = conn.cursor()
        print("Counting grouped rows...")
        total_rows = int(cursor.execute(count_query).fetchone()[0])
        export_rows = min(total_rows, args.max_rows)
        is_full_export = total_rows <= args.max_rows
        query = BASE_QUERY
        if not is_full_export:
            query = f"{BASE_QUERY}\nORDER BY EKBE.EBELN, EKBE.EBELP\nLIMIT {int(args.max_rows)}"
        else:
            query = f"{BASE_QUERY}\nORDER BY EKBE.EBELN, EKBE.EBELP"
        print(f"Fetching {export_rows:,} of {total_rows:,} rows...")
        cursor.execute(query)
        headers = [col[0] for col in cursor.description]
        rows = []
        while True:
            batch = cursor.fetchmany(5000)
            if not batch:
                break
            for row in batch:
                rows.append({headers[i]: jsonable(row[i]) for i in range(len(headers))})

    return {
        "meta": {
            "createdAt": datetime.now().isoformat(timespec="seconds"),
            "rowCount": total_rows,
            "exportedRows": len(rows),
            "isFullExport": is_full_export,
            "maxRows": args.max_rows,
            "source": "User-provided EKBE/RBKP/LATEST_GR SQL, MANDT=800",
        },
        "sql": BASE_QUERY.strip(),
        "rows": rows,
    }


def write_outputs(payload: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "sap_po_history_pure.json"
    csv_path = OUT_DIR / "sap_po_history_pure.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    headers = [
        "PO Number",
        "PO Item",
        "Latest Valid GR",
        "Latest Valid GR Posting Date",
        "GR Amt",
        "Invoice Amt",
        "Down Payment Amt",
        "Down Payment Clearing",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(payload["rows"])
    print(json_path)
    print(csv_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--max-rows", type=int, default=20000)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    payload = fetch_rows(args)
    write_outputs(payload)


if __name__ == "__main__":
    main()
