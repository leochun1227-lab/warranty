#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetch the latest per-material price from three SAP sources at once:

    3090 SO      -- latest VBAP row under VKORG='3090'   (CN sales price)
    3110 PO      -- latest EKPO row under EKORG='3111'   (AU purchase price)
    3091 PO      -- latest EKPO row under EKORG='3091'   (CN factory purchase price)

Public API
----------
    fetch_material_price_map(materials, dsn=..., mandt=...) -> dict

Returns:
    {
        MATNR: {
            "3090 SO": {"netPrice": float, "currency": str, "date": "YYYY-MM-DD", "docNum": str},
            "3110 PO": {...},
            "3091 PO": {...},
        },
        ...
    }

Missing materials are simply absent from the map. Missing sources on a
matched material are absent from the inner dict. Callers should treat
missing entries as blank (do not synthesize zeros - "no data" is different
from "price is zero").

Design notes
------------
Match is BY MATERIAL only, not by document number. Investigation on this
HANA instance showed there is no reliable 1:1 link between AU warranty
POs (EKORG=3111, doctype ZCRM) and CN-side documents; the intercompany
relationship is by-material, so the useful comparison is "latest price
on each org".

Latest is chosen via ROW_NUMBER() OVER (PARTITION BY MATNR ORDER BY date
DESC, doc DESC, item DESC). Deleted / rejected lines are excluded via
EKPO.LOEKZ / EKKO.LOEKZ for POs and VBAP.ABGRU for SOs.

Currency is left as-is; conversion (e.g. CNY -> AUD at a fixed rate) is
the responsibility of the presentation layer, so a rate change is a
front-end tweak rather than a full data re-pull.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Iterable, List

logger = logging.getLogger(__name__)


DEFAULT_DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)
DEFAULT_MANDT = os.getenv("SAP_CLIENT", "800")


class Source:
    __slots__ = ("label", "kind", "org_value", "org_field")

    def __init__(self, label: str, kind: str, org_value: str, org_field: str):
        self.label = label
        self.kind = kind          # "SO" or "PO"
        self.org_value = org_value
        self.org_field = org_field


SOURCES: List[Source] = [
    Source("3090 SO", "SO", "3090", "VKORG"),
    Source("3110 PO", "PO", "3111", "EKORG"),
    Source("3091 PO", "PO", "3091", "EKORG"),
]

PREFERRED_COST_SOURCES = ("3110 PO", "3090 SO")
CNY_TO_AUD_RATE = float(os.getenv("CNY_TO_AUD_RATE", "5"))


def _sql_quote(v: str) -> str:
    return str(v).replace("'", "''")


def _chunks(lst: list, n: int) -> List[list]:
    return [lst[i:i + n] for i in range(0, len(lst), n)]


def _clean(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"", "#", "nan", "none"} else s


def _fmt_sap_date(s: str) -> str:
    s = _clean(s)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def price_to_aud(price: object, currency: object, cny_to_aud_rate: float = CNY_TO_AUD_RATE) -> float:
    try:
        num = float(price)
    except (TypeError, ValueError):
        return 0.0
    cur = _clean(currency).upper()
    if not num:
        return 0.0
    if cur == "CNY":
        return round(num / (cny_to_aud_rate or 5.0), 4)
    return round(num, 4)


def pick_preferred_price_slot(
    by_source: Dict[str, Dict[str, object]],
    preferred_sources: Iterable[str] = PREFERRED_COST_SOURCES,
) -> tuple[str, Dict[str, object]]:
    """Return the first non-empty source slot using the configured priority."""
    for label in preferred_sources:
        slot = by_source.get(label, {})
        if slot and _clean(slot.get("netPrice")) != "":
            return label, slot
    return "", {}


def preferred_line_cost_aud(
    item: Dict[str, object],
    price_map: Dict[str, Dict[str, Dict[str, object]]],
    quantity_key: str = "Order Qty",
    preferred_sources: Iterable[str] = PREFERRED_COST_SOURCES,
    cny_to_aud_rate: float = CNY_TO_AUD_RATE,
) -> Dict[str, object]:
    """Return the preferred parts price for one export row.

    The primary source is 3110 PO; if that material has no 3110 PO price we
    fall back to the 3090 SO price. The returned `lineCostAud` multiplies the
    unit price by the row quantity.
    """
    mat = _clean(item.get("Material"))
    if not mat:
        return {
            "source": "",
            "unitPrice": "",
            "currency": "",
            "date": "",
            "docNum": "",
            "quantity": 0.0,
            "lineCostAud": "",
        }

    by_source = price_map.get(mat, {})
    source, slot = pick_preferred_price_slot(by_source, preferred_sources=preferred_sources)
    if not slot:
        return {
            "source": "",
            "unitPrice": "",
            "currency": "",
            "date": "",
            "docNum": "",
            "quantity": 0.0,
            "lineCostAud": "",
        }

    try:
        raw_qty = item.get(quantity_key)
        if raw_qty is None or raw_qty == "":
            quantity = 0.0
        else:
            # SAP exports sometimes format Order Qty as "1,000" for values >= 1000.
            # Strip separators so float() does not raise ValueError and silently
            # collapse to zero.
            if isinstance(raw_qty, str):
                raw_qty = raw_qty.replace(",", "").strip() or "0"
            quantity = float(raw_qty)
    except (TypeError, ValueError):
        quantity = 0.0

    unit_price = float(slot.get("netPrice") or 0)
    currency = _clean(slot.get("currency"))
    aud_unit = price_to_aud(unit_price, currency, cny_to_aud_rate=cny_to_aud_rate)
    return {
        "source": source,
        "unitPrice": round(unit_price, 4),
        "currency": currency,
        "date": _clean(slot.get("date")),
        "docNum": _clean(slot.get("docNum")),
        "quantity": quantity,
        "lineCostAud": round(aud_unit * quantity, 4),
    }


def _sql_so(materials_in_list: str, mandt: str) -> str:
    return f"""
WITH ranked AS (
    SELECT
        vbap."MATNR",
        vbap."VBELN"                              AS "DOCNUM",
        vbap."POSNR"                              AS "DOCITEM",
        vbak."WAERK"                              AS "WAERS",
        vbap."NETPR"                              AS "NETPR",
        COALESCE(NULLIF(vbap."KPEIN", 0), 1)      AS "PEINH",
        vbap."ERDAT"                              AS "DOCDATE",
        ROW_NUMBER() OVER (
            PARTITION BY vbap."MATNR"
            ORDER BY vbap."ERDAT" DESC, vbap."VBELN" DESC, vbap."POSNR" DESC
        ) AS rn
    FROM "SAPHANADB"."VBAP" vbap
    INNER JOIN "SAPHANADB"."VBAK" vbak
        ON vbak."MANDT" = vbap."MANDT"
       AND vbak."VBELN" = vbap."VBELN"
    WHERE vbap."MANDT" = '{_sql_quote(mandt)}'
      AND vbak."{{ORG_FIELD}}" = '{{ORG_VALUE}}'
      AND vbap."MATNR" IN ('{materials_in_list}')
      AND (vbap."ABGRU" IS NULL OR vbap."ABGRU" = '')
)
SELECT "MATNR","DOCNUM","WAERS","NETPR","PEINH","DOCDATE"
FROM ranked WHERE rn = 1
"""


def _sql_po(materials_in_list: str, mandt: str) -> str:
    return f"""
WITH ranked AS (
    SELECT
        ekpo."MATNR",
        ekpo."EBELN"                                  AS "DOCNUM",
        ekpo."EBELP"                                  AS "DOCITEM",
        ekko."WAERS"                                  AS "WAERS",
        ekpo."NETPR"                                  AS "NETPR",
        COALESCE(NULLIF(ekpo."PEINH", 0), 1)          AS "PEINH",
        ekko."AEDAT"                                  AS "DOCDATE",
        ROW_NUMBER() OVER (
            PARTITION BY ekpo."MATNR"
            ORDER BY ekko."AEDAT" DESC, ekpo."EBELN" DESC, ekpo."EBELP" DESC
        ) AS rn
    FROM "SAPHANADB"."EKPO" ekpo
    INNER JOIN "SAPHANADB"."EKKO" ekko
        ON ekko."MANDT" = ekpo."MANDT"
       AND ekko."EBELN" = ekpo."EBELN"
    WHERE ekpo."MANDT" = '{_sql_quote(mandt)}'
      AND ekko."{{ORG_FIELD}}" = '{{ORG_VALUE}}'
      AND ekpo."MATNR" IN ('{materials_in_list}')
      AND (ekpo."LOEKZ" IS NULL OR ekpo."LOEKZ" = '')
      AND (ekko."LOEKZ" IS NULL OR ekko."LOEKZ" = '')
)
SELECT "MATNR","DOCNUM","WAERS","NETPR","PEINH","DOCDATE"
FROM ranked WHERE rn = 1
"""


def fetch_material_price_map(
    materials: Iterable[str],
    dsn: str = DEFAULT_DSN,
    mandt: str = DEFAULT_MANDT,
    batch_size: int = 500,
) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Fetch latest per-material prices for all three sources in a single connection."""
    unique_mats = sorted({_clean(m) for m in materials if _clean(m)})
    out: Dict[str, Dict[str, Dict[str, object]]] = {}
    if not unique_mats:
        return out

    # Import pyodbc/pandas lazily so the module can be imported without them
    # (e.g. tests that stub the function).
    import pandas as pd
    import pyodbc

    with pyodbc.connect(dsn, autocommit=True) as conn:
        for src in SOURCES:
            template = _sql_so if src.kind == "SO" else _sql_po
            found = 0
            for i, batch in enumerate(_chunks(unique_mats, batch_size), start=1):
                in_list = "','".join(_sql_quote(m) for m in batch)
                sql = template(in_list, mandt).format(
                    ORG_FIELD=src.org_field,
                    ORG_VALUE=_sql_quote(src.org_value),
                )
                logger.info("[%s] batch %s (%s materials)", src.label, i, len(batch))
                df = pd.read_sql(sql, conn)
                for _, row in df.iterrows():
                    mat = _clean(row["MATNR"])
                    if not mat:
                        continue
                    netpr = float(row.get("NETPR") or 0)
                    peinh = float(row.get("PEINH") or 1) or 1
                    unit_price = netpr / peinh
                    slot = out.setdefault(mat, {})
                    slot[src.label] = {
                        "netPrice": round(unit_price, 4),
                        "currency": _clean(row.get("WAERS")),
                        "date": _fmt_sap_date(str(row.get("DOCDATE"))),
                        "docNum": _clean(row.get("DOCNUM")),
                    }
                    found += 1
            logger.info("[%s] found for %s / %s materials", src.label, found, len(unique_mats))
    return out


def enrich_detail_rows(
    details: List[Dict[str, object]],
    price_map: Dict[str, Dict[str, Dict[str, object]]],
) -> List[Dict[str, object]]:
    """Attach the 12 price fields to each Sales Order Details item dict in place,
    using flat string keys so Firebase readers can pick them up without nested
    parsing. Returns the same list for convenience."""
    for item in details:
        mat = _clean(item.get("Material"))
        by_src = price_map.get(mat, {}) if mat else {}
        for src in SOURCES:
            slot = by_src.get(src.label, {})
            item[f"{src.label} Net Price"] = slot.get("netPrice", "")
            item[f"{src.label} Currency"]  = slot.get("currency", "")
            item[f"{src.label} Date"]      = slot.get("date", "")
            item[f"{src.label} Doc"]       = slot.get("docNum", "")
    return details


if __name__ == "__main__":
    # CLI smoke test: pass materials on the command line, dump the map to stdout.
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mats = sys.argv[1:]
    if not mats:
        print("Usage: python sap_material_prices.py MATNR1 [MATNR2 ...]", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(fetch_material_price_map(mats), indent=2, ensure_ascii=False))
