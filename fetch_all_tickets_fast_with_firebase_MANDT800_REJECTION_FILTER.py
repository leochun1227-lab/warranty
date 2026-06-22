from __future__ import annotations

import os
import re
import sys
import json
import time
import logging
import threading
import hashlib
import subprocess
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
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
# ====================================================


# ================= Firebase é…ç½® =================
FIREBASE_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app"
)
FIREBASE_SA_PATH = os.getenv(
    "FIREBASE_SA_PATH",
    r"C:\Users\yan\Desktop\snowy-hr-report-firebase-adminsdk-fbsvc-5dccd921e0.json"
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


def sql_quote(s: str) -> str:
    return str(s).replace("'", "''")


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
                "ERPFreeOrder": erp_free_order,
                "_Lookup1": lookup1,
                "_Lookup2": lookup2,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        for col in ["TicketID", "DealerID", "DealerName", "ERPFreeOrder", "_Lookup1", "_Lookup2"]:
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str).str.strip()
    return df


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
        vbak."ERDAT"                      AS "SO Created Date"
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
        COUNT(DISTINCT lips."VBELN")      AS "Delivery Count"
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
        vbap."POSNR"                      AS "Sales Order Item",
        vbap."MATNR"                      AS "Material",
        vbap."ARKTX"                      AS "Description",
        vbap."KWMENG"                     AS "Order Qty",
        vbap."VRKME"                      AS "Sales Unit",
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
        ib."Sales Order Item",
        ib."Material",
        ib."Description",
        ib."Order Qty",
        ib."Sales Unit",
        COALESCE(gs."Delivery Count", 0) AS "Delivery Count",
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
    "Sales Order Item",
    "Material",
    "Description",
    "Order Qty",
    "Sales Unit",
    "Delivery Count",
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
            "Material", "Description", "Order Qty", "Sales Unit",
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
            "Material", "Description", "Order Qty", "Sales Unit",
            "Delivery Count", "Rejection Reason", "Item Rejection Status"
        ])

    df = pd.concat(frames, ignore_index=True)

    for col in [
        "TicketID", "ERPFreeOrder", "LookupSalesOrder", "Sales Order",
        "Sales Order Item", "Material", "Description", "Sales Unit",
        "Rejection Reason", "Item Rejection Status"
    ]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    if "SO Created Date" in df.columns:
        dt = pd.to_datetime(df["SO Created Date"], errors="coerce")
        df["SO Created Date"] = dt.dt.strftime("%Y-%m-%d")
        df["SO Created Date"] = df["SO Created Date"].where(dt.notna(), "")

    if "Delivery Count" in df.columns:
        df["Delivery Count"] = pd.to_numeric(df["Delivery Count"], errors="coerce").fillna(0).astype(int)

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
        "Sales Order",
        "SO Created Date",
        "Issue Status",
        "Sales Order Item",
        "Material",
        "Description",
        "Order Qty",
        "Sales Unit",
        "Delivery Count",
        "Rejection Reason",
        "Item Rejection Status",
        "Order Rejection Status",
    ]

    if ticket_df.empty:
        return pd.DataFrame(columns=final_cols)

    so_item_df = choose_best_match_per_ticket(so_item_df)

    merged = ticket_df.merge(
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
        not_found["Issue Status"] = "Not Found"
        not_found["Sales Order Item"] = ""
        not_found["Material"] = ""
        not_found["Description"] = ""
        not_found["Order Qty"] = ""
        not_found["Sales Unit"] = ""
        not_found["Delivery Count"] = ""
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
def build_ticket_fields_payload(final_df: pd.DataFrame) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}

    if final_df.empty:
        return payload

    work = final_df.copy()

    text_cols = [
        "TicketID", "Sales Order", "SO Created Date", "Issue Status",
        "Sales Order Item", "Material", "Description", "Order Qty",
        "Sales Unit", "Rejection Reason", "Item Rejection Status",
        "Order Rejection Status"
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

        base = f"tickets/{ticket_id}/ticket"

        payload[f"{base}/Sales Order"] = sales_order
        payload[f"{base}/SO Created Date"] = so_created_date if sales_order else ""
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
            "Delivery Count",
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
                "Delivery Count": int(row.get("Delivery Count", 0) or 0),
                "Rejection Reason": row.get("Rejection Reason", ""),
                "Item Rejection Status": row.get("Item Rejection Status", ""),
            })

        payload[f"{base}/Sales Order Details"] = details
        payload[f"{base}/soLastSyncAt"] = SERVER_TIMESTAMP

    return payload

def upload_ticket_fields_to_firebase(final_df: pd.DataFrame):
    payload_all = build_ticket_fields_payload(final_df)
    upload_payload_in_batches(FIREBASE_ROOT, payload_all, label="FB SO UPLOAD")

    if payload_all:
        now_iso = iso_utc_now()
        db.reference(FIREBASE_ROOT).update({"ticketSoSyncAt": now_iso})


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


def approval_closed_bucket(code: str, text: str) -> str:
    code_norm = as_clean_str(code).upper()
    text_norm = as_clean_str(text).lower()
    if code_norm == "Z9" and text_norm == "sales order approved":
        return "approved"
    if code_norm == "Y8" and text_norm == "unapproved claims closed (closed)":
        return "unapproved"
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
        bucket = approval_closed_bucket(curr_code, curr_text)

        if bucket and status_changed and approval_closed_bucket(prev_code, prev_text) != bucket:
            events.append({
                "ticketId": ticket_id,
                "bucket": bucket,
                "fromStatusCode": prev_code,
                "fromStatusText": prev_text,
                "toStatusCode": curr_code,
                "toStatusText": curr_text,
                "createdOn": as_clean_str(curr.get("createdOn")),
                "amountIncludingTax": as_clean_str(curr.get("amountIncludingTax")),
                "detectedAt": detected_at_iso,
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
            events.append({
                "ticketId": ticket_id,
                "fromStatusCode": prev_code,
                "fromStatusText": prev_text,
                "toStatusCode": curr_code,
                "toStatusText": curr_text,
                "detectedAt": detected_at_iso,
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


def rebuild_dashboard_analytics_after_fetch():
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ctm_v44_history_safe_mandt800_rejection_filter.py")
    if not os.path.exists(script_path):
        logger.warning("Analytics rebuild skipped. Script not found: %s", script_path)
        return

    logger.info("Step 10/10: Rebuilding dashboard history and analytics from updated Firebase tickets ...")
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

    logger.info("Step 5/9: Extracting ERPFreeOrder from ALL current tickets for SAP HANA refresh ...")
    ticket_df = snapshot_to_ticket_df(new_snapshot)

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
        logger.info("No current tickets have ERPFreeOrder. Updating changed hashes and finishing.")
        hash_payload = build_snapshot_hash_payload(changed_hash_payload)
        upload_payload_in_batches(FIREBASE_ROOT, hash_payload, label="FB HASH SNAPSHOT")
        db.reference(FIREBASE_ROOT).update({"ticketSoSyncAt": iso_utc_now()})
        rebuild_dashboard_analytics_after_fetch()
        close_thread_session()
        logger.info("Total elapsed: %.1fs", time.time() - total_started)
        return

    logger.info("Current tickets with ERPFreeOrder for HANA refresh: %s", len(ticket_df))

    logger.info("Step 6/9: Connecting SAP HANA and fetching SO/item data for changed tickets only ...")
    with pyodbc.connect(SAP_HANA_DSN, autocommit=True) as conn:
        so_item_df = fetch_so_items(conn, ticket_df)

    logger.info("Matched SO item rows for current ERPFreeOrder tickets: %s", len(so_item_df))

    logger.info("Step 7/9: Building final output for refreshed HANA tickets ...")
    final_df = build_final_output(ticket_df, so_item_df)

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

    logger.info("Step 8/9: Uploading refreshed SO fields to Firebase ...")
    upload_ticket_fields_to_firebase(final_df)

    logger.info("Step 9/9: Updating changed ticket hash snapshot ...")
    hash_payload = build_snapshot_hash_payload(changed_hash_payload)
    upload_payload_in_batches(FIREBASE_ROOT, hash_payload, label="FB HASH SNAPSHOT")

    export_excel_single_sheet(final_df)

    rebuild_dashboard_analytics_after_fetch()

    close_thread_session()

    logger.info("Done. Firebase updated. Excel export is disabled by default for automation.")
    logger.info("Total elapsed: %.1fs", time.time() - total_started)

if __name__ == "__main__":
    main()
