"""Dump every stored field for PO 7000008489 across EKKO / EKPO / EKKN / EKET
so we can spot which field carries the linked 3090 SO reference.
"""
from __future__ import annotations
import os
import pyodbc

DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)
MANDT = os.getenv("SAP_CLIENT", "800")
PO = "7000008489"


def dump(title, sql, conn, cap=60):
    print(f"\n=== {title} ===")
    try:
        cur = conn.execute(sql)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        if not rows:
            print("  (no rows)")
            return
        for r in rows[:cap]:
            for col, val in zip(cols, r):
                v = "" if val is None else str(val).strip()
                if v:  # only show non-blank fields
                    print(f"  {col:<25} = {v!r}")
            print("  ----")
    except Exception as e:
        print(f"  ERROR: {e}")


def main():
    with pyodbc.connect(DSN, autocommit=True) as c:
        dump("EKKO (all non-blank fields on the PO header)",
             f"SELECT * FROM \"SAPHANADB\".\"EKKO\" WHERE \"MANDT\"='{MANDT}' AND \"EBELN\"='{PO}'", c)

        dump("EKPO (all non-blank fields on each PO item)",
             f"SELECT * FROM \"SAPHANADB\".\"EKPO\" WHERE \"MANDT\"='{MANDT}' AND \"EBELN\"='{PO}'", c)

        dump("EKKN (account assignment on the PO)",
             f"SELECT * FROM \"SAPHANADB\".\"EKKN\" WHERE \"MANDT\"='{MANDT}' AND \"EBELN\"='{PO}'", c)

        dump("EKET (schedule lines)",
             f"SELECT * FROM \"SAPHANADB\".\"EKET\" WHERE \"MANDT\"='{MANDT}' AND \"EBELN\"='{PO}'", c)

        # If the PO number appears as BSTNK on any SO -> that's the link
        dump("VBAK anywhere referencing this PO as BSTNK",
             f"SELECT \"VBELN\",\"VKORG\",\"BSTNK\",\"NETWR\",\"WAERK\",\"ERDAT\" "
             f"FROM \"SAPHANADB\".\"VBAK\" WHERE \"MANDT\"='{MANDT}' AND \"BSTNK\" LIKE '%{PO}%'", c)

        # Doc flow both directions
        dump("VBFA where this PO appears as source (VBELV)",
             f"SELECT * FROM \"SAPHANADB\".\"VBFA\" WHERE \"MANDT\"='{MANDT}' AND \"VBELV\"='{PO}' LIMIT 30", c)
        dump("VBFA where this PO appears as target (VBELN)",
             f"SELECT * FROM \"SAPHANADB\".\"VBFA\" WHERE \"MANDT\"='{MANDT}' AND \"VBELN\"='{PO}' LIMIT 30", c)


if __name__ == "__main__":
    main()
