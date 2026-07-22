from __future__ import annotations

import os
import re
import sys
import csv
import json
import time
import logging
import threading
import hashlib
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Iterable
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import pyodbc
import firebase_admin
from firebase_admin import credentials, db
from firebase_admin.exceptions import InvalidArgumentError
from sap_material_prices import CNY_TO_AUD_RATE, enrich_detail_rows, fetch_material_price_map, preferred_line_cost_aud


# =================== C4C API é…ç½® ===================
BASE_URL = "https://longcui-automobile-cpi-tyrbc1k7.it-cpi010-rt.cpi.cn40.apps.platform.sapcloud.cn"
PATH = "/http/PC4C/Ticket/queryOdataBatch"

USERNAME = os.getenv("C4C_USERNAME", "XIEYONGDONG@newgonow.cn")
PASSWORD = os.getenv("C4C_PASSWORD", "Max@sap2022")
ROLE_CODES = ["1001", "40", "43"]

API_TOP = 1000
API_SKIP_START = 0
API_EXTRA_TAIL_PAGES = int(os.getenv("API_EXTRA_TAIL_PAGES", "3"))
TIMEOUT = 60
VERIFY_SSL = True

MAX_WORKERS = 12
ROLE_WORKERS = min(3, len(ROLE_CODES))
# ================================================


# =================== SAP HANA é…ç½® ===================
SAP_HANA_DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;"
)

OUTPUT_FILE = os.getenv("OUTPUT_FILE", "c4c_ticket_so_single_sheet.xlsx")  # kept only for backward compatibility; not exported in automation
SALES_ORG = os.getenv("SALES_ORG", "3110")
SAP_CLIENT = os.getenv("SAP_CLIENT", "800")
APPROVED_COST_PO_PURCHASING_ORG = os.getenv("APPROVED_COST_PO_PURCHASING_ORG", os.getenv("PURCHASING_ORG", "3111"))
APPROVED_COST_PO_PURCHASING_GROUP = os.getenv("APPROVED_COST_PO_PURCHASING_GROUP", os.getenv("PURCHASING_GROUP", "E06"))
APPROVED_COST_PO_PLANT_FILTER = os.getenv("APPROVED_COST_PO_PLANT_FILTER", "").strip()
APPROVED_COST_EXCLUDE_DELETED_PO = os.getenv("APPROVED_COST_EXCLUDE_DELETED_PO", "false").strip().lower() in {"1", "true", "yes", "y"}
ROOT_DIR = Path(__file__).resolve().parent
OUTPUTS_DIR = ROOT_DIR / "outputs"
VEHICLE_BASE_SUMMARY_PATH = OUTPUTS_DIR / "analysis_vehicle_base_summary.json"
VEHICLE_BASE_SUMMARY_JS_PATH = OUTPUTS_DIR / "analysis_vehicle_base_summary.js"
PARTS_TICKET_COST_MAP_PATH = OUTPUTS_DIR / "analysis_parts_ticket_cost_map.json"
PARTS_TICKET_COST_MAP_JS_PATH = OUTPUTS_DIR / "analysis_parts_ticket_cost_map.js"
ANALYSIS_TICKET_CSV_PATH = OUTPUTS_DIR / "analysis_ticket_base.csv"
LEGACY_ANALYSIS_TICKET_CSV_PATH = ROOT_DIR / "SAPAnalyticsReport_ZF8C06456D7698BCB54F44D_.csv"
ANALYSIS_TICKET_CSV_JS_PATH = OUTPUTS_DIR / "analysis_ticket_csv.js"
PARTS_CLASSIFIED_META_PATH = OUTPUTS_DIR / "parts_classified_meta.json"
PARTS_CLASSIFIED_FLAT_CSV_PATH = OUTPUTS_DIR / "parts_classified.csv"
PARTS_CLASSIFIED_DATA_JS_PATH = OUTPUTS_DIR / "parts_classified_data.js"
# ====================================================


def sql_quote(s: str) -> str:
    return str(s).replace("'", "''")


APPROVED_COST_TICKET_NO_PATTERN = re.compile(
    r"\btickets?\s*no\.?\s*[:#\-]?\s*\[?\s*(\d+)\s*\]?\b",
    flags=re.IGNORECASE,
)
APPROVED_COST_TICKET_BRACKET_PATTERN = re.compile(
    r"\btickets?\s*\[\s*(\d+)\s*\]",
    flags=re.IGNORECASE,
)
APPROVED_COST_TICKET_OUTER_BRACKET_PATTERN = re.compile(
    r"\[\s*tickets?\s+(\d+)\s*(?:[\]\}]|$|[,，;；]|\s)?",
    flags=re.IGNORECASE,
)


# ======================================================================
# Baseline / PGI SQL — MSEG-based route (captures cars that don't have a
# proper LIKP/WADAT_IST record; the LIKP route in fetch_vehicle_base_summary
# misses many vehicles which caused the analysis dashboard to show empty
# failure-timing bars and undercounted denominators in Leaderboard / Full
# Metrics Table).
#
# Three queries:
#   1. PGI_MSEG_SQL          — actual PGI dates for shipped chassis, from
#                              goods-issue movement (BWART 601/602/645) at
#                              plant 3111, joined to serial/VIN/material.
#   2. INSTOCK_MSKA_SQL      — chassis currently sitting in stock at plant
#                              3111 LGORT 0024/0026 (KALAB<>0). These are
#                              produced/received but not yet shipped —
#                              still part of the denominator (pre-delivery
#                              tickets can exist for them).
#   3. INTRANSIT_OPEN_PO_SQL — chassis linked to open POs (no GR yet, not
#                              LOEKZ-deleted), sourced from VBAP → SER02 →
#                              OBJK → EKPO/EKKO chain. These are cars in
#                              the pipeline but not physically at plant
#                              3111 yet.
# ======================================================================

PGI_MSEG_SQL_TEMPLATE = rf"""
WITH obj AS (
    SELECT DISTINCT
        vbak."MANDT"                    AS "MANDT",
        vbak."VBELN"                    AS "Sales Order",
        vbap."MATNR"                    AS "Material",
        vbap."ARKTX"                    AS "Description",
        objk."SERNR"                    AS "Serial",
        z."SERNR2"                      AS "VIN"
    FROM "SAPHANADB"."VBAK" vbak
    INNER JOIN "SAPHANADB"."VBAP" vbap
        ON vbap."MANDT" = vbak."MANDT"
       AND vbap."VBELN" = vbak."VBELN"
       AND LPAD(TO_VARCHAR(vbap."POSNR"), 6, '0') = '000010'
       AND vbap."MATNR" LIKE 'Z%'
    INNER JOIN "SAPHANADB"."SER02" ser02
        ON ser02."MANDT" = vbak."MANDT"
       AND ser02."SDAUFNR" = vbak."VBELN"
       AND LPAD(TO_VARCHAR(ser02."POSNR"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."OBJK" objk
        ON objk."MANDT" = ser02."MANDT"
       AND objk."OBKNR" = ser02."OBKNR"
    LEFT JOIN "SAPHANADB"."ZTSD002" z
        ON z."MANDT" = objk."MANDT"
       AND z."WERKS" = '3091'
       AND z."SERNR" = objk."SERNR"
    WHERE vbak."MANDT" = '{{mandt}}'
      AND vbak."VKORG" = '{sql_quote(SALES_ORG)}'
),
gi AS (
    SELECT DISTINCT
        obj."MANDT"                     AS "MANDT",
        obj."Sales Order"               AS "Sales Order",
        obj."Material"                  AS "Material",
        obj."Description"               AS "Description",
        obj."Serial"                    AS "Serial",
        obj."VIN"                       AS "VIN",
        gi."MBLNR"                      AS "PGI Material Doc",
        gi."ZEILE"                      AS "PGI Item Raw",
        gi."BUDAT_MKPF"                 AS "PGI Date"
    FROM obj
    INNER JOIN "SAPHANADB"."NSDM_V_MSEG" gi
        ON gi."MANDT" = obj."MANDT"
       AND gi."KDAUF" = obj."Sales Order"
       AND LPAD(TO_VARCHAR(gi."KDPOS"), 6, '0') = '000010'
       AND gi."WERKS" = '3111'
       AND gi."BWART" = '601'
       AND gi."BUDAT_MKPF" >= '{{cutoff}}'
)
SELECT DISTINCT
    gi."Sales Order"                   AS "Sales Order",
    gi."Material"                      AS "Material",
    gi."Description"                   AS "Description",
    gi."Serial"                        AS "Serial",
    gi."VIN"                           AS "VIN",
    TO_VARCHAR(gi."PGI Date")          AS "PGI Date",
    gi."PGI Material Doc"              AS "PGI Material Doc",
    LPAD(TO_VARCHAR(gi."PGI Item Raw"), 4, '0') AS "PGI Item"
FROM gi
LEFT JOIN "SAPHANADB"."NSDM_V_MSEG" rev
    ON rev."MANDT" = gi."MANDT"
   AND rev."SMBLN" = gi."PGI Material Doc"
   AND rev."SMBLP" = gi."PGI Item Raw"
   AND rev."BWART" = '602'
WHERE rev."MBLNR" IS NULL
"""

INSTOCK_MSKA_SQL_TEMPLATE = rf"""
WITH mv AS (
    SELECT
        "KDAUF","MATNR","LGORT",
        MAX("BUDAT_MKPF") AS "LastMvmt",
        MIN("BUDAT_MKPF") AS "FirstMvmt"
    FROM "SAPHANADB"."NSDM_V_MSEG"
    WHERE "MANDT" = '{{mandt}}'
      AND "WERKS" = '3111'
      AND "KDPOS" = 10
    GROUP BY "KDAUF","MATNR","LGORT"
)
SELECT
    a."VBELN"                      AS "Sales Order",
    a."MATNR"                      AS "Material",
    v."ARKTX"                      AS "Description",
    a."LGORT"                      AS "Storage Location",
    a."KALAB"                      AS "Stock Qty",
    TO_VARCHAR(mv."LastMvmt")      AS "Last Movement",
    TO_VARCHAR(mv."FirstMvmt")     AS "First Movement",
    o."SERNR"                      AS "Serial",
    z."SERNR2"                     AS "VIN"
FROM "SAPHANADB"."NSDM_V_MSKA" a
LEFT JOIN mv
    ON a."VBELN" = mv."KDAUF"
   AND a."MATNR" = mv."MATNR"
   AND a."LGORT" = mv."LGORT"
LEFT JOIN "SAPHANADB"."VBAP" v
    ON v."MANDT" = '{{mandt}}'
   AND v."VBELN" = a."VBELN"
   AND v."MATNR" = a."MATNR"
   AND LPAD(TO_VARCHAR(v."POSNR"), 6, '0') = '000010'
LEFT JOIN "SAPHANADB"."SER02" s2
    ON s2."MANDT" = '{{mandt}}'
   AND s2."SDAUFNR" = a."VBELN"
   AND LPAD(TO_VARCHAR(s2."POSNR"), 6, '0') = '000010'
LEFT JOIN "SAPHANADB"."OBJK" o
    ON o."MANDT" = s2."MANDT"
   AND o."OBKNR" = s2."OBKNR"
LEFT JOIN "SAPHANADB"."ZTSD002" z
    ON z."MANDT" = o."MANDT"
   AND z."WERKS" = '3091'
   AND z."SERNR" = o."SERNR"
WHERE a."MANDT" = '{{mandt}}'
  AND a."WERKS" = 3111
  AND a."MATNR" LIKE 'Z%'
  AND a."LGORT" IN ('0024','0026')
  AND a."KALAB" <> 0
"""

INTRANSIT_OPEN_PO_SQL_TEMPLATE = rf"""
WITH mseg_gr AS (
    SELECT
        "MANDT","EBELN","EBELP",
        MIN("BUDAT_MKPF") AS "GR_DATE"
    FROM "SAPHANADB"."NSDM_V_MSEG"
    WHERE "MANDT" = '{{mandt}}'
      AND "EBELN" IS NOT NULL
      AND "BWART" IN ('101','103')
    GROUP BY "MANDT","EBELN","EBELP"
),
ekpo_open_nogr AS (
    SELECT
        p."MANDT",
        p."EBELN",
        p."EBELP",
        p."CREATIONDATE",
        SUBSTRING(
            p."TXZ01",
            1,
            CASE
                WHEN INSTR(p."TXZ01", ' ') > 0 THEN INSTR(p."TXZ01", ' ') - 1
                ELSE LENGTH(p."TXZ01")
            END
        ) AS "SERNR_PREFIX"
    FROM "SAPHANADB"."EKPO" p
    JOIN "SAPHANADB"."EKKO" h
        ON h."MANDT" = p."MANDT"
       AND h."EBELN" = p."EBELN"
    LEFT JOIN mseg_gr gr
        ON gr."MANDT" = p."MANDT"
       AND gr."EBELN" = p."EBELN"
       AND gr."EBELP" = p."EBELP"
    WHERE
        p."MANDT" = '{{mandt}}'
        AND p."WERKS"='3111'
        AND p."MATKL"='Z003'
        AND LOWER(p."TXZ01") LIKE '% to %'
        AND COALESCE(p."LOEKZ",'') = ''
        AND COALESCE(h."LOEKZ",'') = ''
        AND COALESCE(p."ELIKZ",'') <> 'X'
        AND gr."EBELN" IS NULL
)
SELECT
    vbap."VBELN"                   AS "Sales Order",
    vbap."MATNR"                   AS "Material",
    vbap."ARKTX"                   AS "Description",
    objk."SERNR"                   AS "Serial",
    ek."EBELN"                     AS "PO No",
    TO_VARCHAR(ek."CREATIONDATE")  AS "PO Date"
FROM "SAPHANADB"."VBAP" vbap
LEFT JOIN "SAPHANADB"."SER02" s
    ON vbap."MANDT" = s."MANDT"
   AND vbap."VBELN" = s."SDAUFNR"
   AND s."POSNR" = 10
LEFT JOIN "SAPHANADB"."OBJK" objk
    ON s."MANDT" = objk."MANDT"
   AND s."OBKNR" = objk."OBKNR"
INNER JOIN ekpo_open_nogr ek
    ON ek."MANDT" = vbap."MANDT"
   AND objk."SERNR" = ek."SERNR_PREFIX"
WHERE
    vbap."MANDT" = '{{mandt}}'
    AND vbap."POSNR" = 10
"""




# ================= Firebase é…ç½® =================
FIREBASE_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app"
)
FIREBASE_SA_PATH = os.getenv(
    "FIREBASE_SA_PATH",
    str(ROOT_DIR / "firebase-service-account.json")
)
FIREBASE_ROOT = os.getenv("FIREBASE_ROOT", "c4cTickets_test")
MONITOR_ROOT = os.getenv("MONITOR_ROOT", "ctmTicketStatusMonitorV44")
DEFAULT_ACTIVE_EMPLOYEES = [
    "Mark Bertoncini",
    "Leanne Pulford",
    "Kylie Clayton",
    "Rosemary Johnstone",
    "Michael Scordia",
    "Robert Stella",
    "Chloe Bolger",
]

MAX_PATHS_PER_UPDATE = 8000
MAX_BYTES_PER_UPDATE = 6_000_000
SERVER_TIMESTAMP = {".sv": "timestamp"}
CRITICAL_STATUS_VALUES = {
    x.strip().lower()
    for x in os.getenv("CRITICAL_STATUS_VALUES", "critical").split(",")
    if x.strip()
}
# ===============================================


ROLE_VARYING_FIELDS = [
    "InvolvedPartyBusinessPartnerID",
    "InvolvedPartyID",
    "InvolvedPartyName",
    "InvolvedPartyRoleID",
    "requested_skip",
]

REQUEST_META_FIELDS = {"requested_role_code", "requested_role_name", "requested_skip"}

DEALER_NAME_CANDIDATES = [
    "DealerName",
    "DEALER_NAME",
    "Dealer_Name",
    "dealerName",
    "Dealer",
    "DealerFullName",
]

DEALER_ID_CANDIDATES = ["DealerID"]

ERP_FREE_ORDER_CANDIDATES = [
    "ERPFreeOrder",
    "ERP_FREE_ORDER",
    "ErpFreeOrder",
    "FreeOrder",
    "ERP Free Order",
]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("c4c_ticket_so_firebase_fast")
SCRIPT_VERSION = "MANDT800_FULLJOIN_REJECTION_FILTER"


# =================== çº¿ç¨‹æœ¬åœ° Session ===================
_thread_local = threading.local()


def get_thread_session() -> requests.Session:
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=0.6,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=50,
            pool_maxsize=50
        )
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        _thread_local.session = sess
    return sess


def close_thread_session():
    sess = getattr(_thread_local, "session", None)
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass
        _thread_local.session = None


# =================== é€šç”¨å‡½æ•° ===================
def sanitize_key(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    for ch in [".", "$", "#", "[", "]", "/"]:
        s = s.replace(ch, "_")
    return s.strip()


def sanitize_fb_key(s: str) -> str:
    return sanitize_key(s)

def firebase_node_to_dict(node: Any) -> Dict[str, Any]:
    """
    Firebase Admin SDK may return numeric-key objects as list.
    Convert list back to dict so numeric ticket ids can be reused correctly.
    """
    if isinstance(node, dict):
        return node
    if isinstance(node, list):
        return {str(i): v for i, v in enumerate(node) if v is not None}
    return {}

def norm(v: Any) -> Any:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    return v


def as_clean_str(v: Any) -> Optional[str]:
    v = norm(v)
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def safe_chunks(items: List[Any], size: int = 250) -> List[List[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def build_lookup_candidates(val: Any) -> Tuple[str, str]:
    """
    Build Sales Order lookup candidates.

    Correct rule:
    - MANDT/client 800 is NOT a prefix of Sales Order number.
    - MANDT/client 800 must be used as SQL filter/join condition.
    - Sales Order lookup still uses raw ERPFreeOrder and 00+ERPFreeOrder fallback.
    """
    s = as_clean_str(val) or ""
    if not s:
        return "", ""

    lookup1 = s
    lookup2 = ""
    if not s.startswith("00"):
        lookup2 = "00" + s

    return lookup1, lookup2


def _rough_bytes(payload: Dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


KNOWN_SERIES_CODES = [
    "SRC", "SRH", "SRT", "SRM", "SRP", "SRL", "SRV",
    "LRV", "LRT", "LRH", "LRP", "LRL", "LRC", "LTR", "LVR", "LPV", "LEP", "RRV",
]
EXCLUDED_SERIES_CODES = {"UNKNOWN", "RO", "SR", "SCR", "STR", "RVV", "RR", "SPV", "SRO", "SEV", "RRC"}


def normalize_series_code(raw: Any) -> str:
    text = (as_clean_str(raw) or "").upper()
    if not text:
        return "UNKNOWN"
    if text.startswith("NG"):
        return "NG"
    if text == "LRV" or text.startswith("LRV"):
        return "SRC"
    if text == "RV" or text == "RRV" or text.startswith("RRV"):
        return "SRL"
    if text.startswith("L") and len(text) >= 2:
        return f"S{text[1:]}"
    return text


def is_excluded_series_code(raw: Any) -> bool:
    return normalize_series_code(raw) in EXCLUDED_SERIES_CODES


def extract_series_code(*values: Any) -> str:
    parts = [str(v).strip().upper() for v in values if as_clean_str(v)]
    if not parts:
        return "UNKNOWN"
    text = " ".join(parts)
    if re.search(r"\bNG[A-Z0-9-]*", text):
        return "NG"
    for code in KNOWN_SERIES_CODES:
        if code in text:
            return normalize_series_code(code)
    match = re.search(r"\b([A-Z]{2,4})\d{2,6}[A-Z]?\b", text)
    if match:
        return normalize_series_code(match.group(1))
    return "UNKNOWN"


def vehicle_lookup_aliases(*values: Any) -> List[str]:
    keys: List[str] = []
    seen = set()
    for value in values:
        raw = as_clean_str(value)
        if not raw:
            continue
        variants = [
            raw,
            raw.upper(),
            re.sub(r"[^A-Z0-9]", "", raw.upper()),
        ]
        for variant in variants:
            if variant and variant not in seen:
                seen.add(variant)
                keys.append(variant)
    return keys


def should_replace_series(current: Any, incoming: Any) -> bool:
    cur = normalize_series_code(current)
    new = normalize_series_code(incoming)

    def score(series: str) -> int:
        if not series or series == "UNKNOWN":
            return 0
        if series in EXCLUDED_SERIES_CODES:
            return 1
        return 2

    return score(new) > score(cur)


def write_vehicle_base_summary(payload: Dict[str, Any]) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    # Guard against accidentally overwriting the local cache with a skinny
    # payload that only contains `seriesBase` but no PGI maps. The analysis
    # dashboard relies on these maps for PGI Date export + failure timing.
    if VEHICLE_BASE_SUMMARY_PATH.exists():
        try:
            previous = json.loads(VEHICLE_BASE_SUMMARY_PATH.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
        if isinstance(previous, dict):
            prev_chassis = previous.get("pgiByChassis")
            prev_sales = previous.get("pgiBySalesOrder")
            prev_series_chassis = previous.get("seriesByChassis")
            prev_series_sales_order = previous.get("seriesBySalesOrder")
            prev_series_sales = previous.get("seriesSales")
            prev_total_sales = previous.get("totalSales")
            next_chassis = payload.get("pgiByChassis")
            next_sales = payload.get("pgiBySalesOrder")
            next_series_chassis = payload.get("seriesByChassis")
            next_series_sales_order = payload.get("seriesBySalesOrder")
            next_series_sales = payload.get("seriesSales")
            if isinstance(prev_chassis, dict) and prev_chassis and not (isinstance(next_chassis, dict) and next_chassis):
                payload["pgiByChassis"] = prev_chassis
            if isinstance(prev_sales, dict) and prev_sales and not (isinstance(next_sales, dict) and next_sales):
                payload["pgiBySalesOrder"] = prev_sales
            if isinstance(prev_series_chassis, dict) and prev_series_chassis and not (isinstance(next_series_chassis, dict) and next_series_chassis):
                payload["seriesByChassis"] = prev_series_chassis
            if isinstance(prev_series_sales_order, dict) and prev_series_sales_order and not (isinstance(next_series_sales_order, dict) and next_series_sales_order):
                payload["seriesBySalesOrder"] = prev_series_sales_order
            if isinstance(prev_series_sales, dict) and prev_series_sales and not (isinstance(next_series_sales, dict) and next_series_sales):
                payload["seriesSales"] = prev_series_sales
            if prev_total_sales is not None and payload.get("totalSales") is None:
                payload["totalSales"] = prev_total_sales
    VEHICLE_BASE_SUMMARY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_js_global(
    path: Path,
    global_name: str,
    payload: Any,
    *,
    is_text: bool = False,
    extra_globals: Optional[Dict[str, Any]] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value_text = json.dumps(payload, ensure_ascii=False) if is_text else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lines = [f"globalThis.{global_name} = {value_text};"]
    for key, value in (extra_globals or {}).items():
        lines.append(f"globalThis.{key} = {json.dumps(value, ensure_ascii=False)};")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


ANALYSIS_TICKET_CSV_HEADERS = [
    "Ticket",
    "",
    "Ticket ID",
    "",
    "Ticket Type",
    "Agent",
    "",
    "Serial ID",
    "Chassis Number",
    "Account",
    "",
    "ERP Free Order ID",
    "ERP Purchase Order ID",
    "ERP Service Order ID",
    "Created On",
    "Dealer",
    "Dealer Name",
    "Country/Region",
    "Date of Purchase",
    "Claim Approved On",
    "Service Requester Postal Code",
    "Registered Product",
    "",
    "Product",
    "",
    "Status",
    "Service Technician",
    "",
    "ClaimTotalAmount",
    "Factory Parts Claim Total Amount",
    "LabourHoursTotalAmount",
    "Repairer Parts Claim Total Amount",
    "Changed On",
    "Posting Date",
]


def compact_lookup_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def first_clean_ticket_value(ticket_data: Dict[str, Any], *candidates: str) -> str:
    for candidate in candidates:
        value = as_clean_str(ticket_data.get(candidate))
        if value:
            return value

    normalized = {compact_lookup_key(key): key for key in ticket_data.keys()}
    for candidate in candidates:
        key = normalized.get(compact_lookup_key(candidate))
        if key:
            value = as_clean_str(ticket_data.get(key))
            if value:
                return value
    return ""


def role_field(new_snapshot_node: Dict[str, Any], role_code: str, *candidates: str) -> str:
    roles = (new_snapshot_node or {}).get("roles", {})
    role_data = roles.get(role_code) if isinstance(roles, dict) else None
    if not isinstance(role_data, dict):
        return ""
    return first_clean_ticket_value(role_data, *candidates)


def csv_value(value: Any, default: str = "#") -> str:
    text = as_clean_str(value) or ""
    return text if text else default


def csv_date_value(value: Any) -> str:
    text = as_clean_str(value) or ""
    if not text or text == "#":
        return "#"
    cleaned = text.replace(" AUSACT", "").strip()
    for fmt in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
    ):
        try:
            return datetime.strptime(cleaned[:19], fmt).date().isoformat()
        except ValueError:
            pass
    try:
        parsed = pd.to_datetime(cleaned, errors="coerce", dayfirst=True)
        if not pd.isna(parsed):
            return parsed.date().isoformat()
    except Exception:
        pass
    return text


def first_non_empty(values: Iterable[Any]) -> str:
    for value in values:
        text = as_clean_str(value)
        if text:
            return text
    return ""


def summarize_final_ticket_rows(final_df: pd.DataFrame) -> Dict[str, Dict[str, str]]:
    if final_df.empty or "TicketID" not in final_df.columns:
        return {}
    summary: Dict[str, Dict[str, str]] = {}
    fields = [
        "ERPFreeOrder",
        "ERPPurchaseOrder",
        "ERP Service Order ID",
        "Sales Order",
        "SO Created Date",
        "AmountIncludingTax",
    ]
    for ticket_id_raw, grp in final_df.groupby("TicketID", dropna=False):
        ticket_id = as_clean_str(ticket_id_raw) or ""
        if not ticket_id:
            continue
        row_summary: Dict[str, str] = {}
        for field in fields:
            if field in grp.columns:
                row_summary[field] = first_non_empty(grp[field].tolist())
            else:
                row_summary[field] = ""
        summary[ticket_id] = row_summary
    return summary


def write_analysis_ticket_base_csv(
    new_snapshot: Dict[str, Any],
    final_df: pd.DataFrame,
    output_path: Path = ANALYSIS_TICKET_CSV_PATH,
) -> None:
    """Write the current ticket base used by analysis/timeline exports.

    The previous fallback copied a manually refreshed SAPAnalyticsReport CSV.
    For scheduled deployments, this file must be generated from the same fetch
    run so all downstream pages follow the daily refresh automatically.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    hana_summary = summarize_final_ticket_rows(final_df)
    rows: List[List[str]] = []

    for ticket_id, node in sorted((new_snapshot or {}).items(), key=lambda item: str(item[0])):
        if not isinstance(node, dict):
            continue
        ticket = node.get("ticket", {})
        if not isinstance(ticket, dict):
            ticket = {}

        ticket_number = first_clean_ticket_value(ticket, "TicketID", "Ticket ID") or str(ticket_id)
        summary = hana_summary.get(str(ticket_id), {})

        title = (
            first_clean_ticket_value(ticket, "TicketName", "Ticket Name", "Subject", "Name")
            or f"{first_clean_ticket_value(ticket, 'DealerName', 'Dealer Name')} {first_clean_ticket_value(ticket, 'ChassisNumber', 'Chassis Number', 'SerialID')}".strip()
            or ticket_number
        )
        agent_name = (
            role_field(node, "40", "InvolvedPartyName")
            or first_clean_ticket_value(ticket, "AssignedToRaw", "Assigned to", "Assigned To", "OwnerPartyName", "AssignedToName")
        )
        agent_id = (
            role_field(node, "40", "InvolvedPartyBusinessPartnerID", "InvolvedPartyID")
            or first_clean_ticket_value(ticket, "AssignedToID", "OwnerPartyID")
        )

        erp_free_order = (
            summary.get("ERPFreeOrder")
            or find_erp_free_order(ticket)
            or first_clean_ticket_value(ticket, "ERPFreeOrder", "ERP Free Order ID")
        )
        erp_purchase_order = (
            summary.get("ERPPurchaseOrder")
            or first_clean_ticket_value(ticket, "ERPPurchaseOrder", "ERP Purchase Order ID")
        )

        service_technician = first_clean_ticket_value(
            ticket,
            "ServiceTechnician",
            "Service Technician",
            "RepairerBusinessNameID",
            "Repairshop ID",
            "Repair Shop ID",
            "RepairerNamePointOfContact",
        )

        claim_total = first_clean_ticket_value(ticket, "ClaimTotalAmount", "Claim Total Amount", "AmountIncludingTax") or summary.get("AmountIncludingTax", "")
        repairer_parts_total = first_clean_ticket_value(ticket, "Repairer Parts Claim Total Amount", "RepairerPartsClaimTotalAmount") or claim_total

        rows.append(
            [
                csv_value(title),
                csv_value(ticket_number),
                csv_value(title),
                csv_value(ticket_number),
                csv_value(first_clean_ticket_value(ticket, "TicketTypeText", "Ticket Type", "TicketType")),
                csv_value(agent_name),
                csv_value(agent_id),
                csv_value(first_clean_ticket_value(ticket, "SerialID", "Serial ID", "Serial")),
                csv_value(first_clean_ticket_value(ticket, "ChassisNumber", "Chassis Number", "VIN")),
                csv_value(first_clean_ticket_value(ticket, "AccountName", "Account", "ServiceRequesterName", "CustomerName")),
                csv_value(first_clean_ticket_value(ticket, "AccountID", "Account ID", "ServiceRequesterID", "CustomerID")),
                csv_value(erp_free_order),
                csv_value(erp_purchase_order),
                csv_value(first_clean_ticket_value(ticket, "ERPServiceOrder", "ERP Service Order ID", "ERPServiceOrderID")),
                csv_date_value(first_clean_ticket_value(ticket, "CreatedOn", "Created On")),
                csv_value(first_clean_ticket_value(ticket, "DealerID", "Dealer")),
                csv_value(first_clean_ticket_value(ticket, "DealerName", "Dealer Name")),
                csv_value(first_clean_ticket_value(ticket, "CountryRegion", "Country/Region", "Country", "Region")),
                csv_date_value(first_clean_ticket_value(ticket, "DateOfPurchase", "Date of Purchase", "PurchaseDate")),
                csv_date_value(
                    first_clean_ticket_value(
                        ticket,
                        "ClaimApprovedOnDateTime",
                        "ClaimApprovedOnDate",
                        "ClaimApprovedOn",
                        "Claim Approved On",
                    )
                ),
                csv_value(first_clean_ticket_value(ticket, "ServiceRequesterPostalCode", "Service Requester Postal Code", "PostalCode")),
                csv_value(first_clean_ticket_value(ticket, "RegisteredProduct", "Registered Product")),
                csv_value(first_clean_ticket_value(ticket, "RegisteredProductCode", "Registered Product Code")),
                csv_value(first_clean_ticket_value(ticket, "Product", "ProductName")),
                csv_value(first_clean_ticket_value(ticket, "ProductCode", "Product ID")),
                csv_value(first_clean_ticket_value(ticket, "TicketStatusText", "Status")),
                csv_value(service_technician),
                csv_value(first_clean_ticket_value(ticket, "ServiceTechnicianID", "Service Technician ID", "RepairerBusinessNameID")),
                csv_value(claim_total, "0"),
                csv_value(first_clean_ticket_value(ticket, "Factory Parts Claim Total Amount", "FactoryPartsClaimTotalAmount"), "0"),
                csv_value(first_clean_ticket_value(ticket, "LabourHoursTotalAmount", "Labour Hours Total Amount"), "0"),
                csv_value(repairer_parts_total, "0"),
                csv_date_value(
                    first_clean_ticket_value(
                        ticket,
                        "ChangeOnDateTime",
                        "ChangeOnDate",
                        "ChangeOn",
                        "ChangedOn",
                        "Changed On",
                    )
                ),
                csv_date_value(first_clean_ticket_value(ticket, "PostingDate", "Posting Date", "CreatedOn", "Created On")),
            ]
        )

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(ANALYSIS_TICKET_CSV_HEADERS)
        writer.writerows(rows)

    if output_path.resolve() == ANALYSIS_TICKET_CSV_PATH.resolve():
        csv_text = output_path.read_text(encoding="utf-8-sig")
        write_js_global(ANALYSIS_TICKET_CSV_JS_PATH, "ANALYSIS_TICKET_CSV_TEXT", csv_text, is_text=True)
    logger.info("Wrote refreshed analysis ticket base: %s rows -> %s", len(rows), output_path)


def resolve_parts_classified_csv_path() -> Optional[Path]:
    candidates: List[Path] = []
    meta_candidates = [PARTS_CLASSIFIED_META_PATH, *sorted(OUTPUTS_DIR.glob("parts_classification_*/parts_classified_meta.json"), reverse=True)]
    for meta_path in meta_candidates:
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            csv_path = as_clean_str((meta or {}).get("csvPath"))
            if csv_path:
                resolved = Path(csv_path)
                if not resolved.is_absolute():
                    resolved = (ROOT_DIR / resolved).resolve()
                candidates.append(resolved)
        except Exception:
            pass
    candidates.append(PARTS_CLASSIFIED_FLAT_CSV_PATH.resolve())
    candidates.extend(path.resolve() for path in sorted(OUTPUTS_DIR.glob("parts_classification_*/parts_classified.csv"), reverse=True))
    for path in candidates:
        if path.exists():
            return path
    return None


def refresh_analysis_offline_assets(vehicle_base_summary: Optional[Dict[str, Any]] = None) -> None:
    try:
        summary_payload = vehicle_base_summary
        if summary_payload is None and VEHICLE_BASE_SUMMARY_PATH.exists():
            summary_payload = json.loads(VEHICLE_BASE_SUMMARY_PATH.read_text(encoding="utf-8"))
        if isinstance(summary_payload, dict) and summary_payload:
            write_js_global(VEHICLE_BASE_SUMMARY_JS_PATH, "ANALYSIS_VEHICLE_BASE_SUMMARY", summary_payload)
    except Exception as exc:
        logger.warning("Failed to write vehicle base summary JS fallback: %s", exc)

    try:
        if PARTS_TICKET_COST_MAP_PATH.exists():
            cost_payload = json.loads(PARTS_TICKET_COST_MAP_PATH.read_text(encoding="utf-8"))
            write_js_global(PARTS_TICKET_COST_MAP_JS_PATH, "ANALYSIS_PARTS_TICKET_COST_MAP", cost_payload)
    except Exception as exc:
        logger.warning("Failed to write parts ticket cost JS fallback: %s", exc)

    try:
        ticket_csv_path = ANALYSIS_TICKET_CSV_PATH if ANALYSIS_TICKET_CSV_PATH.exists() else LEGACY_ANALYSIS_TICKET_CSV_PATH
        if ticket_csv_path.exists():
            csv_text = ticket_csv_path.read_text(encoding="utf-8-sig")
            write_js_global(ANALYSIS_TICKET_CSV_JS_PATH, "ANALYSIS_TICKET_CSV_TEXT", csv_text, is_text=True)
    except Exception as exc:
        logger.warning("Failed to write main ticket CSV JS fallback: %s", exc)

    try:
        parts_csv_path = resolve_parts_classified_csv_path()
        if parts_csv_path and parts_csv_path.exists():
            csv_text = parts_csv_path.read_text(encoding="utf-8-sig")
            rel = parts_csv_path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
            write_js_global(
                PARTS_CLASSIFIED_DATA_JS_PATH,
                "ANALYSIS_PARTS_CLASSIFIED_CSV_TEXT",
                csv_text,
                is_text=True,
                extra_globals={"ANALYSIS_PARTS_CLASSIFIED_SOURCE": rel},
            )
    except Exception as exc:
        logger.warning("Failed to write parts classified JS fallback: %s", exc)


# =================== Firebase ===================
def firebase_init():
    if getattr(firebase_admin, "_apps", None) and firebase_admin._apps:
        return
    if not os.path.exists(FIREBASE_SA_PATH):
        raise SystemExit("FIREBASE_SA_PATH ç§é’¥æ–‡ä»¶è·¯å¾„æ— æ•ˆ")
    if not FIREBASE_DB_URL:
        raise SystemExit("è¯·å¡«å†™æ­£ç¡®çš„ FIREBASE_DB_URL")

    cred = credentials.Certificate(FIREBASE_SA_PATH)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


def _ref_update_once(path: str, payload: Dict[str, Any]):
    db.reference(path).update(payload)


def _update_bisect(path: str, items: List[Tuple[str, Any]]):
    if not items:
        return

    try:
        _ref_update_once(path, dict(items))
        return
    except Exception as e:
        msg = str(e)
        if "exceeds the maximum size" not in msg and not isinstance(e, InvalidArgumentError):
            raise

    if len(items) == 1:
        k, _ = items[0]
        raise RuntimeError(f"[FB] single path too large to update: {k}")

    mid = len(items) // 2
    _update_bisect(path, items[:mid])
    _update_bisect(path, items[mid:])


def fb_update_with_retry(path: str, payload: dict, tries: int = 5):
    last_err = None
    for i in range(tries):
        try:
            db.reference(path).update(payload)
            return
        except Exception as e:
            last_err = e
            msg = str(e)

            if "exceeds the maximum size" in msg or isinstance(e, InvalidArgumentError):
                items = list(payload.items())
                _update_bisect(path, items)
                return

            wait = 1.2 * (2 ** i)
            logger.warning("[FB] update failed (try %s/%s), wait %.1fs, err=%s", i + 1, tries, wait, e)
            time.sleep(wait)

    raise RuntimeError(f"[FB] update failed after {tries} tries: {last_err}")


# =================== C4C æ‹‰æ•° ===================
def build_url(role_code: str, top: int, skip: int) -> str:
    flt = f"(CCSRQ_DPY_ROLE_CD eq '{role_code}')"
    fval = quote(flt, safe="()'")
    return BASE_URL.rstrip("/") + PATH + f"?$top={top}&$skip={skip}&$filter={fval}"


def fetch_role_page(role_code: str, top: int, skip: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    session = get_thread_session()
    url = build_url(role_code, top, skip)

    r = session.get(
        url,
        auth=HTTPBasicAuth(USERNAME, PASSWORD),
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
        verify=VERIFY_SSL,
    )

    if r.status_code != 200:
        raise RuntimeError(
            f"[API] role={role_code} skip={skip} top={top} HTTP {r.status_code} BODY={r.text[:500]}"
        )

    payload = r.json()
    rows = list(payload.get("data", []))
    for rr in rows:
        rr["requested_skip"] = skip

    meta = {
        "pageSize": payload.get("pageSize"),
        "pageNumber": payload.get("pageNumber"),
        "count": payload.get("count"),
        "totalCount": payload.get("totalCount"),
    }
    return rows, meta


def fetch_role_page_task(role_code: str, top: int, skip: int):
    rows, meta = fetch_role_page(role_code, top, skip)
    return skip, rows, meta


def fetch_all_rows_for_role(role_code: str) -> List[Dict[str, Any]]:
    started = time.time()
    role_rows_all: List[Dict[str, Any]] = []

    first_rows, meta = fetch_role_page(role_code, API_TOP, API_SKIP_START)
    logger.info("[FETCH] role=%s page=1 skip=%s rows=%s", role_code, API_SKIP_START, len(first_rows))

    if not first_rows:
        return []

    role_rows_all.extend(first_rows)

    total_raw = meta.get("totalCount") or meta.get("count")
    total = None
    try:
        total = int(total_raw)
    except (TypeError, ValueError):
        total = None

    if not total:
        skip = API_SKIP_START + API_TOP
        page = 1
        while True:
            rows, _ = fetch_role_page(role_code, API_TOP, skip)
            page += 1
            logger.info("[FETCH] role=%s page=%s skip=%s rows=%s", role_code, page, skip, len(rows))
            if not rows:
                break
            role_rows_all.extend(rows)
            if len(rows) < API_TOP:
                break
            skip += API_TOP

        logger.info(
            "===== END ROLE %s rows=%s elapsed=%.1fs =====",
            role_code, len(role_rows_all), time.time() - started
        )
        return role_rows_all

    skips = list(range(API_SKIP_START + API_TOP, total, API_TOP))
    if not skips:
        logger.info(
            "===== END ROLE %s rows=%s elapsed=%.1fs =====",
            role_code, len(role_rows_all), time.time() - started
        )
        return role_rows_all

    page_workers = min(MAX_WORKERS, max(1, len(skips)))
    page_result_map: Dict[int, List[Dict[str, Any]]] = {}

    with ThreadPoolExecutor(max_workers=page_workers) as executor:
        future_map = {
            executor.submit(fetch_role_page_task, role_code, API_TOP, sk): sk
            for sk in skips
        }

        for idx, future in enumerate(as_completed(future_map), start=2):
            sk = future_map[future]
            skip, rows, _ = future.result()
            logger.info("[FETCH] role=%s page~=%s skip=%s rows=%s", role_code, idx, skip, len(rows))
            page_result_map[sk] = rows

    for sk in sorted(page_result_map.keys()):
        role_rows_all.extend(page_result_map[sk])

    # Some C4C responses have returned a slightly stale totalCount while the
    # next high-numbered tickets were already visible in later pages. Probe a
    # few pages past the reported total so Firebase does not miss the newest
    # tickets when totalCount lags behind the actual result set.
    tail_skip = (skips[-1] + API_TOP) if skips else (API_SKIP_START + API_TOP)
    for tail_page in range(max(0, API_EXTRA_TAIL_PAGES)):
        rows, _ = fetch_role_page(role_code, API_TOP, tail_skip)
        logger.info("[FETCH] role=%s tail_probe=%s skip=%s rows=%s", role_code, tail_page + 1, tail_skip, len(rows))
        if not rows:
            break
        role_rows_all.extend(rows)
        if len(rows) < API_TOP:
            break
        tail_skip += API_TOP

    logger.info(
        "===== END ROLE %s rows=%s elapsed=%.1fs =====",
        role_code, len(role_rows_all), time.time() - started
    )
    return role_rows_all


def split_ticket_row(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ticket_data: Dict[str, Any] = {}
    role_data: Dict[str, Any] = {}

    for k, v in row.items():
        v2 = norm(v)
        if k in ROLE_VARYING_FIELDS:
            role_data[k] = v2
        elif k in REQUEST_META_FIELDS:
            continue
        else:
            ticket_data[k] = v2

    dealer_name: Optional[str] = None
    for cand in DEALER_NAME_CANDIDATES:
        if cand in row:
            dealer_name = as_clean_str(row.get(cand))
            if dealer_name:
                break
    if dealer_name:
        ticket_data["DealerName"] = dealer_name

    dealer_id: Optional[str] = None
    for cand in DEALER_ID_CANDIDATES:
        if cand in row:
            dealer_id = as_clean_str(row.get(cand))
            if dealer_id:
                break
    if dealer_id:
        ticket_data["DealerID"] = dealer_id

    return ticket_data, role_data


def build_new_snapshot() -> Tuple[Dict[str, Any], int]:
    new_snapshot: Dict[str, Any] = {}
    total_rows = 0

    def role_job(role_code: str):
        logger.info("===== START ROLE %s =====", role_code)
        rows = fetch_all_rows_for_role(role_code)
        return role_code, rows

    with ThreadPoolExecutor(max_workers=ROLE_WORKERS) as executor:
        futures = [executor.submit(role_job, rc) for rc in ROLE_CODES]

        for future in as_completed(futures):
            role_code, role_rows = future.result()

            for row in role_rows:
                total_rows += 1

                tid = str(row.get("TicketID") or "").strip()
                if not tid:
                    continue

                tid_key = sanitize_key(tid)
                ticket_data, role_data = split_ticket_row(row)

                if tid_key not in new_snapshot:
                    new_snapshot[tid_key] = {
                        "ticket": ticket_data,
                        "roles": {},
                    }
                else:
                    if ticket_data:
                        new_snapshot[tid_key]["ticket"] = ticket_data

                new_snapshot[tid_key]["roles"][role_code] = role_data

            logger.info(
                "[ROLE MERGED] role=%s rows=%s total_rows=%s unique_tickets=%s",
                role_code, len(role_rows), total_rows, len(new_snapshot)
            )

    return new_snapshot, total_rows


def find_erp_free_order(ticket_data: Dict[str, Any]) -> Optional[str]:
    for cand in ERP_FREE_ORDER_CANDIDATES:
        if cand in ticket_data:
            val = as_clean_str(ticket_data.get(cand))
            if val:
                return val

    normalized_map = {re.sub(r"[^a-z0-9]", "", str(k).lower()): k for k in ticket_data.keys()}
    for cand in ERP_FREE_ORDER_CANDIDATES:
        key = re.sub(r"[^a-z0-9]", "", cand.lower())
        if key in normalized_map:
            real_key = normalized_map[key]
            val = as_clean_str(ticket_data.get(real_key))
            if val:
                return val
    return None


def snapshot_to_ticket_df(new_snapshot: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    for tid, ticket in new_snapshot.items():
        ticket_data = (ticket or {}).get("ticket", {})
        erp_free_order = find_erp_free_order(ticket_data)
        if not erp_free_order:
            continue

        lookup1, lookup2 = build_lookup_candidates(erp_free_order)

        rows.append(
            {
                "TicketID": tid,
                "DealerID": ticket_data.get("DealerID"),
                "DealerName": ticket_data.get("DealerName"),
                "CreatedOn": ticket_data.get("CreatedOn"),
                "TicketStatus": ticket_data.get("TicketStatus"),
                "TicketStatusText": ticket_data.get("TicketStatusText"),
                "ERPPurchaseOrder": ticket_data.get("ERPPurchaseOrder"),
                "AmountIncludingTax": ticket_data.get("AmountIncludingTax"),
                "ERPFreeOrder": erp_free_order,
                "SerialID": ticket_data.get("SerialID"),
                "ChassisNumber": ticket_data.get("ChassisNumber"),
                "Sales Order": ticket_data.get("Sales Order") or ticket_data.get("SalesOrder") or ticket_data.get("LookupSalesOrder"),
                "_Lookup1": lookup1,
                "_Lookup2": lookup2,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        for col in [
            "TicketID",
            "DealerID",
            "DealerName",
            "ERPPurchaseOrder",
            "ERPFreeOrder",
            "SerialID",
            "ChassisNumber",
            "Sales Order",
            "_Lookup1",
            "_Lookup2",
        ]:
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str).str.strip()
    return df


def normalize_vehicle_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", as_clean_str(value) or "").upper()


def build_vehicle_candidate_df(ticket_df: pd.DataFrame) -> pd.DataFrame:
    if ticket_df.empty:
        return pd.DataFrame(columns=["TicketID", "Candidate", "CandidateSource"])

    rows: List[Dict[str, str]] = []
    for _, row in ticket_df.iterrows():
        ticket_id = as_clean_str(row.get("TicketID"))
        if not ticket_id:
            continue
        seen = set()
        for source, raw_value in [
            ("serial_direct", row.get("SerialID")),
            ("chassis_direct", row.get("ChassisNumber")),
        ]:
            candidate = normalize_vehicle_id(raw_value)
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            rows.append({
                "TicketID": ticket_id,
                "Candidate": candidate,
                "CandidateSource": source,
            })
    return pd.DataFrame(rows)


def fetch_vehicle_dispatch_by_serial_candidates(
    conn,
    ticket_df: pd.DataFrame,
) -> pd.DataFrame:
    candidates_df = build_vehicle_candidate_df(ticket_df)
    if candidates_df.empty:
        return pd.DataFrame(columns=[
            "TicketID",
            "Candidate",
            "CandidateSource",
            "MatchedSerial",
            "VehicleDispatchDate",
            "VehicleDispatchDeliveryDoc",
            "VehicleDispatchSalesOrder",
        ])

    unique_candidates = sorted(set(candidates_df["Candidate"].tolist()))
    frames: List[pd.DataFrame] = []

    for batch in safe_chunks(unique_candidates, 300):
        values_sql = "\nUNION ALL\n".join(
            f"SELECT '{sql_quote(candidate)}' AS \"InputCandidate\" FROM DUMMY"
            for candidate in batch
            if candidate
        )
        sql = f"""
WITH input_candidates AS (
{values_sql}
),
matched_units AS (
    SELECT DISTINCT
        i."InputCandidate",
        o."SERNR" AS "MatchedSerial"
    FROM input_candidates i
    INNER JOIN "SAPHANADB"."OBJK" o
        ON o."MANDT" = '{sql_quote(SAP_CLIENT)}'
       AND o."SERNR" = i."InputCandidate"
    UNION
    SELECT DISTINCT
        i."InputCandidate",
        e."SERNR" AS "MatchedSerial"
    FROM input_candidates i
    INNER JOIN "SAPHANADB"."EQUI" e
        ON e."MANDT" = '{sql_quote(SAP_CLIENT)}'
       AND e."SERNR" = i."InputCandidate"
    UNION
    SELECT DISTINCT
        i."InputCandidate",
        z."SERNR" AS "MatchedSerial"
    FROM input_candidates i
    INNER JOIN "SAPHANADB"."ZTSD002" z
        ON z."MANDT" = '{sql_quote(SAP_CLIENT)}'
       AND z."WERKS" = '3091'
       AND z."SERNR2" = i."InputCandidate"
),
vehicle_so AS (
    SELECT DISTINCT
        mu."InputCandidate",
        mu."MatchedSerial",
        s2."SDAUFNR" AS "Sales Order"
    FROM matched_units mu
    INNER JOIN "SAPHANADB"."OBJK" o
        ON o."MANDT" = '{sql_quote(SAP_CLIENT)}'
       AND o."SERNR" = mu."MatchedSerial"
    INNER JOIN "SAPHANADB"."SER02" s2
        ON s2."MANDT" = o."MANDT"
       AND s2."OBKNR" = o."OBKNR"
       AND LPAD(TO_VARCHAR(s2."POSNR"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."VBAK" vbak
        ON vbak."MANDT" = s2."MANDT"
       AND vbak."VBELN" = s2."SDAUFNR"
       AND vbak."VKORG" = '{sql_quote(SALES_ORG)}'
    INNER JOIN "SAPHANADB"."VBAP" vbap
        ON vbap."MANDT" = s2."MANDT"
       AND vbap."VBELN" = s2."SDAUFNR"
       AND LPAD(TO_VARCHAR(vbap."POSNR"), 6, '0') = '000010'
       AND vbap."MATNR" LIKE 'Z%'
),
vehicle_pgi AS (
    SELECT DISTINCT
        vs."InputCandidate",
        vs."MatchedSerial",
        vs."Sales Order",
        lips."VBELN" AS "Delivery Doc",
        likp."WADAT_IST" AS "Dispatch Date"
    FROM vehicle_so vs
    INNER JOIN "SAPHANADB"."LIPS" lips
        ON lips."MANDT" = '{sql_quote(SAP_CLIENT)}'
       AND lips."VGBEL" = vs."Sales Order"
       AND LPAD(TO_VARCHAR(lips."VGPOS"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."LIKP" likp
        ON likp."MANDT" = lips."MANDT"
       AND likp."VBELN" = lips."VBELN"
    WHERE likp."WADAT_IST" IS NOT NULL
      AND likp."WADAT_IST" <> '00000000'
)
SELECT
    vp."InputCandidate",
    vp."MatchedSerial",
    MIN(vp."Dispatch Date") AS "VehicleDispatchDate",
    MIN(vp."Delivery Doc") AS "VehicleDispatchDeliveryDoc",
    MIN(vp."Sales Order") AS "VehicleDispatchSalesOrder"
FROM vehicle_pgi vp
GROUP BY
    vp."InputCandidate",
    vp."MatchedSerial"
ORDER BY
    vp."InputCandidate",
    MIN(vp."Dispatch Date")
"""
        part = pd.read_sql(sql, conn)
        if not part.empty:
            frames.append(part)

    if not frames:
        return pd.DataFrame(columns=[
            "TicketID",
            "Candidate",
            "CandidateSource",
            "MatchedSerial",
            "VehicleDispatchDate",
            "VehicleDispatchDeliveryDoc",
            "VehicleDispatchSalesOrder",
        ])

    dispatch_df = pd.concat(frames, ignore_index=True)
    for col in ["InputCandidate", "MatchedSerial", "VehicleDispatchDate", "VehicleDispatchDeliveryDoc", "VehicleDispatchSalesOrder"]:
        if col in dispatch_df.columns:
            dispatch_df[col] = dispatch_df[col].fillna("").astype(str).str.strip()

    dispatch_df = dispatch_df.sort_values(
        ["InputCandidate", "VehicleDispatchDate", "VehicleDispatchSalesOrder", "VehicleDispatchDeliveryDoc"],
        na_position="last",
    ).drop_duplicates(subset=["InputCandidate"], keep="first")

    resolved = candidates_df.merge(
        dispatch_df,
        how="left",
        left_on="Candidate",
        right_on="InputCandidate",
    )
    resolved = resolved[resolved["VehicleDispatchDate"].fillna("").astype(str).str.strip() != ""].copy()
    if resolved.empty:
        return pd.DataFrame(columns=[
            "TicketID",
            "Candidate",
            "CandidateSource",
            "MatchedSerial",
            "VehicleDispatchDate",
            "VehicleDispatchDeliveryDoc",
            "VehicleDispatchSalesOrder",
        ])

    resolved = resolved.sort_values(
        ["TicketID", "CandidateSource", "VehicleDispatchDate"],
        key=lambda s: s.map({"serial_direct": 0, "chassis_direct": 1}).fillna(9) if s.name == "CandidateSource" else s,
        na_position="last",
    ).drop_duplicates(subset=["TicketID"], keep="first")

    return resolved[[
        "TicketID",
        "Candidate",
        "CandidateSource",
        "MatchedSerial",
        "VehicleDispatchDate",
        "VehicleDispatchDeliveryDoc",
        "VehicleDispatchSalesOrder",
    ]].reset_index(drop=True)


def fetch_vehicle_dispatch_by_sales_orders(
    conn,
    sales_orders: List[str],
) -> pd.DataFrame:
    clean_sales_orders = sorted({
        as_clean_str(so)
        for so in (sales_orders or [])
        if as_clean_str(so)
    })
    if not clean_sales_orders:
        return pd.DataFrame(columns=[
            "Sales Order",
            "MatchedSerial",
            "VehicleDispatchDate",
            "VehicleDispatchDeliveryDoc",
        ])

    frames: List[pd.DataFrame] = []
    for batch in safe_chunks(clean_sales_orders, 300):
        values_sql = "\nUNION ALL\n".join(
            f"SELECT '{sql_quote(so)}' AS \"Sales Order\" FROM DUMMY"
            for so in batch
            if so
        )
        sql = f"""
WITH input_so AS (
{values_sql}
),
serial_from_so AS (
    SELECT DISTINCT
        i."Sales Order",
        o."SERNR" AS "MatchedSerial"
    FROM input_so i
    INNER JOIN "SAPHANADB"."VBAK" vbak
        ON vbak."MANDT" = '{sql_quote(SAP_CLIENT)}'
       AND vbak."VBELN" = i."Sales Order"
       AND vbak."VKORG" = '{sql_quote(SALES_ORG)}'
    INNER JOIN "SAPHANADB"."VBAP" vbap
        ON vbap."MANDT" = vbak."MANDT"
       AND vbap."VBELN" = vbak."VBELN"
       AND LPAD(TO_VARCHAR(vbap."POSNR"), 6, '0') = '000010'
       AND vbap."MATNR" LIKE 'Z%'
    INNER JOIN "SAPHANADB"."SER02" s2
        ON s2."MANDT" = '{sql_quote(SAP_CLIENT)}'
       AND s2."SDAUFNR" = i."Sales Order"
       AND LPAD(TO_VARCHAR(s2."POSNR"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."OBJK" o
        ON o."MANDT" = s2."MANDT"
       AND o."OBKNR" = s2."OBKNR"
),
vehicle_pgi AS (
    SELECT DISTINCT
        so."Sales Order",
        so."MatchedSerial",
        lips."VBELN" AS "Delivery Doc",
        likp."WADAT_IST" AS "Dispatch Date"
    FROM serial_from_so so
    INNER JOIN "SAPHANADB"."LIPS" lips
        ON lips."MANDT" = '{sql_quote(SAP_CLIENT)}'
       AND lips."VGBEL" = so."Sales Order"
       AND LPAD(TO_VARCHAR(lips."VGPOS"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."LIKP" likp
        ON likp."MANDT" = lips."MANDT"
       AND likp."VBELN" = lips."VBELN"
    WHERE likp."WADAT_IST" IS NOT NULL
      AND likp."WADAT_IST" <> '00000000'
)
SELECT
    "Sales Order",
    "MatchedSerial",
    MIN("Dispatch Date") AS "VehicleDispatchDate",
    MIN("Delivery Doc") AS "VehicleDispatchDeliveryDoc"
FROM vehicle_pgi
GROUP BY
    "Sales Order",
    "MatchedSerial"
ORDER BY
    "Sales Order",
    MIN("Dispatch Date")
"""
        part = pd.read_sql(sql, conn)
        if not part.empty:
            frames.append(part)

    if not frames:
        return pd.DataFrame(columns=[
            "Sales Order",
            "MatchedSerial",
            "VehicleDispatchDate",
            "VehicleDispatchDeliveryDoc",
        ])

    out = pd.concat(frames, ignore_index=True)
    for col in ["Sales Order", "MatchedSerial", "VehicleDispatchDate", "VehicleDispatchDeliveryDoc"]:
        if col in out.columns:
            out[col] = out[col].fillna("").astype(str).str.strip()
    out = out.sort_values(
        ["Sales Order", "VehicleDispatchDate", "VehicleDispatchDeliveryDoc"],
        na_position="last",
    ).drop_duplicates(subset=["Sales Order"], keep="first")
    return out.reset_index(drop=True)


def fetch_vehicle_base_summary(
    conn,
    cutoff_yyyymmdd: str = "20250101",
) -> Dict[str, Any]:
    """Comprehensive baseline: valid-PGI sales, in-stock
    (at plant 3111 LGORT 0024/0026), and in-transit (open POs, no GR yet).

    Returns both sales-only and broader denominator bases per series, plus
    per-chassis PGI dates so
    the analysis dashboard can:
      - compute sales / repair-rate metrics against 2025+ valid PGI sold
        vehicles only, and
      - keep a wider pipeline/base view available (sold + in-stock +
        in-transit healthy vehicles never seen in tickets), and
      - resolve failure timing (delivery→ticket days) via chassis→PGI even
        when the LIKP/WADAT_IST route is empty.
    """
    def _q(sql_tmpl: str) -> str:
        return sql_tmpl.format(mandt=sql_quote(SAP_CLIENT), cutoff=sql_quote(cutoff_yyyymmdd))

    # ---- 1) Shipped chassis with PGI date (MSEG BWART outbound) ----
    shipped_df = pd.DataFrame()
    try:
        shipped_df = pd.read_sql(_q(PGI_MSEG_SQL_TEMPLATE), conn)
    except Exception as exc:
        logger.warning("PGI (MSEG) query failed: %s", exc)

    # ---- 2) In-stock chassis at plant 3111, LGORT 0024/0026 ----
    instock_df = pd.DataFrame()
    try:
        instock_df = pd.read_sql(_q(INSTOCK_MSKA_SQL_TEMPLATE), conn)
    except Exception as exc:
        logger.warning("In-stock (MSKA 0024/0026) query failed: %s", exc)

    # ---- 3) In-transit chassis via open POs (no GR yet) ----
    intransit_df = pd.DataFrame()
    try:
        intransit_df = pd.read_sql(_q(INTRANSIT_OPEN_PO_SQL_TEMPLATE), conn)
    except Exception as exc:
        logger.warning("In-transit (open PO) query failed: %s", exc)

    # ---- Legacy LIKP-based route (kept as an additional data source) ----
    likp_sql = f"""
SELECT DISTINCT
    s2."SDAUFNR" AS "Sales Order",
    o."SERNR" AS "Serial",
    z."SERNR2" AS "VIN",
    vbap."MATNR" AS "Material",
    vbap."ARKTX" AS "Description",
    lips."VBELN" AS "Delivery Doc",
    likp."WADAT_IST" AS "PGI Date"
FROM "SAPHANADB"."SER02" s2
INNER JOIN "SAPHANADB"."OBJK" o
    ON o."MANDT" = s2."MANDT"
   AND o."OBKNR" = s2."OBKNR"
INNER JOIN "SAPHANADB"."VBAK" vbak
    ON vbak."MANDT" = s2."MANDT"
   AND vbak."VBELN" = s2."SDAUFNR"
   AND vbak."VKORG" = '{sql_quote(SALES_ORG)}'
INNER JOIN "SAPHANADB"."VBAP" vbap
    ON vbap."MANDT" = s2."MANDT"
   AND vbap."VBELN" = s2."SDAUFNR"
   AND LPAD(TO_VARCHAR(vbap."POSNR"), 6, '0') = '000010'
   AND vbap."MATNR" LIKE 'Z%'
LEFT JOIN "SAPHANADB"."ZTSD002" z
    ON z."MANDT" = o."MANDT"
   AND z."WERKS" = '3091'
   AND z."SERNR" = o."SERNR"
INNER JOIN "SAPHANADB"."LIPS" lips
    ON lips."MANDT" = s2."MANDT"
   AND lips."VGBEL" = s2."SDAUFNR"
   AND LPAD(TO_VARCHAR(lips."VGPOS"), 6, '0') = '000010'
INNER JOIN "SAPHANADB"."LIKP" likp
    ON likp."MANDT" = lips."MANDT"
   AND likp."VBELN" = lips."VBELN"
WHERE s2."MANDT" = '{sql_quote(SAP_CLIENT)}'
  AND LPAD(TO_VARCHAR(s2."POSNR"), 6, '0') = '000010'
  AND likp."WADAT_IST" IS NOT NULL
  AND likp."WADAT_IST" <> '00000000'
  AND likp."WADAT_IST" >= '{sql_quote(cutoff_yyyymmdd)}'
"""
    likp_df = pd.DataFrame()
    try:
        likp_df = pd.read_sql(likp_sql, conn)
    except Exception as exc:
        logger.warning("LIKP-based PGI query failed: %s", exc)

    # ---- Normalise all frames ----
    def _clean(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        out = df.copy()
        for col in out.columns:
            out[col] = out[col].fillna("").astype(str).str.strip()
        return out

    shipped_df = _clean(shipped_df)
    instock_df = _clean(instock_df)
    intransit_df = _clean(intransit_df)
    likp_df = _clean(likp_df)
    if not shipped_df.empty and "PGI Date" in shipped_df.columns:
        shipped_df = shipped_df.sort_values(
            ["PGI Date", "Sales Order", "PGI Material Doc", "PGI Item"],
            na_position="last",
        ).reset_index(drop=True)
    if not likp_df.empty and "PGI Date" in likp_df.columns:
        likp_df = likp_df.sort_values(
            ["PGI Date", "Sales Order", "Delivery Doc"],
            na_position="last",
        ).reset_index(drop=True)

    # ---- Union chassis → (series, best PGI date, source) ----
    # Precedence: MSEG PGI > LIKP WADAT_IST > (in-stock/in-transit have no PGI).
    chassis_map: Dict[str, Dict[str, str]] = {}   # SERNR -> {series, pgi, source, salesOrder, vin, material, description}
    sales_order_pgi: Dict[str, str] = {}          # Sales Order -> pgi date
    series_by_chassis: Dict[str, str] = {}        # Serial / VIN aliases -> series
    series_by_sales_order: Dict[str, str] = {}    # Sales Order -> series

    def _norm_date(v: str) -> str:
        """Return YYYY-MM-DD or '' for missing."""
        s = (v or "").strip()
        if not s or s == "00000000":
            return ""
        # SAP dates come back as 'YYYYMMDD' or 'YYYY-MM-DD HH:MM:SS' from TO_VARCHAR
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return s

    def _register(serial: str, series: str, pgi: str, source: str,
                  sales_order: str, vin: str, material: str, description: str,
                  status: str) -> None:
        if not serial:
            return
        norm_pgi = _norm_date(pgi)
        cur = chassis_map.get(serial)
        if cur is None:
            chassis_map[serial] = {
                "series": series,
                "pgi": norm_pgi,
                "source": source if norm_pgi else "",
                "salesOrder": sales_order,
                "vin": vin,
                "material": material,
                "description": description,
                "status": status,
            }
        else:
            if should_replace_series(cur.get("series"), series):
                cur["series"] = series
            # Prefer a PGI date if we didn't have one; prefer MSEG > LIKP
            if norm_pgi and not cur.get("pgi"):
                cur["pgi"] = norm_pgi
                cur["source"] = source
            elif norm_pgi and cur.get("source") == "likp" and source == "mseg":
                cur["pgi"] = norm_pgi
                cur["source"] = source
            # Prefer status "shipped" > "inStock" > "inTransit"
            rank = {"shipped": 3, "inStock": 2, "inTransit": 1, "": 0}
            if rank.get(status, 0) > rank.get(cur.get("status", ""), 0):
                cur["status"] = status
            for key, val in (("salesOrder", sales_order), ("vin", vin),
                             ("material", material), ("description", description)):
                if val and not cur.get(key):
                    cur[key] = val
        if sales_order and norm_pgi:
            for so_key in vehicle_lookup_aliases(sales_order):
                sales_order_pgi.setdefault(so_key, norm_pgi)

    def _series(row: dict) -> str:
        return extract_series_code(
            row.get("Material"), row.get("Description"),
            row.get("Serial"), row.get("VIN"),
        )

    for _, row in shipped_df.iterrows():
        r = row.to_dict()
        _register(r.get("Serial",""), _series(r), r.get("PGI Date",""), "mseg",
                  r.get("Sales Order",""), r.get("VIN",""), r.get("Material",""),
                  r.get("Description",""), "shipped")
    for _, row in likp_df.iterrows():
        r = row.to_dict()
        _register(r.get("Serial",""), _series(r), r.get("PGI Date",""), "likp",
                  r.get("Sales Order",""), r.get("VIN",""), r.get("Material",""),
                  r.get("Description",""), "shipped")
    for _, row in instock_df.iterrows():
        r = row.to_dict()
        _register(r.get("Serial",""), _series(r), "", "",
                  r.get("Sales Order",""), r.get("VIN",""), r.get("Material",""),
                  r.get("Description",""), "inStock")
    for _, row in intransit_df.iterrows():
        r = row.to_dict()
        _register(r.get("Serial",""), _series(r), "", "",
                  r.get("Sales Order",""), "", r.get("Material",""),
                  r.get("Description",""), "inTransit")

    # ---- Drop excluded series ----
    for ser in list(chassis_map.keys()):
        s = chassis_map[ser].get("series", "")
        if is_excluded_series_code(s):
            chassis_map.pop(ser, None)

    # ---- Roll up ----
    series_base_shipped: Dict[str, int] = {}
    series_base_in_stock: Dict[str, int] = {}
    series_base_in_transit: Dict[str, int] = {}
    series_base_total: Dict[str, int] = {}
    pgi_by_chassis: Dict[str, str] = {}
    for serial, info in chassis_map.items():
        s = info.get("series", "UNKNOWN")
        status = info.get("status", "")
        series_base_total[s] = series_base_total.get(s, 0) + 1
        if status == "shipped":
            series_base_shipped[s] = series_base_shipped.get(s, 0) + 1
        elif status == "inStock":
            series_base_in_stock[s] = series_base_in_stock.get(s, 0) + 1
        elif status == "inTransit":
            series_base_in_transit[s] = series_base_in_transit.get(s, 0) + 1
        if info.get("pgi"):
            for key in vehicle_lookup_aliases(serial, info.get("vin")):
                pgi_by_chassis[key] = info["pgi"]
        if s and s != "UNKNOWN" and not is_excluded_series_code(s):
            for key in vehicle_lookup_aliases(serial, info.get("vin")):
                series_by_chassis[key] = s
            sales_order = as_clean_str(info.get("salesOrder")) or ""
            if sales_order:
                for so_key in vehicle_lookup_aliases(sales_order):
                    series_by_sales_order[so_key] = s

    def _sorted(d: Dict[str, int]) -> Dict[str, int]:
        return dict(sorted(d.items()))

    payload: Dict[str, Any] = {
        "generatedAt": iso_utc_now(),
        "cutoff": f"{cutoff_yyyymmdd[:4]}-{cutoff_yyyymmdd[4:6]}-{cutoff_yyyymmdd[6:]}",
        # Sales-only base: valid PGI vehicles after the cutoff.
        "seriesSales": _sorted(series_base_shipped),
        # Broader denominator: valid PGI sold + in-stock + in-transit.
        "seriesBase": _sorted(series_base_total),
        # Breakdown so the UI can show how the denominator was composed.
        "seriesBaseBreakdown": {
            s: {
                "shipped": series_base_shipped.get(s, 0),
                "inStock": series_base_in_stock.get(s, 0),
                "inTransit": series_base_in_transit.get(s, 0),
                "total": series_base_total.get(s, 0),
            }
            for s in sorted(series_base_total.keys())
        },
        "totalSales": int(sum(series_base_shipped.values())),
        # Chassis-level PGI dates. Consumed by the dashboard's
        # `getDeliveryDate(row)` as an additional dispatch source so failure
        # timing can be computed for tickets whose LIKP-based dispatch date
        # was missing.
        "pgiByChassis": pgi_by_chassis,
        # Vehicle-series mappings derived from SAP sales-order / chassis joins.
        # The dashboard uses these first so Model Series no longer depends
        # primarily on fuzzy parsing of ticket text.
        "seriesByChassis": dict(sorted(series_by_chassis.items())),
        "seriesBySalesOrder": dict(sorted(series_by_sales_order.items())),
        # Sales-order-level PGI dates (fallback when only the SO is known).
        "pgiBySalesOrder": dict(sorted(sales_order_pgi.items())),
        "totalVehicles": int(sum(series_base_total.values())),
        "totalShipped": int(sum(series_base_shipped.values())),
        "totalInStock": int(sum(series_base_in_stock.values())),
        "totalInTransit": int(sum(series_base_in_transit.values())),
        "sources": {
            "shippedMseg": int(len(shipped_df)),
            "shippedLikp": int(len(likp_df)),
            "inStockMska": int(len(instock_df)),
            "inTransitOpenPo": int(len(intransit_df)),
        },
    }
    logger.info(
        "Vehicle base summary: total=%s (shipped=%s, in-stock=%s, in-transit=%s); PGI dates for %s chassis",
        payload["totalVehicles"], payload["totalShipped"],
        payload["totalInStock"], payload["totalInTransit"],
        len(pgi_by_chassis),
    )
    return payload


def resolve_vehicle_dispatch_rows(
    all_ticket_df: pd.DataFrame,
    so_item_df: pd.DataFrame,
    direct_dispatch_df: pd.DataFrame,
    sales_order_dispatch_df: pd.DataFrame,
) -> pd.DataFrame:
    if all_ticket_df.empty:
        return pd.DataFrame(columns=[
            "TicketID",
            "Vehicle Dispatch Date",
            "Vehicle Dispatch Source",
            "Vehicle Dispatch Serial",
            "Vehicle Dispatch Sales Order",
            "Vehicle Dispatch Delivery Doc",
        ])

    ticket_df = all_ticket_df.copy()
    for col in ["TicketID", "SerialID", "ChassisNumber", "Sales Order"]:
        if col in ticket_df.columns:
            ticket_df[col] = ticket_df[col].fillna("").astype(str).str.strip()

    direct_map = {
        as_clean_str(row["TicketID"]): row
        for _, row in direct_dispatch_df.iterrows()
        if as_clean_str(row.get("TicketID"))
    }

    sales_order_map = {}
    if not so_item_df.empty and "Sales Order" in so_item_df.columns:
        work = so_item_df.copy()
        for col in ["TicketID", "Sales Order"]:
            if col in work.columns:
                work[col] = work[col].fillna("").astype(str).str.strip()
        work = work[work["Sales Order"] != ""].copy()
        if not work.empty:
            first_so = work.sort_values(
                ["TicketID", "Sales Order", "Sales Order Item"],
                na_position="last",
            ).drop_duplicates(subset=["TicketID"], keep="first")
            sales_order_map = {
                as_clean_str(row["TicketID"]): as_clean_str(row["Sales Order"])
                for _, row in first_so.iterrows()
                if as_clean_str(row.get("TicketID")) and as_clean_str(row.get("Sales Order"))
            }

    so_dispatch_map = {
        as_clean_str(row["Sales Order"]): row
        for _, row in sales_order_dispatch_df.iterrows()
        if as_clean_str(row.get("Sales Order"))
    }

    rows: List[Dict[str, str]] = []
    for _, row in ticket_df.iterrows():
        ticket_id = as_clean_str(row.get("TicketID"))
        if not ticket_id:
            continue

        resolved = direct_map.get(ticket_id)
        source = ""
        dispatch_date = ""
        dispatch_serial = ""
        dispatch_so = ""
        dispatch_doc = ""

        if resolved is not None:
            source = "vehicle_serial" if as_clean_str(resolved.get("CandidateSource")) == "serial_direct" else "vehicle_chassis"
            dispatch_date = as_clean_str(resolved.get("VehicleDispatchDate"))
            dispatch_serial = as_clean_str(resolved.get("MatchedSerial"))
            dispatch_so = as_clean_str(resolved.get("VehicleDispatchSalesOrder"))
            dispatch_doc = as_clean_str(resolved.get("VehicleDispatchDeliveryDoc"))
        else:
            sales_order = sales_order_map.get(ticket_id) or as_clean_str(row.get("Sales Order"))
            so_resolved = so_dispatch_map.get(sales_order or "")
            if so_resolved is not None:
                source = "sales_order_serial"
                dispatch_date = as_clean_str(so_resolved.get("VehicleDispatchDate"))
                dispatch_serial = as_clean_str(so_resolved.get("MatchedSerial"))
                dispatch_so = as_clean_str(so_resolved.get("Sales Order")) or sales_order
                dispatch_doc = as_clean_str(so_resolved.get("VehicleDispatchDeliveryDoc"))

        rows.append({
            "TicketID": ticket_id,
            "Vehicle Dispatch Date": dispatch_date,
            "Vehicle Dispatch Source": source,
            "Vehicle Dispatch Serial": dispatch_serial,
            "Vehicle Dispatch Sales Order": dispatch_so,
            "Vehicle Dispatch Delivery Doc": dispatch_doc,
        })

    return pd.DataFrame(rows)


def build_vehicle_dispatch_payload(vehicle_df: pd.DataFrame) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if vehicle_df.empty:
        return payload

    work = vehicle_df.copy()
    for col in [
        "TicketID",
        "Vehicle Dispatch Date",
        "Vehicle Dispatch Source",
        "Vehicle Dispatch Serial",
        "Vehicle Dispatch Sales Order",
        "Vehicle Dispatch Delivery Doc",
    ]:
        if col in work.columns:
            work[col] = work[col].fillna("").astype(str).str.strip()

    for _, row in work.iterrows():
        ticket_id = sanitize_fb_key(row.get("TicketID"))
        if not ticket_id:
            continue
        base = f"tickets/{ticket_id}/ticket"
        payload[f"{base}/Vehicle Dispatch Date"] = row.get("Vehicle Dispatch Date", "")
        payload[f"{base}/Vehicle Dispatch Source"] = row.get("Vehicle Dispatch Source", "")
        payload[f"{base}/Vehicle Dispatch Serial"] = row.get("Vehicle Dispatch Serial", "")
        payload[f"{base}/Vehicle Dispatch Sales Order"] = row.get("Vehicle Dispatch Sales Order", "")
        payload[f"{base}/Vehicle Dispatch Delivery Doc"] = row.get("Vehicle Dispatch Delivery Doc", "")
        payload[f"{base}/vehicleDispatchSyncAt"] = SERVER_TIMESTAMP

    return payload


def upload_vehicle_dispatch_to_firebase(vehicle_df: pd.DataFrame):
    payload_all = build_vehicle_dispatch_payload(vehicle_df)
    if not payload_all:
        return
    upload_payload_in_batches(FIREBASE_ROOT, payload_all, label="FB VEHICLE DISPATCH")


# =================== SAP æŸ¥è¯¢ ===================
def build_sql(input_pairs: List[Tuple[str, str, str]]) -> str:
    values_sql = "\nUNION ALL\n".join(
        [
            f"SELECT '{sql_quote(ticket_id)}' AS \"TicketID\", "
            f"'{sql_quote(erp)}' AS \"ERPFreeOrder\", "
            f"'{sql_quote(lookup)}' AS \"LookupSalesOrder\" FROM DUMMY"
            for ticket_id, erp, lookup in input_pairs
            if lookup
        ]
    )

    sql = f"""
WITH input_so AS (
{values_sql}
),
so_base AS (
    SELECT DISTINCT
        io."TicketID",
        io."ERPFreeOrder",
        io."LookupSalesOrder",
        vbak."MANDT"                      AS "SAP Client",
        vbak."VBELN"                      AS "Sales Order",
        vbak."ERDAT"                      AS "SO Created Date",
        vbak."ERNAM"                      AS "Purchaser",
        vbak."WAERK"                      AS "Currency"
    FROM input_so io
    INNER JOIN "SAPHANADB"."VBAK" vbak
        ON vbak."VBELN" = io."LookupSalesOrder"
       AND vbak."VKORG" = '{sql_quote(SALES_ORG)}'
       AND vbak."MANDT" = '{sql_quote(SAP_CLIENT)}'
),
gi_summary AS (
    SELECT
        lips."MANDT"                      AS "SAP Client",
        lips."VGBEL"                      AS "Sales Order",
        lips."VGPOS"                      AS "Sales Order Item",
        COUNT(DISTINCT lips."VBELN")      AS "Delivery Count",
        MIN(likp."WADAT_IST")             AS "First Issue Date"
    FROM "SAPHANADB"."LIPS" lips
    INNER JOIN so_base sb
        ON sb."SAP Client" = lips."MANDT"
       AND sb."Sales Order" = lips."VGBEL"
    INNER JOIN "SAPHANADB"."LIKP" likp
        ON likp."MANDT" = lips."MANDT"
       AND likp."VBELN" = lips."VBELN"
    WHERE likp."WADAT_IST" IS NOT NULL
    GROUP BY
        lips."MANDT",
        lips."VGBEL",
        lips."VGPOS"
),
item_base AS (
    SELECT DISTINCT
        sb."TicketID",
        sb."ERPFreeOrder",
        sb."LookupSalesOrder",
        sb."SAP Client",
        sb."Sales Order",
        sb."SO Created Date",
        sb."Purchaser",
        sb."Currency",
        vbap."POSNR"                      AS "Sales Order Item",
        vbap."MATNR"                      AS "Material",
        vbap."ARKTX"                      AS "Description",
        vbap."KWMENG"                     AS "Order Qty",
        vbap."VRKME"                      AS "Sales Unit",
        vbap."NETWR"                      AS "Net Value",
        vbap."ABGRU"                      AS "Rejection Reason"
    FROM so_base sb
    INNER JOIN "SAPHANADB"."VBAP" vbap
        ON vbap."MANDT" = sb."SAP Client"
       AND vbap."VBELN" = sb."Sales Order"
),
final_ranked AS (
    SELECT
        ib."TicketID",
        ib."ERPFreeOrder",
        ib."LookupSalesOrder",
        ib."Sales Order",
        ib."SO Created Date",
        ib."Purchaser",
        ib."Currency",
        ib."Sales Order Item",
        ib."Material",
        ib."Description",
        ib."Order Qty",
        ib."Sales Unit",
        ib."Net Value",
        COALESCE(gs."Delivery Count", 0) AS "Delivery Count",
        gs."First Issue Date"             AS "First Issue Date",
        ib."Rejection Reason",
        CASE
            WHEN ib."Rejection Reason" IS NOT NULL AND ib."Rejection Reason" <> ''
                THEN 'Rejected'
            ELSE 'Not Rejected'
        END AS "Item Rejection Status",
        ROW_NUMBER() OVER (
            PARTITION BY
                ib."TicketID",
                ib."LookupSalesOrder",
                ib."Sales Order",
                ib."Sales Order Item",
                ib."Material",
                ib."Description",
                ib."Order Qty",
                ib."Sales Unit"
            ORDER BY ib."Sales Order Item"
        ) AS rn
    FROM item_base ib
    LEFT JOIN gi_summary gs
        ON gs."SAP Client" = ib."SAP Client"
       AND gs."Sales Order" = ib."Sales Order"
       AND gs."Sales Order Item" = ib."Sales Order Item"
)
SELECT
    "TicketID",
    "ERPFreeOrder",
    "LookupSalesOrder",
    "Sales Order",
    "SO Created Date",
    "Purchaser",
    "Currency",
    "Sales Order Item",
    "Material",
    "Description",
    "Order Qty",
    "Sales Unit",
    "Net Value",
    "Delivery Count",
    "First Issue Date",
    "Rejection Reason",
    "Item Rejection Status"
FROM final_ranked
WHERE rn = 1
ORDER BY
    "TicketID",
    "Sales Order",
    "Sales Order Item"
"""
    return sql
def fetch_so_items(conn, ticket_df: pd.DataFrame) -> pd.DataFrame:
    if ticket_df.empty:
        return pd.DataFrame(columns=[
            "TicketID", "ERPFreeOrder", "LookupSalesOrder",
            "Sales Order", "SO Created Date", "Sales Order Item",
            "Purchaser", "Currency", "Material", "Description", "Order Qty", "Sales Unit",
            "Net Value",
            "Delivery Count", "Rejection Reason", "Item Rejection Status"
        ])

    pairs = []
    for _, row in ticket_df.iterrows():
        ticket_id = str(row.get("TicketID", "")).strip()
        erp = str(row.get("ERPFreeOrder", "")).strip()
        lookup1 = str(row.get("_Lookup1", "")).strip()
        lookup2 = str(row.get("_Lookup2", "")).strip()

        if lookup1:
            pairs.append((ticket_id, erp, lookup1))
        if lookup2 and lookup2 != lookup1:
            pairs.append((ticket_id, erp, lookup2))

    frames: List[pd.DataFrame] = []
    batches = safe_chunks(pairs, 300)

    logger.info("Total input lookup rows: %s", len(pairs))
    logger.info("Total SQL batches: %s", len(batches))

    for i, batch in enumerate(batches, start=1):
        logger.info("Running batch %s/%s with %s lookup rows...", i, len(batches), len(batch))
        sql = build_sql(batch)
        part = pd.read_sql(sql, conn)
        logger.info("Batch %s returned %s rows", i, len(part))
        if not part.empty:
            frames.append(part)

    if not frames:
        return pd.DataFrame(columns=[
            "TicketID", "ERPFreeOrder", "LookupSalesOrder",
            "Sales Order", "SO Created Date", "Sales Order Item",
            "Purchaser", "Currency", "Material", "Description", "Order Qty", "Sales Unit",
            "Net Value",
            "Delivery Count", "Rejection Reason", "Item Rejection Status"
        ])

    df = pd.concat(frames, ignore_index=True)

    for col in [
        "TicketID", "ERPFreeOrder", "LookupSalesOrder", "Sales Order",
        "Purchaser", "Currency", "Sales Order Item", "Material", "Description", "Sales Unit",
        "Rejection Reason", "Item Rejection Status"
    ]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    if "Net Value" in df.columns:
        df["Net Value"] = pd.to_numeric(df["Net Value"], errors="coerce").fillna(0.0)

    if "SO Created Date" in df.columns:
        dt = pd.to_datetime(df["SO Created Date"], errors="coerce")
        df["SO Created Date"] = dt.dt.strftime("%Y-%m-%d")
        df["SO Created Date"] = df["SO Created Date"].where(dt.notna(), "")

    if "Delivery Count" in df.columns:
        df["Delivery Count"] = pd.to_numeric(df["Delivery Count"], errors="coerce").fillna(0).astype(int)

    if "First Issue Date" in df.columns:
        dt = pd.to_datetime(df["First Issue Date"], errors="coerce")
        df["First Issue Date"] = dt.dt.strftime("%Y-%m-%d")
        df["First Issue Date"] = df["First Issue Date"].where(dt.notna(), "")

    return df


# =================== çŠ¶æ€è®¡ç®— ===================
def calc_issue_status(group: pd.DataFrame) -> str:
    counts = group["Delivery Count"].fillna(0).astype(int).tolist()
    if not counts:
        return "No issued yet"
    if all(x == 1 for x in counts):
        return "parts issued"
    if all(x == 0 for x in counts):
        return "No issued yet"
    if any(x == 0 for x in counts):
        return "partially issue"
    return "partially issue"


def calc_order_rejection_status(group: pd.DataFrame) -> str:
    flags = group["Item Rejection Status"].fillna("").astype(str).tolist()
    if not flags:
        return "Not Found"
    if all(x == "Rejected" for x in flags):
        return "Fully Rejected"
    if any(x == "Rejected" for x in flags):
        return "Partially Rejected"
    return "Not Rejected"


def choose_best_match_per_ticket(so_item_df: pd.DataFrame) -> pd.DataFrame:
    """
    åŒä¸€ä¸ª ticket å¯èƒ½ raw å’Œ 00+raw éƒ½æŸ¥åˆ°ã€‚
    ä¼˜å…ˆä¿ç•™ LookupSalesOrder == ERPFreeOrder çš„ç»“æžœï¼›
    å¦‚æžœåŽŸå€¼æŸ¥ä¸åˆ°ï¼Œå†ä¿ç•™è¡¥00çš„ç»“æžœã€‚
    """
    if so_item_df.empty:
        return so_item_df

    tmp = so_item_df.copy()
    tmp["_priority"] = tmp.apply(
        lambda r: 1 if str(r["LookupSalesOrder"]).strip() == str(r["ERPFreeOrder"]).strip() else 2,
        axis=1
    )

    best = (
        tmp[["TicketID", "Sales Order", "LookupSalesOrder", "_priority"]]
        .drop_duplicates()
        .sort_values(["TicketID", "_priority", "Sales Order", "LookupSalesOrder"])
        .groupby("TicketID", as_index=False)
        .first()
    )

    tmp = tmp.merge(
        best[["TicketID", "Sales Order", "LookupSalesOrder"]],
        how="inner",
        on=["TicketID", "Sales Order", "LookupSalesOrder"]
    ).drop(columns=["_priority"])

    return tmp


def dedupe_final_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    cols = [
        "TicketID",
        "ERPFreeOrder",
        "Sales Order",
        "Sales Order Item",
        "Material",
        "Description",
        "Order Qty",
        "Sales Unit",
        "Delivery Count",
        "First Issue Date",
        "Rejection Reason",
        "Item Rejection Status",
        "Order Rejection Status",
    ]
    keep_cols = [c for c in cols if c in df.columns]
    return df.drop_duplicates(subset=keep_cols).reset_index(drop=True)


def build_final_output(ticket_df: pd.DataFrame, so_item_df: pd.DataFrame) -> pd.DataFrame:
    final_cols = [
        "TicketID",
        "DealerID",
        "DealerName",
        "CreatedOn",
        "TicketStatus",
        "TicketStatusText",
        "ERPFreeOrder",
        "ERPPurchaseOrder",
        "AmountIncludingTax",
        "Sales Order",
        "SO Created Date",
        "Purchaser",
        "Currency",
        "Issue Status",
        "Sales Order Item",
        "Material",
        "Description",
        "Order Qty",
        "Sales Unit",
        "Net Value",
        "Delivery Count",
        "First Issue Date",
        "Rejection Reason",
        "Item Rejection Status",
        "Order Rejection Status",
    ]

    if ticket_df.empty:
        return pd.DataFrame(columns=final_cols)

    so_item_df = choose_best_match_per_ticket(so_item_df)

    # HANA fetch is authoritative for Sales Order.  The ticket snapshot also
    # carries a legacy Sales Order field; dropping it before the merge avoids
    # pandas renaming the two non-key columns to Sales Order_x/Sales Order_y.
    ticket_base = ticket_df.drop(columns=["Sales Order"], errors="ignore")
    merged = ticket_base.merge(
        so_item_df,
        how="left",
        on=["TicketID", "ERPFreeOrder"]
    )

    found = merged[merged["Sales Order"].fillna("").astype(str).str.strip() != ""].copy()

    if not found.empty:
        issue_df = (
            found.groupby(
                ["TicketID", "ERPFreeOrder", "Sales Order", "SO Created Date"],
                dropna=False
            )
            .apply(calc_issue_status)
            .reset_index(name="Issue Status")
        )

        reject_df = (
            found.groupby(
                ["TicketID", "ERPFreeOrder", "Sales Order", "SO Created Date"],
                dropna=False
            )
            .apply(calc_order_rejection_status)
            .reset_index(name="Order Rejection Status")
        )

        found = found.merge(
            issue_df,
            how="left",
            on=["TicketID", "ERPFreeOrder", "Sales Order", "SO Created Date"]
        ).merge(
            reject_df,
            how="left",
            on=["TicketID", "ERPFreeOrder", "Sales Order", "SO Created Date"]
        )

        for col in ["Rejection Reason", "Item Rejection Status"]:
            if col not in found.columns:
                found[col] = ""

        found_final = found[final_cols].copy()
    else:
        found_final = pd.DataFrame(columns=final_cols)

    found_ticket_ids = set(found_final["TicketID"].astype(str).tolist()) if not found_final.empty else set()

    not_found = ticket_df[~ticket_df["TicketID"].astype(str).isin(found_ticket_ids)].copy()
    if not not_found.empty:
        not_found["Sales Order"] = ""
        not_found["SO Created Date"] = ""
        not_found["Purchaser"] = ""
        not_found["Currency"] = ""
        not_found["ERPPurchaseOrder"] = ""
        not_found["AmountIncludingTax"] = ""
        not_found["Issue Status"] = "Not Found"
        not_found["Sales Order Item"] = ""
        not_found["Material"] = ""
        not_found["Description"] = ""
        not_found["Order Qty"] = ""
        not_found["Sales Unit"] = ""
        not_found["Net Value"] = ""
        not_found["Delivery Count"] = ""
        not_found["First Issue Date"] = ""
        not_found["Rejection Reason"] = ""
        not_found["Item Rejection Status"] = "Not Found"
        not_found["Order Rejection Status"] = "Not Found"

        not_found_final = not_found[final_cols].copy()
    else:
        not_found_final = pd.DataFrame(columns=final_cols)

    final_df = pd.concat([found_final, not_found_final], ignore_index=True)

    if not final_df.empty:
        blank_so_mask = final_df["Sales Order"].fillna("").astype(str).str.strip() == ""
        final_df.loc[blank_so_mask, "Issue Status"] = "Not Found"
        final_df.loc[blank_so_mask, "Order Rejection Status"] = "Not Found"
        final_df.loc[blank_so_mask, "Item Rejection Status"] = "Not Found"

        final_df = dedupe_final_rows(final_df)

        final_df = final_df.sort_values(
            ["ERPFreeOrder", "Sales Order", "Sales Order Item", "TicketID"],
            na_position="last"
        ).reset_index(drop=True)

        try:
            reservation_meta_map = fetch_reservation_meta_map_from_hana(
                final_df["Sales Order"].fillna("").astype(str).tolist()
            )
        except Exception as exc:
            logger.warning("Reservation metadata lookup failed; continuing without RKPF fields: %s", exc)
            reservation_meta_map = {}

        if reservation_meta_map:
            final_df["Reservation_Created_By"] = final_df["Sales Order"].fillna("").astype(str).map(
                lambda so: reservation_meta_map.get(as_clean_str(so) or "", {}).get("Reservation_Created_By", "")
            )
            final_df["Reservation_Cost_Center"] = final_df["Sales Order"].fillna("").astype(str).map(
                lambda so: reservation_meta_map.get(as_clean_str(so) or "", {}).get("Reservation_Cost_Center", "")
            )
        else:
            final_df["Reservation_Created_By"] = ""
            final_df["Reservation_Cost_Center"] = ""

    return final_df


# =================== Incremental Sync / Snapshot Compare ===================
def canonical_for_hash(value: Any) -> Any:
    """
    Make a stable JSON-compatible object for hashing.
    This avoids false changes caused only by dict key order or pandas NaN values.
    """
    value = norm(value)
    if isinstance(value, dict):
        return {str(k): canonical_for_hash(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, list):
        return [canonical_for_hash(v) for v in value]
    return value


def build_ticket_hash(ticket_node: Dict[str, Any]) -> str:
    clean_node = canonical_for_hash(ticket_node or {})
    raw = json.dumps(clean_node, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()
    
def firebase_node_to_dict(node: Any) -> Dict[str, Any]:
    """
    Firebase Admin SDK may return numeric-key objects as list.
    Convert list back to dict so numeric ticket ids like 41, 42 can be read.
    """
    if isinstance(node, dict):
        return node
 
    if isinstance(node, list):
        return {str(i): v for i, v in enumerate(node) if v is not None}
 
    return {}

def load_old_ticket_hashes() -> Dict[str, str]:
    ref = db.reference(f"{FIREBASE_ROOT}/ticketSyncSnapshot")
    data_raw = ref.get()
    data = firebase_node_to_dict(data_raw)
 
    out: Dict[str, str] = {}
 
    for ticket_id, value in data.items():
        if isinstance(value, dict):
            h = as_clean_str(value.get("hash")) or ""
        else:
            h = as_clean_str(value) or ""
 
        if h:
            out[str(ticket_id)] = h
 
    return out


def get_changed_tickets(
    new_snapshot: Dict[str, Any],
    old_hashes: Dict[str, str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    changed: Dict[str, Any] = {}
    changed_hash_payload: Dict[str, Any] = {}

    for ticket_id, node in new_snapshot.items():
        new_hash = build_ticket_hash(node)
        old_hash = old_hashes.get(ticket_id, "")

        if new_hash != old_hash:
            changed[ticket_id] = node
            changed_hash_payload[ticket_id] = {
                "hash": new_hash,
                "updatedAt": SERVER_TIMESTAMP,
            }

    return changed, changed_hash_payload


def get_deleted_tickets(
    new_snapshot: Dict[str, Any],
    old_hashes: Dict[str, str],
) -> List[str]:
    return sorted(set(old_hashes.keys()) - set(new_snapshot.keys()))


def build_delete_removed_tickets_payload(deleted_ticket_ids: List[str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for tid in deleted_ticket_ids:
        tid_key = sanitize_fb_key(tid)
        if not tid_key:
            continue
        payload[f"tickets/{tid_key}"] = None
        payload[f"ticketSyncSnapshot/{tid_key}"] = None
    return payload


def build_clear_so_payload(ticket_ids: List[str], reason: str = "Not Found") -> Dict[str, Any]:
    """
    Clear old SO fields for tickets that no longer match SAP, or have no ERPFreeOrder.
    This is the important part that removes old unmatched Sales Order data.
    """
    payload: Dict[str, Any] = {}

    for tid in ticket_ids:
        tid_key = sanitize_fb_key(tid)
        if not tid_key:
            continue

        base = f"tickets/{tid_key}/ticket"
        payload[f"{base}/Sales Order"] = ""
        payload[f"{base}/SO Created Date"] = ""
        payload[f"{base}/First Issue Date"] = ""
        payload[f"{base}/Complete Issue Date"] = ""
        payload[f"{base}/Issue Status"] = reason
        payload[f"{base}/Order Rejection Status"] = reason
        payload[f"{base}/Sales Order Details"] = []
        payload[f"{base}/soLastSyncAt"] = SERVER_TIMESTAMP

    return payload




def build_roles_payload(new_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Upload C4C involved-party roles to Firebase.

    The C4C API returns different rows depending on CCSRQ_DPY_ROLE_CD.
    Role 40 = Assign To, and its InvolvedPartyName is the internal employee
    owner used by Employee Workbench. Older versions only uploaded flat ticket
    fields, so Firebase could miss /tickets/{id}/roles/40 and employee analytics
    became unmapped.
    """
    payload: Dict[str, Any] = {}
    for ticket_id_raw, node in (new_snapshot or {}).items():
        ticket_id = sanitize_fb_key(ticket_id_raw)
        if not ticket_id:
            continue
        roles = (node or {}).get("roles", {})
        if not isinstance(roles, dict):
            continue
        for role_code, role_data in roles.items():
            role_key = sanitize_fb_key(role_code)
            if not role_key:
                continue
            payload[f"tickets/{ticket_id}/roles/{role_key}"] = canonical_for_hash(role_data or {})
    return payload


def upload_roles_to_firebase(new_snapshot: Dict[str, Any]):
    payload_all = build_roles_payload(new_snapshot)
    upload_payload_in_batches(FIREBASE_ROOT, payload_all, label="FB C4C ROLES UPLOAD")
    if payload_all:
        db.reference(FIREBASE_ROOT).update({"ticketRolesSyncAt": iso_utc_now()})

def build_snapshot_hash_payload(changed_hash_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for tid, node in changed_hash_payload.items():
        tid_key = sanitize_fb_key(tid)
        if tid_key:
            payload[f"ticketSyncSnapshot/{tid_key}"] = node
    return payload


def upload_payload_in_batches(path: str, payload_all: Dict[str, Any], label: str = "FB UPDATE"):
    if not payload_all:
        logger.info("[%s] nothing to upload", label)
        return

    updates_batch: Dict[str, Any] = {}
    batch_no = 0

    def flush_batch():
        nonlocal updates_batch, batch_no
        if not updates_batch:
            return
        batch_no += 1
        logger.info(
            "[%s] batch=%s paths=%s bytes~=%s",
            label, batch_no, len(updates_batch), _rough_bytes(updates_batch)
        )
        fb_update_with_retry(path, updates_batch)
        logger.info("[%s] batch=%s done", label, batch_no)
        updates_batch = {}

    for k, v in payload_all.items():
        updates_batch[k] = v
        if (
            len(updates_batch) >= MAX_PATHS_PER_UPDATE
            or _rough_bytes(updates_batch) >= MAX_BYTES_PER_UPDATE
        ):
            flush_batch()

    flush_batch()


# =================== Firebase å†™ ticket å­—æ®µ ===================
def build_material_price_map(final_df: pd.DataFrame) -> Dict[str, Dict[str, Dict[str, object]]]:
    if final_df.empty or "Material" not in final_df.columns:
        return {}
    materials = sorted({
        as_clean_str(m)
        for m in final_df["Material"].tolist()
        if as_clean_str(m)
    })
    if not materials:
        return {}
    try:
        logger.info("Fetching SAP material prices for %s unique materials", len(materials))
        return fetch_material_price_map(materials)
    except Exception as exc:
        logger.warning("SAP material price fetch failed; continuing with blank price fields: %s", exc)
        return {}


def build_ticket_fields_payload(
    final_df: pd.DataFrame,
    material_price_map: Optional[Dict[str, Dict[str, Dict[str, object]]]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}

    if final_df.empty:
        return payload

    work = final_df.copy()

    text_cols = [
        "TicketID", "Sales Order", "SO Created Date", "Issue Status",
        "Sales Order Item", "Material", "Description", "Order Qty",
        "Sales Unit", "First Issue Date", "Rejection Reason", "Item Rejection Status",
        "Order Rejection Status", "Reservation_Created_By", "Reservation_Cost_Center"
    ]
    for col in text_cols:
        if col in work.columns:
            work[col] = work[col].fillna("").astype(str).str.strip()

    if "Delivery Count" in work.columns:
        work["Delivery Count"] = pd.to_numeric(work["Delivery Count"], errors="coerce").fillna(0).astype(int)

    blank_so_mask = work["Sales Order"] == ""
    work.loc[blank_so_mask, "Issue Status"] = "Not Found"
    work.loc[blank_so_mask, "Order Rejection Status"] = "Not Found"
    if "Item Rejection Status" in work.columns:
        work.loc[blank_so_mask, "Item Rejection Status"] = "Not Found"

    work = dedupe_final_rows(work)

    material_price_map = material_price_map or build_material_price_map(work)

    for ticket_id_raw, grp in work.groupby("TicketID", dropna=False):
        ticket_id = sanitize_fb_key(ticket_id_raw)
        if not ticket_id:
            continue

        grp = grp.sort_values(
            ["Sales Order", "Sales Order Item", "Material", "Description"],
            na_position="last"
        ).reset_index(drop=True)

        first = grp.iloc[0]

        sales_order = as_clean_str(first.get("Sales Order")) or ""
        so_created_date = as_clean_str(first.get("SO Created Date")) or ""
        issue_status = as_clean_str(first.get("Issue Status")) or "Not Found"
        order_rejection_status = as_clean_str(first.get("Order Rejection Status")) or "Not Found"
        purchaser = as_clean_str(first.get("Purchaser")) or ""
        currency = as_clean_str(first.get("Currency")) or ""
        erp_purchase_order = as_clean_str(first.get("ERPPurchaseOrder")) or ""
        reservation_created_by = as_clean_str(first.get("Reservation_Created_By")) or ""
        reservation_cost_center = as_clean_str(first.get("Reservation_Cost_Center")) or ""
        amount_including_tax = first.get("AmountIncludingTax", "")
        net_value = first.get("Net Value", "")

        base = f"tickets/{ticket_id}/ticket"

        payload[f"{base}/Sales Order"] = sales_order
        payload[f"{base}/SO Created Date"] = so_created_date if sales_order else ""
        payload[f"{base}/Purchaser"] = purchaser if sales_order else ""
        payload[f"{base}/Reservation_Created_By"] = reservation_created_by if sales_order else ""
        payload[f"{base}/Reservation_Cost_Center"] = reservation_cost_center if sales_order else ""
        payload[f"{base}/Currency"] = currency if sales_order else ""
        payload[f"{base}/ERPPurchaseOrder"] = erp_purchase_order if sales_order else ""
        payload[f"{base}/AmountIncludingTax"] = amount_including_tax if sales_order else ""
        payload[f"{base}/Net Value"] = net_value if sales_order else ""
        payload[f"{base}/First Issue Date"] = ""
        payload[f"{base}/Complete Issue Date"] = ""
        payload[f"{base}/Issue Status"] = "Not Found" if not sales_order else issue_status
        payload[f"{base}/Order Rejection Status"] = (
            "Not Found" if not sales_order else order_rejection_status
        )

        if not sales_order:
            payload[f"{base}/Sales Order Details"] = []
            payload[f"{base}/soLastSyncAt"] = SERVER_TIMESTAMP
            continue

        detail_cols = [
            "Sales Order Item",
            "Material",
            "Description",
            "Order Qty",
            "Sales Unit",
            "Purchaser",
            "Reservation_Created_By",
            "Reservation_Cost_Center",
            "Currency",
            "Net Value",
            "ERPPurchaseOrder",
            "AmountIncludingTax",
            "Delivery Count",
            "First Issue Date",
            "Rejection Reason",
            "Item Rejection Status",
        ]
        for col in detail_cols:
            if col not in grp.columns:
                grp[col] = ""

        detail_rows = grp[detail_cols].copy()

        detail_rows["Delivery Count"] = pd.to_numeric(
            detail_rows["Delivery Count"], errors="coerce"
        ).fillna(0).astype(int)

        # Critical fix:
        # If SAP item has VBAP.ABGRU / Rejection Reason, it must NOT appear in Parts Delivery.
        # Example: SAP shows "Assigned by the System", while C4C ticket status is still Repair in Progress.
        # The old webpage was showing ticket status, but item-level SAP rejection should win here.
        rejection_text = detail_rows["Rejection Reason"].fillna("").astype(str).str.strip()
        item_rej = detail_rows["Item Rejection Status"].fillna("").astype(str).str.strip().str.lower()
        detail_rows = detail_rows[(rejection_text == "") & (item_rej != "rejected")].copy()

        detail_rows = detail_rows.drop_duplicates(
            subset=[
                "Sales Order Item",
                "Material",
                "Description",
                "Order Qty",
                "Sales Unit",
                "Delivery Count",
                "Rejection Reason",
                "Item Rejection Status",
            ]
        ).reset_index(drop=True)

        details = []
        for _, row in detail_rows.iterrows():
            details.append({
                "Sales Order Item": row.get("Sales Order Item", ""),
                "Material": row.get("Material", ""),
                "Description": row.get("Description", ""),
                "Order Qty": row.get("Order Qty", ""),
                "Sales Unit": row.get("Sales Unit", ""),
                "Purchaser": row.get("Purchaser", ""),
                "Reservation_Created_By": row.get("Reservation_Created_By", ""),
                "Reservation_Cost_Center": row.get("Reservation_Cost_Center", ""),
                "Currency": row.get("Currency", ""),
                "Net Value": row.get("Net Value", ""),
                "ERPPurchaseOrder": row.get("ERPPurchaseOrder", erp_purchase_order),
                "AmountIncludingTax": row.get("AmountIncludingTax", amount_including_tax),
                "Delivery Count": int(row.get("Delivery Count", 0) or 0),
                "First Issue Date": row.get("First Issue Date", ""),
                "Rejection Reason": row.get("Rejection Reason", ""),
                "Item Rejection Status": row.get("Item Rejection Status", ""),
            })

        enrich_detail_rows(details, material_price_map)

        payload[f"{base}/Sales Order Details"] = details
        first_issue_dates = [
            as_clean_str(v.get("First Issue Date"))
            for v in details
            if as_clean_str(v.get("First Issue Date"))
        ]
        payload[f"{base}/First Issue Date"] = min(first_issue_dates) if first_issue_dates else ""
        payload[f"{base}/Complete Issue Date"] = max(first_issue_dates) if first_issue_dates else ""
        payload[f"{base}/soLastSyncAt"] = SERVER_TIMESTAMP

    return payload

def upload_ticket_fields_to_firebase(
    final_df: pd.DataFrame,
    material_price_map: Optional[Dict[str, Dict[str, Dict[str, object]]]] = None,
):
    payload_all = build_ticket_fields_payload(final_df, material_price_map=material_price_map)
    upload_payload_in_batches(FIREBASE_ROOT, payload_all, label="FB SO UPLOAD")

    if payload_all:
        now_iso = iso_utc_now()
        db.reference(FIREBASE_ROOT).update({"ticketSoSyncAt": now_iso})


def is_nishi_creator(name: str) -> bool:
    normalized = as_clean_str(name).lower()
    if not normalized:
        return False
    return "nishi" in normalized or normalized == "e03" or normalized.endswith("e03")


def choose_preferred_meta_value(values: List[str], prefer_nishi: bool = False) -> str:
    cleaned = [as_clean_str(v) for v in values if as_clean_str(v)]
    if not cleaned:
        return ""

    counts = Counter(cleaned)
    if prefer_nishi:
        nishi_values = [value for value in counts if is_nishi_creator(value)]
        if nishi_values:
            return max(nishi_values, key=lambda value: (counts[value], value.lower()))

    return max(counts, key=lambda value: (counts[value], value.lower()))


def fetch_reservation_meta_map_from_hana(sales_orders: List[str]) -> Dict[str, Dict[str, str]]:
    clean_sales_orders = sorted({
        as_clean_str(so)
        for so in (sales_orders or [])
        if as_clean_str(so)
    })
    if not clean_sales_orders:
        return {}

    collected: Dict[str, Dict[str, List[str]]] = {}
    batches = safe_chunks(clean_sales_orders, 200)

    with pyodbc.connect(SAP_HANA_DSN, autocommit=True) as conn:
        for i, batch in enumerate(batches, start=1):
            values_sql = "\nUNION ALL\n".join(
                [f"SELECT '{sql_quote(so)}' AS \"SalesOrder\" FROM DUMMY" for so in batch]
            )
            sql = f"""
WITH input_so AS (
{values_sql}
),
so_orders AS (
    SELECT DISTINCT
        afpo."KDAUF" AS "SalesOrder",
        afpo."AUFNR" AS "Order_Number"
    FROM "SAPHANADB"."AFPO" afpo
    INNER JOIN input_so iso
        ON iso."SalesOrder" = afpo."KDAUF"
    WHERE afpo."MANDT" = '{sql_quote(SAP_CLIENT)}'
      AND COALESCE(afpo."KDAUF", '') <> ''
      AND COALESCE(afpo."AUFNR", '') <> ''
),
reservation_rows AS (
    SELECT DISTINCT
        so_orders."SalesOrder" AS "SalesOrder",
        so_orders."Order_Number" AS "Order_Number",
        resb."RSNUM" AS "Reservation_Number",
        rkpf."USNAM" AS "Reservation_Created_By",
        rkpf."KOSTL" AS "Reservation_Cost_Center"
    FROM so_orders
    INNER JOIN "SAPHANADB"."RESB" resb
        ON resb."MANDT" = '{sql_quote(SAP_CLIENT)}'
       AND resb."AUFNR" = so_orders."Order_Number"
    LEFT JOIN "SAPHANADB"."RKPF" rkpf
        ON rkpf."MANDT" = resb."MANDT"
       AND rkpf."RSNUM" = resb."RSNUM"
)
SELECT
    "SalesOrder",
    "Order_Number",
    "Reservation_Number",
    "Reservation_Created_By",
    "Reservation_Cost_Center"
FROM reservation_rows
"""
            logger.info(
                "Fetching RKPF reservation metadata batch %s/%s with %s SOs",
                i,
                len(batches),
                len(batch),
            )
            part = pd.read_sql(sql, conn)
            for _, row in part.iterrows():
                sales_order = as_clean_str(row.get("SalesOrder"))
                if not sales_order:
                    continue
                bucket = collected.setdefault(
                    sales_order,
                    {
                        "Reservation_Created_By": [],
                        "Reservation_Cost_Center": [],
                        "Order_Number": [],
                        "Reservation_Number": [],
                    },
                )
                creator = as_clean_str(row.get("Reservation_Created_By"))
                cost_center = as_clean_str(row.get("Reservation_Cost_Center"))
                order_number = as_clean_str(row.get("Order_Number"))
                reservation_number = as_clean_str(row.get("Reservation_Number"))
                if creator:
                    bucket["Reservation_Created_By"].append(creator)
                if cost_center:
                    bucket["Reservation_Cost_Center"].append(cost_center)
                if order_number:
                    bucket["Order_Number"].append(order_number)
                if reservation_number:
                    bucket["Reservation_Number"].append(reservation_number)

    result: Dict[str, Dict[str, str]] = {}
    for sales_order, bucket in collected.items():
        result[sales_order] = {
            "Reservation_Created_By": choose_preferred_meta_value(bucket["Reservation_Created_By"], prefer_nishi=True),
            "Reservation_Cost_Center": choose_preferred_meta_value(bucket["Reservation_Cost_Center"]),
            "Order_Number": choose_preferred_meta_value(bucket["Order_Number"]),
            "Reservation_Number": choose_preferred_meta_value(bucket["Reservation_Number"]),
        }
    return result


def fetch_po_cost_map_from_hana(po_ids: List[str]) -> Dict[str, float]:
    """
    Return PO net values for active PO items only.

    We intentionally drop header/item rows that look deleted in SAP so the
    Nishi comparison does not count cancelled POs or already-deleted items.
    """
    clean_po_ids = sorted({
        as_clean_str(po_id)
        for po_id in (po_ids or [])
        if as_clean_str(po_id) and as_clean_str(po_id) != "#"
    })
    if not clean_po_ids:
        return {}

    cost_map: Dict[str, float] = {}
    batches = safe_chunks(clean_po_ids, 300)
    with pyodbc.connect(SAP_HANA_DSN, autocommit=True) as conn:
        for i, batch in enumerate(batches, start=1):
            values_sql = "\nUNION ALL\n".join(
                [f"SELECT '{sql_quote(po)}' AS \"EBELN\" FROM DUMMY" for po in batch]
            )
            sql = f"""
WITH input_po AS (
{values_sql}
),
po_items AS (
    SELECT
        ekpo."EBELN" AS "EBELN",
        ekpo."EBELP" AS "EBELP",
        MAX(COALESCE(ekpo."NETWR", 0)) AS "NETWR",
        MAX(
            CASE
                WHEN COALESCE(ekko."LOEKZ", '') <> '' THEN 0
                WHEN COALESCE(ekpo."LOEKZ", '') <> '' THEN 0
                ELSE 1
            END
        ) AS "IsActive"
    FROM "SAPHANADB"."EKPO" ekpo
    INNER JOIN input_po ip
        ON ip."EBELN" = ekpo."EBELN"
    LEFT JOIN "SAPHANADB"."EKKO" ekko
        ON ekko."MANDT" = ekpo."MANDT"
       AND ekko."EBELN" = ekpo."EBELN"
    GROUP BY
        ekpo."EBELN",
        ekpo."EBELP"
)
SELECT
    "EBELN",
    SUM(CASE WHEN "IsActive" = 1 THEN "NETWR" ELSE 0 END) AS "PO Net Value"
FROM po_items
GROUP BY "EBELN"
HAVING SUM(CASE WHEN "IsActive" = 1 THEN 1 ELSE 0 END) > 0
"""
            logger.info("Fetching EKPO cost batch %s/%s with %s POs", i, len(batches), len(batch))
            part = pd.read_sql(sql, conn)
            for _, row in part.iterrows():
                po = as_clean_str(row.get("EBELN"))
                if not po:
                    continue
                cost_map[po] = float(row.get("PO Net Value", 0) or 0)
    return cost_map


def build_hana_cost_sidecar(
    final_df: pd.DataFrame,
    as_of_date: date,
    material_price_map: Optional[Dict[str, Dict[str, Dict[str, object]]]] = None,
) -> Optional[Dict[str, Any]]:
    if final_df.empty:
        return None

    work = final_df.copy()
    if "SO Created Date" not in work.columns:
        return None

    work["__so_date"] = pd.to_datetime(work["SO Created Date"], errors="coerce").dt.date
    work = work[work["__so_date"].notna()].copy()
    if work.empty:
        return None

    period_start = date(2025, 1, 1)
    work = work[(work["__so_date"] >= period_start) & (work["__so_date"] <= as_of_date)].copy()
    if work.empty:
        return None

    # Total Parts Cost intentionally covers ALL purchasers, not just Nishi.
    # An earlier attempt to filter on work["Purchaser"] (= VBAK.ERNAM) emptied
    # the frame because warranty SOs are system-generated by PS4CRM, so no row
    # matched is_nishi_creator. That collapsed the sidecar to None and the
    # aggregator fell back to AmountIncludingTax (finance number), which is
    # exactly the bug we are here to fix. If we ever want a Nishi-only card,
    # the detection needs the aggregator-side signals (Reservation_Created_By,
    # Reservation_Cost_Center, ERPPurchaseOrder E03 marker), not ERNAM.

    material_price_map = material_price_map or build_material_price_map(work)

    so_groups: List[Dict[str, Any]] = []
    for sales_order, grp in work.groupby("Sales Order", dropna=False):
        sales_order = as_clean_str(sales_order)
        if not sales_order:
            continue

        grp = grp.sort_values(["TicketID", "Sales Order Item"], na_position="last").copy()
        first = grp.iloc[0]
        so_date = first.get("__so_date")
        if not so_date:
            continue

        preferred_total = 0.0
        po_total = 0.0
        fallback_total = 0.0
        po_hit_count = 0
        fallback_count = 0
        material_rows = 0

        for _, row in grp.iterrows():
            breakdown = preferred_line_cost_aud(row.to_dict(), material_price_map)
            line_cost = breakdown.get("lineCostAud", "")
            if line_cost == "":
                continue
            line_cost = float(line_cost or 0)
            preferred_total += line_cost
            material_rows += 1
            if breakdown.get("source") == "3110 PO":
                po_total += line_cost
                po_hit_count += 1
            elif breakdown.get("source") == "3090 SO":
                fallback_total += line_cost
                fallback_count += 1

        so_groups.append({
            "ticketKey": as_clean_str(first.get("TicketID")) or sales_order,
            "ticketId": as_clean_str(first.get("TicketID")) or sales_order,
            "salesOrder": sales_order,
            "soCreatedDate": so_date.isoformat(),
            "preferredCost": round(preferred_total, 2),
            "poCost": round(po_total, 2),
            "fallbackCost": round(fallback_total, 2),
            "poHitCount": po_hit_count,
            "fallbackCount": fallback_count,
            "materialRows": material_rows,
        })

    if not so_groups:
        return None

    so_groups.sort(key=lambda row: (row["soCreatedDate"], row["salesOrder"]))
    latest_date = max(date.fromisoformat(row["soCreatedDate"]) for row in so_groups)
    ytd_year = latest_date.year
    period_rows = so_groups
    if not period_rows:
        return None

    month_map: Dict[str, Dict[str, Any]] = {}
    total_cost = 0.0
    total_po_cost = 0.0
    total_fallback_cost = 0.0
    total_so_set = set()
    total_material_rows = 0
    po_hit_count_total = 0
    fallback_count_total = 0

    for row in period_rows:
        row_date = date.fromisoformat(row["soCreatedDate"])
        month_start = date(row_date.year, row_date.month, 1)
        month_key = month_start.isoformat()
        month = month_map.setdefault(month_key, {
            "key": month_key,
            "label": month_start.strftime("%b %Y"),
            "cost": 0.0,
            "poCost": 0.0,
            "fallbackCost": 0.0,
            "soSet": set(),
            "poHitCount": 0,
            "fallbackCount": 0,
            "materialRows": 0,
        })

        month["cost"] += row["preferredCost"]
        month["poCost"] += row["poCost"]
        month["fallbackCost"] += row["fallbackCost"]
        total_cost += row["preferredCost"]
        total_po_cost += row["poCost"]
        total_fallback_cost += row["fallbackCost"]
        total_material_rows += row["materialRows"]
        po_hit_count_total += row["poHitCount"]
        fallback_count_total += row["fallbackCount"]
        if row["salesOrder"]:
            month["soSet"].add(row["salesOrder"])
            total_so_set.add(row["salesOrder"])
        month["poHitCount"] += row["poHitCount"]
        month["fallbackCount"] += row["fallbackCount"]
        month["materialRows"] += row["materialRows"]

    months: List[Dict[str, Any]] = []
    for month in sorted(month_map.values(), key=lambda item: item["key"]):
        so_count = len(month["soSet"])
        cost = round(month["cost"], 2)
        months.append({
            "key": month["key"],
            "label": month["label"],
            "cost": cost,
            "poCost": round(month["poCost"], 2),
            "fallbackCost": round(month["fallbackCost"], 2),
            "poHitCount": month["poHitCount"],
            "fallbackCount": month["fallbackCount"],
            "materialRows": month["materialRows"],
            "soCount": so_count,
            "poShare": (month["poCost"] / cost) if cost else 0,
            "fallbackShare": (month["fallbackCost"] / cost) if cost else 0,
            "avgPerSo": round(cost / so_count, 2) if so_count else 0,
        })

    by_month_cost = {item["key"]: item["cost"] for item in months}
    by_month_po = {item["key"]: item["poCost"] for item in months}
    by_month_fallback = {item["key"]: item["fallbackCost"] for item in months}
    by_month_po_hit = {item["key"]: item["poHitCount"] for item in months}
    by_month_fallback_count = {item["key"]: item["fallbackCount"] for item in months}
    by_month_material_rows = {item["key"]: item["materialRows"] for item in months}
    by_month_so_count = {item["key"]: item["soCount"] for item in months}

    cost_report = {
        "periodStart": period_start.isoformat(),
        "periodEnd": latest_date.isoformat(),
        "periodLabel": f"{period_start.isoformat()} to {latest_date.isoformat()}",
        "ytdYear": period_start.year,
        "latestDate": latest_date.isoformat(),
        "totalCost": round(total_cost, 2),
        "poTotalCost": round(total_po_cost, 2),
        # Backward-compatible alias used by the existing dashboard and logs.
        "purchaserTotalCost": round(total_po_cost, 2),
        "fallbackTotalCost": round(total_fallback_cost, 2),
        "poHitCount": po_hit_count_total,
        "fallbackCount": fallback_count_total,
        "materialRows": total_material_rows,
        "totalSoCount": len(total_so_set),
        "months": months,
        "avgCostPerSo": round(total_cost / len(total_so_set), 2) if total_so_set else 0,
        "source": {
            "name": "SAP HANA",
            "sourceType": "3110 PO first, 3090 SO fallback",
            "preferredFields": ["3110 PO Net Price", "3090 SO Net Price"],
            "quantityField": "Order Qty",
            "currencyNormalization": f"CNY -> AUD @ {int(CNY_TO_AUD_RATE)}",
            "periodStart": period_start.isoformat(),
        },
    }

    return {
        "asOf": as_of_date.isoformat(),
        "generatedAt": iso_utc_now(),
        "partsCost": {
            "total": round(total_cost, 2),
            "byMonth": by_month_cost,
            "byMonthPo": by_month_po,
            "byMonthFallback": by_month_fallback,
            "poHitCountByMonth": by_month_po_hit,
            "fallbackCountByMonth": by_month_fallback_count,
            "materialRowsByMonth": by_month_material_rows,
            "soCountByMonth": by_month_so_count,
            "poCost": round(total_po_cost, 2),
            "fallbackCost": round(total_fallback_cost, 2),
            "poHitCount": po_hit_count_total,
            "fallbackCount": fallback_count_total,
            "materialRows": total_material_rows,
            "avgPerSO": round(total_cost / len(total_so_set), 2) if total_so_set else 0,
            "soCount": len(total_so_set),
        },
        "costReport": cost_report,
    }


def write_hana_cost_sidecar(report_payload: Dict[str, Any]) -> None:
    day_key = as_clean_str(report_payload.get("asOf")) or iso_utc_now()[:10]
    base = f"{MONITOR_ROOT}/analytics/deliveryFlow/hanaCostYtd"
    ref = db.reference(f"{base}/daily/{day_key}")
    ref.set(report_payload)
    db.reference(f"{base}/latest").set(report_payload)
    logger.info(
        "Wrote HANA cost sidecar for %s (totalCost=%s, poTotal=%s, fallbackTotal=%s)",
        report_payload["asOf"],
        report_payload.get("costReport", {}).get("totalCost", 0),
        report_payload.get("costReport", {}).get("poTotalCost", 0),
        report_payload.get("costReport", {}).get("fallbackTotalCost", 0),
    )


def extract_approved_cost_ticket_number(short_text: Any) -> Tuple[str, str]:
    text = as_clean_str(short_text)
    if not text:
        return "", "Short Text is blank"

    matches: List[str] = []
    matches.extend(APPROVED_COST_TICKET_NO_PATTERN.findall(text))
    matches.extend(APPROVED_COST_TICKET_BRACKET_PATTERN.findall(text))
    matches.extend(APPROVED_COST_TICKET_OUTER_BRACKET_PATTERN.findall(text))

    unique: List[str] = []
    seen = set()
    for match in matches:
        ticket_id = as_clean_str(match)
        if re.fullmatch(r"\d+\.0+", ticket_id):
            ticket_id = ticket_id.split(".", 1)[0]
        if ticket_id and ticket_id not in seen:
            unique.append(ticket_id)
            seen.add(ticket_id)

    if len(unique) == 1:
        return unique[0], ""
    if len(unique) > 1:
        return "", "Multiple ticket numbers found: " + ", ".join(unique)
    if re.search(r"\btickets?\b", text, flags=re.IGNORECASE):
        return "", "Contains Ticket word but no standard Ticket No. number or Ticket [number] pattern"
    return "", "No standard Ticket No. number or Ticket [number] pattern"


def build_approved_cost_sap_po_short_text_sidecar(conn) -> Dict[str, Any]:
    """
    Approved Cost price source of truth.

    Do not calculate Approved Cost from C4C AmountIncludingTax here. The
    business rule is: C4C decides which tickets are approved; SAP PO Short Text
    (EKPO.TXZ01) gives the ticket number; EKPO.NETWR gives the approved cost.
    """
    where_parts = [
        f'h."MANDT" = \'{sql_quote(SAP_CLIENT)}\'',
        f'h."EKORG" = \'{sql_quote(APPROVED_COST_PO_PURCHASING_ORG)}\'',
        f'h."EKGRP" = \'{sql_quote(APPROVED_COST_PO_PURCHASING_GROUP)}\'',
    ]
    if APPROVED_COST_PO_PLANT_FILTER:
        where_parts.append(f'p."WERKS" = \'{sql_quote(APPROVED_COST_PO_PLANT_FILTER)}\'')
    if APPROVED_COST_EXCLUDE_DELETED_PO:
        where_parts.append("COALESCE(h.\"LOEKZ\", '') = ''")
        where_parts.append("COALESCE(p.\"LOEKZ\", '') = ''")

    sql = f"""
SELECT
    p."EBELN" AS "Purchasing Document",
    p."EBELP" AS "Item",
    h."EKORG" AS "Purchasing Organization",
    h."EKGRP" AS "Purchasing Group",
    p."WERKS" AS "Plant",
    h."BEDAT" AS "Document Date",
    h."AEDAT" AS "Changed On",
    h."ERNAM" AS "Created By",
    h."LIFNR" AS "Supplier",
    p."MATNR" AS "Material",
    p."TXZ01" AS "Short Text",
    p."MENGE" AS "Order Quantity",
    p."MEINS" AS "Order Unit",
    p."NETPR" AS "Net Price",
    p."PEINH" AS "Price Unit",
    p."NETWR" AS "Net Order Value",
    h."WAERS" AS "Currency",
    h."LOEKZ" AS "Header Deletion Indicator",
    p."LOEKZ" AS "Item Deletion Indicator"
FROM "SAPHANADB"."EKPO" p
INNER JOIN "SAPHANADB"."EKKO" h
    ON h."MANDT" = p."MANDT"
   AND h."EBELN" = p."EBELN"
WHERE {" AND ".join(where_parts)}
ORDER BY p."EBELN", p."EBELP"
"""
    logger.info(
        "Fetching Approved Cost SAP PO Short Text rows: EKORG=%s EKGRP=%s Plant=%s ExcludeDeleted=%s",
        APPROVED_COST_PO_PURCHASING_ORG,
        APPROVED_COST_PO_PURCHASING_GROUP,
        APPROVED_COST_PO_PLANT_FILTER or "(blank)",
        APPROVED_COST_EXCLUDE_DELETED_PO,
    )
    po_df = pd.read_sql(sql, conn)
    logger.info("Approved Cost SAP PO rows fetched: %s", len(po_df))

    by_ticket: Dict[str, Dict[str, Any]] = {}
    exception_count = 0
    regular_count = 0
    total_amount = 0.0

    for _, row in po_df.iterrows():
        ticket_id, reason = extract_approved_cost_ticket_number(row.get("Short Text"))
        if not ticket_id:
            exception_count += 1
            continue

        regular_count += 1
        amount_raw = pd.to_numeric(row.get("Net Order Value"), errors="coerce")
        net_price_raw = pd.to_numeric(row.get("Net Price"), errors="coerce")
        amount = 0.0 if pd.isna(amount_raw) else float(amount_raw)
        net_price = 0.0 if pd.isna(net_price_raw) else float(net_price_raw)
        total_amount += amount
        storage_key = f"ticket_{ticket_id}"
        bucket = by_ticket.setdefault(
            storage_key,
            {
                "ticketNumber": ticket_id,
                "amount": 0.0,
                "netOrderValue": 0.0,
                "netPriceSum": 0.0,
                "poItemCount": 0,
                "poDocuments": [],
                "currencies": [],
                "shortTextSamples": [],
            },
        )
        bucket["amount"] = round(float(bucket["amount"]) + amount, 2)
        bucket["netOrderValue"] = round(float(bucket["netOrderValue"]) + amount, 2)
        bucket["netPriceSum"] = round(float(bucket["netPriceSum"]) + net_price, 2)
        bucket["poItemCount"] = int(bucket["poItemCount"]) + 1

        po = as_clean_str(row.get("Purchasing Document"))
        currency = as_clean_str(row.get("Currency"))
        short_text = as_clean_str(row.get("Short Text"))
        if po and po not in bucket["poDocuments"]:
            bucket["poDocuments"].append(po)
        if currency and currency not in bucket["currencies"]:
            bucket["currencies"].append(currency)
        if short_text and len(bucket["shortTextSamples"]) < 5 and short_text not in bucket["shortTextSamples"]:
            bucket["shortTextSamples"].append(short_text)

    return {
        "generatedAt": iso_utc_now(),
        "source": {
            "name": "SAP HANA PO Short Text",
            "rule": "Approved Cost = sum EKPO.NETWR for PO items whose EKPO.TXZ01 contains standard Ticket No. / Ticket [number]. C4C is used only to decide approved ticket membership.",
            "amountField": "EKPO.NETWR",
            "shortTextField": "EKPO.TXZ01",
            "sapClient": SAP_CLIENT,
            "purchasingOrganization": APPROVED_COST_PO_PURCHASING_ORG,
            "purchasingGroup": APPROVED_COST_PO_PURCHASING_GROUP,
            "plantFilter": APPROVED_COST_PO_PLANT_FILTER,
            "excludeDeletedPo": APPROVED_COST_EXCLUDE_DELETED_PO,
        },
        "byTicket": by_ticket,
        "summary": {
            "ticketCount": len(by_ticket),
            "poRows": int(len(po_df)),
            "regularRows": regular_count,
            "exceptionRows": exception_count,
            "totalAmount": round(total_amount, 2),
        },
    }


def write_approved_cost_sidecar(report_payload: Dict[str, Any]) -> None:
    day_key = iso_utc_now()[:10]
    base = f"{MONITOR_ROOT}/analytics/approvedCost/sapPoShortText"
    db.reference(f"{base}/daily/{day_key}").set(report_payload)
    db.reference(f"{base}/latest").set(report_payload)
    logger.info(
        "Wrote Approved Cost SAP PO Short Text sidecar (tickets=%s, total=%s)",
        report_payload.get("summary", {}).get("ticketCount", 0),
        report_payload.get("summary", {}).get("totalAmount", 0),
    )


# =================== Firebase å†™ C4C æ ¸å¿ƒ ticket å­—æ®µ ===================
def build_changed_ticket_core_payload(changed_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """
    Important fix:
    The old script detected changed tickets by hash, but it only uploaded Sales Order/SO fields.
    So when C4C TicketStatus changed, Firebase /tickets/{TicketID}/ticket/TicketStatus stayed old.

    This payload updates existing ticket core fields from the latest C4C API snapshot.
    It writes only under FIREBASE_ROOT/tickets/{TicketID}/ticket/{field}; it does not create any child table
    inside the original ticket database.
    """
    payload: Dict[str, Any] = {}

    # Keep this list flat. Do not write roles or nested monitor/history data here.
    CORE_FIELDS_TO_SYNC = [
        "TicketID",
        "TicketName",
        "TicketStatus",
        "TicketStatusText",
        "TicketSeverity",
        "TicketType",
        "TicketTypeText",
        "Responded",
        "ApprovalDate",
        "ApprovalNumber",
        "ERPInvoiceNumber",
        "ERPPurchaseOrder",
        "ERPFreeOrder",
        "AmountIncludingTax",
        "ServiceRequesterEmail",
        "RepairerBusinessNameID",
        "RepairerEmail",
        "RepairerPhoneNumber",
        "RepairerNamePointOfContact",
        "Z1Z8TimeConsumed",
        "CreatedOn",
        "ClaimApprovedOnDateTime",
        "ClaimApprovedOnDate",
        "ClaimApprovedOn",
        "Claim Approved On",
        "ResolvedOnDateTime",
        "ResolvedOnDate",
        "ResolvedOn",
        "Resolved On",
        "ChangeOnDateTime",
        "ChangeOnDate",
        "ChangeOn",
        "ChangedOn",
        "Changed On",
        "ChassisNumber",
        "SerialID",
        "DealerID",
        "DealerName",
        "WarrantyHandlingDealerID",
        # Ticket-level Assigned To may contain Queue Warranty / queue id.
        # It is not the employee owner, but when role 40 is blank it indicates
        # the ticket is waiting in the queue and should be visible in Employee page.
        "Assigned to",
        "Assigned To",
        "AssignedTo",
        "AssignedToName",
        "Assigned To Name",
        "Assignee",
        "AssignedUser",
        "Assigned User",
        "OwnerPartyName",
    ]

    for ticket_id_raw, node in (changed_snapshot or {}).items():
        ticket_id = sanitize_fb_key(ticket_id_raw)
        if not ticket_id:
            continue

        ticket_data = (node or {}).get("ticket", {})
        if not isinstance(ticket_data, dict):
            continue

        base = f"tickets/{ticket_id}/ticket"
        for field in CORE_FIELDS_TO_SYNC:
            if field in ticket_data:
                payload[f"{base}/{field}"] = norm(ticket_data.get(field))

        # Be tolerant to API spelling variations for Assigned To. Store the raw
        # value under its original field name and a normalized local field.
        for k, v in ticket_data.items():
            nk = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if nk in {"assignedto", "assignedtoname", "assignee", "assigneduser", "ownerpartyname"}:
                payload[f"{base}/{k}"] = norm(v)
                payload[f"{base}/AssignedToRaw"] = norm(v)

        # This is our local sync timestamp. It is useful for the webpage/monitor to know the DB was updated.
        payload[f"{base}/updatedAt"] = SERVER_TIMESTAMP

    return payload


def upload_changed_ticket_core_to_firebase(changed_snapshot: Dict[str, Any]):
    payload_all = build_changed_ticket_core_payload(changed_snapshot)
    upload_payload_in_batches(FIREBASE_ROOT, payload_all, label="FB CORE TICKET UPLOAD")

    if payload_all:
        db.reference(FIREBASE_ROOT).update({"ticketCoreSyncAt": iso_utc_now()})


def employee_directory_key(name: str) -> str:
    return " ".join(as_clean_str(name).lower().split())


def seed_employee_directory():
    ref = db.reference(f"{MONITOR_ROOT}/employeeDirectory")
    current = ref.get() or {}
    if not isinstance(current, dict):
        current = {}
    changed = False
    for name in DEFAULT_ACTIVE_EMPLOYEES:
        key = employee_directory_key(name)
        if key and key not in current:
            current[key] = {"name": name, "status": "active"}
            changed = True
    if changed:
        ref.update(current)
        logger.info("Seeded employeeDirectory defaults under %s/employeeDirectory", MONITOR_ROOT)

# =================== Critical ç§»å‡ºè¶‹åŠ¿ ===================
def build_ticket_status_snapshot(new_snapshot: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    status_snapshot: Dict[str, Dict[str, str]] = {}
    for ticket_id, node in new_snapshot.items():
        ticket = (node or {}).get("ticket", {})
        status_snapshot[ticket_id] = {
            "statusCode": as_clean_str(ticket.get("TicketStatus")) or "",
            "statusText": as_clean_str(ticket.get("TicketStatusText")) or "",
            "createdOn": as_clean_str(ticket.get("CreatedOn")) or "",
            "amountIncludingTax": as_clean_str(ticket.get("AmountIncludingTax")) or "",
            "claimApprovedOn": as_clean_str(
                ticket.get("ClaimApprovedOnDateTime")
                or ticket.get("ClaimApprovedOnDate")
                or ticket.get("ClaimApprovedOn")
                or ticket.get("Claim Approved On")
            ) or "",
            "resolvedOn": as_clean_str(
                ticket.get("ResolvedOnDateTime")
                or ticket.get("ResolvedOnDate")
                or ticket.get("ResolvedOn")
                or ticket.get("Resolved On")
            ) or "",
            "changedOn": as_clean_str(
                ticket.get("ChangeOnDateTime")
                or ticket.get("ChangeOnDate")
                or ticket.get("ChangeOn")
                or ticket.get("ChangedOn")
                or ticket.get("Changed On")
            ) or "",
        }
    return status_snapshot


def _status_candidates(code: str, text: str) -> List[str]:
    out: List[str] = []
    if code:
        out.append(code.strip().lower())
    if text:
        out.append(text.strip().lower())
    return out


def is_critical_status(code: str, text: str) -> bool:
    return any(c in CRITICAL_STATUS_VALUES for c in _status_candidates(code, text))


def approval_closed_bucket(code: str, text: str, claim_approved_on: str = "") -> str:
    code_norm = as_clean_str(code).upper()
    text_norm = as_clean_str(text).lower()
    if as_clean_str(claim_approved_on) and (
        code_norm in {"Z9", "Y0", "Y1", "Y2", "Y4", "YB"}
        or text_norm in {
            "sales order approved",
            "partially picked",
            "dispatch parts",
            "repair in progress",
            "repairer invoiced received",
            "repairer invoiced processed",
        }
    ):
        return "approved"
    if code_norm == "Y8" or text_norm in {"unapproved claims closed", "unapproved claims closed (closed)"}:
        return "unapproved"
    return ""


def _first_clean_string(*values: Any) -> str:
    for v in values:
        s = as_clean_str(v)
        if s:
            return s
    return ""


def detect_approval_closed_events(
    prev_snapshot: Dict[str, Any],
    curr_snapshot: Dict[str, Dict[str, str]],
    detected_at_iso: str,
) -> List[Dict[str, str]]:
    events: List[Dict[str, str]] = []
    if not isinstance(prev_snapshot, dict) or not prev_snapshot:
        return events

    for ticket_id, curr in curr_snapshot.items():
        prev = prev_snapshot.get(ticket_id, {}) if isinstance(prev_snapshot, dict) else {}
        if not prev:
            continue
        prev_code = as_clean_str(prev.get("statusCode")) or ""
        prev_text = as_clean_str(prev.get("statusText")) or ""
        curr_code = as_clean_str(curr.get("statusCode")) or ""
        curr_text = as_clean_str(curr.get("statusText")) or ""
        status_changed = (prev_code != curr_code) or (prev_text != curr_text)
        curr_claim_approved_on = as_clean_str(curr.get("claimApprovedOn")) or ""
        prev_claim_approved_on = as_clean_str(prev.get("claimApprovedOn")) or ""
        bucket = approval_closed_bucket(curr_code, curr_text, curr_claim_approved_on)

        if bucket and status_changed and approval_closed_bucket(prev_code, prev_text, prev_claim_approved_on) != bucket:
            if bucket == "approved":
                detected_at = _first_clean_string(curr.get("claimApprovedOn"), curr.get("changedOn"), detected_at_iso)
            else:
                detected_at = _first_clean_string(curr.get("resolvedOn"), curr.get("changedOn"), detected_at_iso)
            events.append({
                "ticketId": ticket_id,
                "bucket": bucket,
                "fromStatusCode": prev_code,
                "fromStatusText": prev_text,
                "toStatusCode": curr_code,
                "toStatusText": curr_text,
                "changedOn": as_clean_str(curr.get("changedOn")) or "",
                "createdOn": as_clean_str(curr.get("createdOn")),
                "amountIncludingTax": as_clean_str(curr.get("amountIncludingTax")),
                "detectedAt": detected_at,
                "timestampSource": "claimApprovedOn" if bucket == "approved" and as_clean_str(curr.get("claimApprovedOn")) else (
                    "resolvedOn" if bucket == "unapproved" and as_clean_str(curr.get("resolvedOn")) else (
                        "changedOn" if as_clean_str(curr.get("changedOn")) else "legacy_detectedAt"
                    )
                ),
            })

    return events


def detect_removed_from_critical_events(
    prev_snapshot: Dict[str, Any],
    curr_snapshot: Dict[str, Dict[str, str]],
    detected_at_iso: str,
) -> List[Dict[str, str]]:
    events: List[Dict[str, str]] = []

    for ticket_id, curr in curr_snapshot.items():
        prev = prev_snapshot.get(ticket_id, {}) if isinstance(prev_snapshot, dict) else {}
        prev_code = as_clean_str(prev.get("statusCode")) or ""
        prev_text = as_clean_str(prev.get("statusText")) or ""
        curr_code = as_clean_str(curr.get("statusCode")) or ""
        curr_text = as_clean_str(curr.get("statusText")) or ""

        prev_is_critical = is_critical_status(prev_code, prev_text)
        curr_is_critical = is_critical_status(curr_code, curr_text)
        status_changed = (prev_code != curr_code) or (prev_text != curr_text)

        if prev_is_critical and (not curr_is_critical) and status_changed:
            detected_at = _first_clean_string(curr.get("changedOn"), detected_at_iso)
            events.append({
                "ticketId": ticket_id,
                "fromStatusCode": prev_code,
                "fromStatusText": prev_text,
                "toStatusCode": curr_code,
                "toStatusText": curr_text,
                "changedOn": as_clean_str(curr.get("changedOn")) or "",
                "detectedAt": detected_at,
                "timestampSource": "changedOn" if as_clean_str(curr.get("changedOn")) else "legacy_detectedAt",
            })

    return events


def upload_critical_removed_metrics(
    new_snapshot: Dict[str, Any],
):
    status_snapshot_ref = db.reference(f"{FIREBASE_ROOT}/ticketStatusSnapshot")
    prev_snapshot = status_snapshot_ref.get() or {}
    curr_snapshot = build_ticket_status_snapshot(new_snapshot)
    now_iso = iso_utc_now()
    day_key = now_iso[:10]

    events = detect_removed_from_critical_events(prev_snapshot, curr_snapshot, now_iso)
    approval_events = detect_approval_closed_events(prev_snapshot, curr_snapshot, now_iso)

    if events:
        updates = {}
        for ev in events:
            ticket_id = sanitize_fb_key(ev["ticketId"])
            updates[f"criticalRemovedHistory/{day_key}/{ticket_id}"] = ev
        fb_update_with_retry(FIREBASE_ROOT, updates)

    if approval_events:
        updates = {}
        for ev in approval_events:
            ticket_id = sanitize_fb_key(ev["ticketId"])
            updates[f"approvalClosedHistory/{day_key}/{ticket_id}"] = ev
        fb_update_with_retry(FIREBASE_ROOT, updates)

    daily_ref = db.reference(f"{FIREBASE_ROOT}/criticalRemovedDaily/{day_key}")
    daily_ref.update({
        "count": len(events),
        "updatedAt": now_iso,
    })

    db.reference(f"{FIREBASE_ROOT}/approvalClosedDaily/{day_key}").update({
        "approved": sum(1 for ev in approval_events if ev.get("bucket") == "approved"),
        "unapproved": sum(1 for ev in approval_events if ev.get("bucket") == "unapproved"),
        "updatedAt": now_iso,
    })

    db.reference(f"{FIREBASE_ROOT}/criticalRemovedLatestSyncAt").set(now_iso)
    db.reference(f"{FIREBASE_ROOT}/approvalClosedLatestSyncAt").set(now_iso)
    status_snapshot_ref.set(curr_snapshot)


# =================== Excel export disabled for automation ===================
def export_excel_single_sheet(final_df: pd.DataFrame):
    """No local Excel export in scheduled runs.

    The automatic refresh must only update Firebase. Writing an .xlsx can block
    the task when Excel is open or Windows asks for permission.
    """
    logger.info("Excel export disabled. Firebase is the source for dashboard data.")


def rebuild_model_series_assets_after_fetch():
    if os.getenv("SKIP_MODEL_SERIES_ASSETS_AFTER_FETCH", "").strip().lower() in {"1", "true", "yes"}:
        logger.info("Model-series asset rebuild skipped after fetch because SKIP_MODEL_SERIES_ASSETS_AFTER_FETCH=1.")
        return

    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rebuild_model_series_assets.py")
    if not os.path.exists(script_path):
        logger.warning("Model-series asset rebuild skipped. Script not found: %s", script_path)
        return

    logger.info("Step 10/11: Rebuilding analysis assets from updated Firebase tickets ...")
    env = os.environ.copy()
    env["FIREBASE_DB_URL"] = FIREBASE_DB_URL
    env["FIREBASE_SA_PATH"] = FIREBASE_SA_PATH
    env["FIREBASE_ROOT"] = FIREBASE_ROOT
    env["MONITOR_ROOT"] = MONITOR_ROOT
    env["SAP_CLIENT"] = SAP_CLIENT
    cmd = [
        sys.executable,
        script_path,
        "--firebase-db-url",
        FIREBASE_DB_URL,
        "--firebase-sa-path",
        FIREBASE_SA_PATH,
        "--firebase-root",
        FIREBASE_ROOT,
        "--monitor-root",
        MONITOR_ROOT,
        "--sap-client",
        SAP_CLIENT,
        "--log-level",
        "INFO",
    ]
    try:
        subprocess.run(cmd, check=True, env=env)
    except subprocess.CalledProcessError as exc:
        logger.exception("Analysis asset rebuild FAILED after fetch. repair, analysis, or dashboard support data may be stale.")
        raise SystemExit(exc.returncode or 1)
    logger.info("Analysis asset rebuild completed. Failure timing, repair, parts, and approved-cost assets are refreshed.")


def rebuild_dashboard_analytics_after_fetch() -> bool:
    if os.getenv("SKIP_ANALYTICS_REBUILD_AFTER_FETCH", "").strip().lower() in {"1", "true", "yes"}:
        logger.info("Analytics rebuild skipped after fetch because SKIP_ANALYTICS_REBUILD_AFTER_FETCH=1.")
        return False

    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ctm_v44_history_safe_mandt800_rejection_filter.py")
    if not os.path.exists(script_path):
        logger.warning("Analytics rebuild skipped. Script not found: %s", script_path)
        return False

    logger.info("Step 11/11: Rebuilding dashboard history and analytics from updated Firebase tickets ...")
    env = os.environ.copy()
    env["FIREBASE_DB_URL"] = FIREBASE_DB_URL
    env["FIREBASE_SA_PATH"] = FIREBASE_SA_PATH
    env["FIREBASE_ROOT"] = FIREBASE_ROOT
    env["SOURCE_ROOT"] = FIREBASE_ROOT
    env["MONITOR_ROOT"] = MONITOR_ROOT
    cmd = [
        sys.executable,
        script_path,
        "--once",
        "--skip-fetch",
        "--source-root",
        FIREBASE_ROOT,
        "--monitor-root",
        MONITOR_ROOT,
        "--firebase-db-url",
        FIREBASE_DB_URL,
        "--firebase-sa-path",
        FIREBASE_SA_PATH,
    ]
    try:
        subprocess.run(cmd, check=True, env=env)
    except subprocess.CalledProcessError as exc:
        logger.exception("Analytics rebuild FAILED after fetch. Dashboard trend data may still be stale.")
        raise SystemExit(exc.returncode or 1)
    logger.info("Analytics rebuild completed. Dashboard trend data is refreshed.")
    return True


def rebuild_ticket_timeline_export_after_fetch() -> None:
    if os.getenv("SKIP_TICKET_TIMELINE_EXPORT_AFTER_FETCH", "").strip().lower() in {"1", "true", "yes"}:
        logger.info("Ticket Timeline export skipped after fetch because SKIP_TICKET_TIMELINE_EXPORT_AFTER_FETCH=1.")
        return

    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export_ticket_timeline_segments_2025_2026.py")
    if not os.path.exists(script_path):
        logger.warning("Ticket Timeline export skipped. Script not found: %s", script_path)
        return

    logger.info("Refreshing Ticket Timeline workbook, JSON, and price-mix PPT after fetch ...")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        subprocess.run([sys.executable, script_path], check=True, env=env)
    except subprocess.CalledProcessError as exc:
        logger.exception("Ticket Timeline export FAILED after fetch. Ticket Timeline page may be stale.")
        raise SystemExit(exc.returncode or 1)
    logger.info("Ticket Timeline workbook, JSON, and price-mix PPT refreshed.")


# =================== Main ===================
def main():
    if not USERNAME or not PASSWORD:
        raise SystemExit("è¯·å…ˆè®¾ç½® C4C_USERNAME / C4C_PASSWORD")
    if "YOUR_USER" in SAP_HANA_DSN or "YOUR_PASSWORD" in SAP_HANA_DSN:
        raise SystemExit("è¯·å…ˆè®¾ç½® SAP_HANA_DSN")

    total_started = time.time()

    logger.info("Script version: %s", SCRIPT_VERSION)
    logger.info("SAP HANA rule: MANDT=%s full join + exclude VBAP.ABGRU rejected items from Sales Order Details", SAP_CLIENT)
    logger.info("Step 1/9: Fetching latest C4C snapshot ...")
    new_snapshot, total_rows = build_new_snapshot()

    logger.info("C4C total API rows processed: %s", total_rows)
    logger.info("C4C total unique TicketIDs: %s", len(new_snapshot))

    logger.info("Step 2/9: Initializing Firebase and loading previous sync snapshot ...")
    firebase_init()
    seed_employee_directory()
    old_hashes = load_old_ticket_hashes()
    logger.info("Previous synced TicketIDs in hash snapshot: %s", len(old_hashes))

    logger.info("Step 3/9: Detecting changed/deleted tickets ...")
    changed_snapshot, changed_hash_payload = get_changed_tickets(new_snapshot, old_hashes)
    deleted_tickets = get_deleted_tickets(new_snapshot, old_hashes)

    logger.info("Changed tickets: %s", len(changed_snapshot))
    logger.info("Deleted tickets: %s", len(deleted_tickets))

    if changed_snapshot:
        logger.info("Step 3A/9: Uploading changed C4C core ticket fields to Firebase ...")
        # Critical fix: keep Firebase ticket status in sync before SO/HANA logic.
        # Without this, TicketStatus can change in C4C but remain old in Firebase.
        upload_changed_ticket_core_to_firebase(changed_snapshot)

    logger.info("Step 3B/9: Uploading C4C involved-party roles to Firebase, including role 40 Assign To ...")
    # Role 40 / InvolvedPartyName is the employee owner. Upload roles every fetch
    # because older runs may have updated hashes without writing roles to Firebase.
    upload_roles_to_firebase(new_snapshot)

    if deleted_tickets:
        delete_payload = build_delete_removed_tickets_payload(deleted_tickets)
        upload_payload_in_batches(FIREBASE_ROOT, delete_payload, label="FB DELETE REMOVED TICKETS")

    logger.info("Step 4/9: Uploading critical-removed metrics ...")
    # This still checks the full latest snapshot, because critical trend depends on status movement.
    upload_critical_removed_metrics(new_snapshot)

    # Full HANA refresh mode:
    # SAP HANA SO/material/delivery/rejection data can change even when C4C ticket core data does not.
    # So refresh HANA SO/material data for ALL current tickets with ERPFreeOrder.
    if not changed_snapshot:
        logger.info("No C4C core ticket changes detected, but continuing with full SAP HANA SO/material refresh.")

    logger.info("Step 5/9: Extracting ERPFreeOrder and vehicle identifiers from ALL current tickets for SAP HANA refresh ...")
    all_ticket_df = snapshot_to_ticket_df(new_snapshot)
    ticket_df = all_ticket_df.copy()

    changed_ticket_ids = set(str(tid).strip() for tid in changed_snapshot.keys() if str(tid).strip())
    tickets_with_erp = set()

    if not ticket_df.empty:
        ticket_df = ticket_df[
            ticket_df["ERPFreeOrder"].fillna("").astype(str).str.strip() != ""
        ].copy()
        tickets_with_erp = set(ticket_df["TicketID"].fillna("").astype(str).str.strip())

    tickets_without_erp = sorted(changed_ticket_ids - tickets_with_erp)
    if tickets_without_erp:
        logger.info("Changed tickets without ERPFreeOrder. Clearing old SO fields: %s", len(tickets_without_erp))
        clear_payload = build_clear_so_payload(tickets_without_erp, reason="Not Found")
        upload_payload_in_batches(FIREBASE_ROOT, clear_payload, label="FB CLEAR NO ERPFreeOrder")

    if ticket_df.empty:
        logger.info("No current tickets have ERPFreeOrder. Continuing with vehicle dispatch refresh only.")
    else:
        logger.info("Current tickets with ERPFreeOrder for HANA refresh: %s", len(ticket_df))

    logger.info("Step 6/9: Connecting SAP HANA and fetching SO/item plus vehicle dispatch data ...")
    with pyodbc.connect(SAP_HANA_DSN, autocommit=True) as conn:
        so_item_df = fetch_so_items(conn, ticket_df) if not ticket_df.empty else pd.DataFrame()
        direct_vehicle_dispatch_df = fetch_vehicle_dispatch_by_serial_candidates(conn, all_ticket_df)
        sales_order_dispatch_df = fetch_vehicle_dispatch_by_sales_orders(
            conn,
            sorted(set(so_item_df["Sales Order"].fillna("").astype(str).str.strip().tolist())) if not so_item_df.empty and "Sales Order" in so_item_df.columns else [],
        )
        vehicle_base_summary = fetch_vehicle_base_summary(conn, cutoff_yyyymmdd="20250101")
        approved_cost_payload = build_approved_cost_sap_po_short_text_sidecar(conn)

    logger.info("Matched SO item rows for current ERPFreeOrder tickets: %s", len(so_item_df))
    logger.info("Vehicle dispatch matches by ticket serial/chassis: %s", len(direct_vehicle_dispatch_df))
    logger.info("Vehicle dispatch matches by sales order fallback: %s", len(sales_order_dispatch_df))
    logger.info(
        "Vehicle base summary rows: %s, total vehicles since 2025-01-01: %s",
        vehicle_base_summary.get("totalRows", 0),
        vehicle_base_summary.get("totalVehicles", 0),
    )

    logger.info("Step 7/9: Building final output for refreshed HANA tickets ...")
    final_df = build_final_output(ticket_df, so_item_df)
    vehicle_dispatch_df = resolve_vehicle_dispatch_rows(
        all_ticket_df,
        so_item_df,
        direct_vehicle_dispatch_df,
        sales_order_dispatch_df,
    )

    # Safety clear: for any changed ERPFreeOrder ticket whose final Sales Order is blank,
    # upload an explicit empty list for details and blank SO fields.
    if not final_df.empty:
        unmatched_mask = final_df["Sales Order"].fillna("").astype(str).str.strip() == ""
        unmatched_ticket_ids = sorted(set(final_df.loc[unmatched_mask, "TicketID"].astype(str).str.strip()))
    else:
        unmatched_ticket_ids = sorted(tickets_with_erp)

    if unmatched_ticket_ids:
        logger.info("SAP unmatched changed tickets. Clearing old SO fields: %s", len(unmatched_ticket_ids))
        clear_payload = build_clear_so_payload(unmatched_ticket_ids, reason="Not Found")
        upload_payload_in_batches(FIREBASE_ROOT, clear_payload, label="FB CLEAR UNMATCHED SO")

    material_price_map = build_material_price_map(final_df)

    logger.info("Step 8/9: Uploading refreshed SO fields to Firebase ...")
    upload_ticket_fields_to_firebase(final_df, material_price_map=material_price_map)
    upload_vehicle_dispatch_to_firebase(vehicle_dispatch_df)
    write_vehicle_base_summary(vehicle_base_summary)
    write_analysis_ticket_base_csv(new_snapshot, final_df)
    refresh_analysis_offline_assets(vehicle_base_summary)
    if approved_cost_payload:
        logger.info("Step 8A/9: Writing Approved Cost SAP PO Short Text sidecar to Firebase ...")
        write_approved_cost_sidecar(approved_cost_payload)
    # Also push to Firebase so analysis.html can consume it there (it already
    # reads other analysis data from `analysisVehicleBaseSummary` / RTDB).
    try:
        fb_update_with_retry("analysisVehicleBaseSummary", vehicle_base_summary)
        logger.info(
            "Pushed vehicle base summary to Firebase: %s vehicles across %s series",
            vehicle_base_summary.get("totalVehicles", 0),
            len(vehicle_base_summary.get("seriesBase", {})),
        )
    except Exception as exc:
        logger.warning("Failed to push vehicle base summary to Firebase: %s", exc)

    hana_cost_payload = build_hana_cost_sidecar(
        final_df,
        as_of_date=datetime.now(timezone.utc).date(),
        material_price_map=material_price_map,
    )
    if hana_cost_payload:
        logger.info("Step 8B/9: Writing HANA-derived delivery-flow cost sidecar to Firebase ...")
        write_hana_cost_sidecar(hana_cost_payload)

    logger.info("Step 9/9: Updating changed ticket hash snapshot ...")
    hash_payload = build_snapshot_hash_payload(changed_hash_payload)
    upload_payload_in_batches(FIREBASE_ROOT, hash_payload, label="FB HASH SNAPSHOT")

    export_excel_single_sheet(final_df)

    rebuild_model_series_assets_after_fetch()
    analytics_rebuilt = rebuild_dashboard_analytics_after_fetch()
    if not analytics_rebuilt:
        rebuild_ticket_timeline_export_after_fetch()

    close_thread_session()

    logger.info("Done. Firebase updated. Excel export is disabled by default for automation.")
    logger.info("Total elapsed: %.1fs", time.time() - total_started)

if __name__ == "__main__":
    main()
