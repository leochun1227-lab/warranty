"""Refresh recall models from HANA sales organization 3110, order item 000010."""
from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)
SAP_CLIENT = "800"
SALES_ORG = "3110"
MODEL_ITEM = "000010"


def vehicle_keys(ticket: Dict[str, Any]) -> List[str]:
    product, raw = ticket.get("product") or {}, ticket.get("ticket") or {}
    values = [product.get("chassisNumber"), product.get("serialId"),
              raw.get("ChassisNumber"), raw.get("SerialID")]
    return sorted({re.sub(r"[^A-Z0-9]", "", str(v).strip().upper()) for v in values if v})


def query_model_rows(connection: Any, keys: Iterable[str]) -> List[Dict[str, Any]]:
    """Collect every matching order before choosing a model; never choose an arbitrary order."""
    valid_keys = sorted({key for key in keys if len(key) >= 8})
    rows: List[Dict[str, Any]] = []
    for start in range(0, len(valid_keys), 200):
        batch = valid_keys[start:start + 200]
        inputs = " UNION ALL ".join(
            "SELECT CAST(? AS NVARCHAR(50)) AS INPUT_ID FROM DUMMY" for _ in batch
        )
        sql = """WITH inputs AS (""" + inputs + """), units AS (
            SELECT INPUT_ID,INPUT_ID AS SERIAL_ID,'Direct serial' AS MATCH_METHOD FROM inputs
            UNION
            SELECT i.INPUT_ID,z.SERNR AS SERIAL_ID,'VIN mapping' AS MATCH_METHOD
            FROM inputs i JOIN SAPHANADB.ZTSD002 z
              ON z.MANDT='800' AND z.WERKS='3091' AND z.SERNR2=i.INPUT_ID
        )
        SELECT DISTINCT u.INPUT_ID,u.SERIAL_ID,u.MATCH_METHOD,
            s.SDAUFNR AS SALES_ORDER,s.POSNR AS SERIAL_ITEM,
            v.VKORG AS SALES_ORG,v.AUART AS ORDER_TYPE,v.ERDAT AS ORDER_DATE,
            p.POSNR AS MODEL_ITEM,p.MATNR AS MATERIAL_CODE,
            p.ARKTX AS MATERIAL_DESCRIPTION,p.ABGRU AS REJECTION_REASON
        FROM units u
        JOIN SAPHANADB.OBJK o ON o.MANDT='800' AND o.SERNR=u.SERIAL_ID
        JOIN SAPHANADB.SER02 s ON s.MANDT=o.MANDT AND s.OBKNR=o.OBKNR
        JOIN SAPHANADB.VBAK v
          ON v.MANDT=s.MANDT AND v.VBELN=s.SDAUFNR AND v.VKORG='3110'
        LEFT JOIN SAPHANADB.VBAP p
          ON p.MANDT=v.MANDT AND p.VBELN=v.VBELN AND p.POSNR='000010'
        ORDER BY u.INPUT_ID,s.SDAUFNR,s.POSNR"""
        cursor = connection.cursor()
        try:
            cursor.execute(sql, batch)
            columns = [column[0] for column in cursor.description]
            rows.extend(dict(zip(columns, row)) for row in cursor.fetchall())
        finally:
            cursor.close()
    return rows


def attach_recall_models(
    payload: Dict[str, Any], rows: Iterable[Dict[str, Any]], checked_at: str
) -> Dict[str, Any]:
    """Build fresh model fields without mutating the caller or retaining an outdated model."""
    by_input: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row.get("SALES_ORG", "")) == SALES_ORG:
            by_input[str(row["INPUT_ID"])].append(row)
    tickets = {}
    counts: Counter[str] = Counter()
    for tid, original in payload.get("tickets", {}).items():
        ticket = dict(original)
        for field in ("model", "modelDescription", "modelLookup"):
            ticket.pop(field, None)
        keys = vehicle_keys(ticket)
        evidence = [row for key in keys for row in by_input[key]]
        model_rows = [r for r in evidence
                      if str(r.get("MODEL_ITEM") or "").zfill(6) == MODEL_ITEM
                      and str(r.get("MATERIAL_CODE") or "").strip()]
        codes = sorted({str(r["MATERIAL_CODE"]).strip() for r in model_rows})
        if len(codes) == 1:
            status = "matched"
            ticket["model"] = codes[0]
            # Multiple orders may share one model. Use the newest description only.
            latest = max(model_rows, key=lambda r: (str(r.get("ORDER_DATE") or ""),
                                                    str(r.get("SALES_ORDER") or "")))
            ticket["modelDescription"] = str(latest.get("MATERIAL_DESCRIPTION") or "").strip()
        elif len(codes) > 1:
            status = "conflict"
        elif not any(len(key) >= 8 for key in keys):
            status = "invalid_vehicle_id"
        elif evidence:
            status = "missing_0010_material"
        else:
            status = "no_sales_order"
        lookup: Dict[str, Any] = {"status": status, "checkedAt": checked_at}
        if keys:
            lookup["vehicleKeys"] = keys
        if evidence:
            lookup["salesOrders"] = sorted({str(r["SALES_ORDER"]) for r in evidence})
        if model_rows:
            candidates = {
                (str(r["INPUT_ID"]), str(r["SERIAL_ID"]), str(r["SALES_ORDER"]),
                 str(r["MATERIAL_CODE"]).strip(), str(r.get("MATERIAL_DESCRIPTION") or "").strip())
                for r in model_rows
            }
            lookup["candidates"] = [dict(zip(
                ["vehicleInput", "matchedSerial", "salesOrder", "materialCode", "description"], row
            )) for row in sorted(candidates)]
        ticket["modelLookup"] = lookup
        tickets[tid] = ticket
        counts[status] += 1
    meta = dict(payload.get("meta") or {})
    meta["modelLookup"] = {
        "source": "SAP HANA", "client": SAP_CLIENT, "salesOrganization": SALES_ORG,
        "salesOrderItem": MODEL_ITEM, "checkedAt": checked_at, "counts": dict(counts),
        "rule": "Chassis/serial or mapped VIN -> sales order -> item 000010 material code",
    }
    return {**payload, "meta": meta, "tickets": tickets}


def enrich_recall_models(payload: Dict[str, Any], dsn: str | None = None) -> Dict[str, Any]:
    """Refresh on every sync. A HANA failure aborts before Firebase is replaced."""
    keys = {key for ticket in payload.get("tickets", {}).values() for key in vehicle_keys(ticket)}
    rows = []
    if any(len(key) >= 8 for key in keys):
        import pyodbc

        if not dsn:
            dsn = os.getenv("SAP_HANA_DSN")
        if not dsn:
            # Reuse this project's configured connection; do not duplicate credentials.
            from fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER import SAP_HANA_DSN
            dsn = SAP_HANA_DSN
        with pyodbc.connect(dsn, autocommit=True, timeout=30) as connection:
            rows = query_model_rows(connection, keys)
    checked_at = datetime.now(timezone.utc).isoformat()
    result = attach_recall_models(payload, rows, checked_at)
    logger.info("Recall model lookup completed: %s", result["meta"]["modelLookup"]["counts"])
    return result
