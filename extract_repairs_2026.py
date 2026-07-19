from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "outputs" / "analysis_ticket_base.csv"
LEGACY_DEFAULT_SOURCE = ROOT / "SAPAnalyticsReport_ZF8C06456D7698BCB54F44D_.csv"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "repairers_2026"

DEFAULT_DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)
DEFAULT_MANDT = os.getenv("SAP_CLIENT", "800")
DEFAULT_CNY_TO_AUD = 5.0

logger = logging.getLogger("extract_repairs_2026")


# ---------------------------------------------------------------------------
# Basic value helpers (unchanged)
# ---------------------------------------------------------------------------

def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def meaningful_text(value: Any) -> str:
    text = clean(value)
    if not text:
        return ""
    if text.upper() in {"#", "NOT ASSIGNED", "-", "UNKNOWN", "N/A"}:
        return ""
    return text


def parse_date(value: Any) -> Optional[datetime]:
    text = clean(value)
    if not text or text == "#":
        return None
    for fmt in (
        "%d.%m.%Y",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M:%S AUSACT",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_amount(value: Any) -> float:
    text = clean(value).replace(",", "")
    if not text or text == "#":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def normalize_name(value: Any) -> str:
    text = clean(value).upper()
    if not text or text in {"#", "NOT ASSIGNED", "-", "UNKNOWN"}:
        return ""
    text = text.replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def family_key(value: Any) -> str:
    tokens = normalize_name(value).split()
    if not tokens:
        return ""
    suffix_noise = {
        "REPAIRS", "REPAIR", "WORKSHOP", "WORKSHOPS", "SERVICE", "SERVICES",
        "PTY", "LTD", "LIMITED", "CO", "COMPANY", "PARTS", "ONLY", "DO",
        "NOT", "USE",
    }
    while tokens and tokens[-1] in suffix_noise:
        tokens.pop()
    while tokens and tokens[0] in {"THE"}:
        tokens.pop(0)
    return " ".join(tokens).strip()


def address_group(row: Dict[str, str]) -> str:
    dealer_name = meaningful_text(row.get("Dealer Name"))
    country = clean(row.get("Country/Region"))
    postal = clean(row.get("Service Requester Postal Code"))
    if dealer_name:
        return dealer_name
    if country or postal:
        return " / ".join(part for part in (country, postal) if part)
    return "Unknown"


def is_customer_like_repairer(value: Any) -> bool:
    text = clean(value).lower()
    if not text:
        return False
    return "customer" in text and ("repair" in text or "repairer" in text)


def is_unassigned_repairer(value: Any) -> bool:
    text = clean(value).lower()
    if not text:
        return True
    return text in {"not assigned", "unassigned", "notassigned", "#", "-", "n/a", "unknown"}


SNOWY_RIVER_RV_TOKEN = "SNOWY RIVER RV PTY LTD"


def is_snowy_river_service_tech(value: Any) -> bool:
    text = normalize_name(value)
    return SNOWY_RIVER_RV_TOKEN in text and "WANGARATTA" not in text


def is_numericish(value: Any) -> bool:
    text = clean(value).replace(" ", "")
    return bool(text) and text.isdigit()


def is_valid_repairshop_name(value: Any) -> bool:
    text = meaningful_text(value)
    return bool(text) and not is_numericish(text)


def choose_snowy_river_repair_name(
    service_tech: Any,
    repairshop_id: Any,
    dealer_name: Any,
) -> str:
    """Snowy River tickets keep the service tech name unless we have a real shop name.

    The user-facing rule is:
    - if Service Technician does not fully match the Snowy River token, keep the
      Service Technician value as-is;
    - if it does match, prefer Repairshop ID / Repair Shop ID;
    - fall back to Dealer Name when the shop value is missing or numeric.
    """
    service_text = clean(service_tech)
    dealer_text = meaningful_text(dealer_name) or "Unassigned"
    if not is_snowy_river_service_tech(service_text):
        return service_text or dealer_text

    shop_text = meaningful_text(repairshop_id)
    if is_valid_repairshop_name(shop_text):
        return shop_text
    return dealer_text


# ---------------------------------------------------------------------------
# State inference: dealer text → dealer code → postal-code fallback
# ---------------------------------------------------------------------------

STATE_PATTERN_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("NZ", ("NEW ZEALAND", "CHRISTCHURCH", "MARSDEN POINT", "MARDEN POINT", "AUCKLAND", "HAMILTON", "WELLINGTON", "DUNEDIN")),
    ("QLD", ("QUEENSLAND", "FOREST GLEN", "SLACKS CREEK", "BUNDABERG", "TOOWOOMBA", "TOWNSVILLE", "GYMPIE", "SUNSHINE COAST", "MAROOCHYDORE", "CABOOLTURE", "KAWANA", "SOUTHPORT", "NOOSA", "BRISBANE", "CAIRNS", "MACKAY", "ROCKHAMPTON")),
    ("NSW", ("NEW SOUTH WALES", "NEWCASTLE", "HEATHERBRAE", "MORISSET", "BERESFIELD", "BEREFIELD", "SOUTH NOWRA", "ULLADULLA", "COFFS", "BOAMBEE", "ST MARYS", "PARRAMATTA", "WOLLONGONG", "LISMORE", "TAREE", "PORT MACQUARIE", "SYDNEY", "ORANGE", "DUBBO", "ALBURY")),
    ("VIC", ("VICTORIA", "FRANKSTON", "GEELONG", "TRARALGON", "WANGARATTA", "WARRNAMBOOL", "BENDIGO", "KEYSBOROUGH", "DANDENONG", "CRANBOURNE", "BAYSWATER", "MELBOURNE", "HALLAM", "BALLARAT", "SHEPPARTON", "MILDURA")),
    ("WA", ("WESTERN AUSTRALIA", "PERTH", "ST JAMES", "MANDURAH", "JANDAKOT", "BELMONT", "CANNING VALE", "CARAVANS WA", "BUNBURY", "GERALDTON")),
    ("SA", ("SOUTH AUSTRALIA", "ADELAIDE", "POORAKA", "MILE END", "MUNNO PARA", "WOODVILLE", "GAWLER")),
    ("TAS", ("TASMANIA", "LAUNCESTON", "DEVONPORT", "HOBART", "ROCHERLEA")),
    ("ACT", ("AUSTRALIAN CAPITAL TERRITORY", "CANBERRA", "FYSHWICK", "HUME")),
    ("NT", ("NORTHERN TERRITORY", "DARWIN", "ALICE SPRINGS", "KATHERINE")),
]


def infer_state_from_text(*values: Any) -> Tuple[str, str]:
    text = " ".join(normalize_name(value) for value in values if normalize_name(value))
    if not text:
        return "", ""
    for state, patterns in STATE_PATTERN_RULES:
        for pattern in patterns:
            normalized_pattern = normalize_name(pattern)
            if normalized_pattern and re.search(rf"\b{re.escape(normalized_pattern)}\b", text):
                return state, f"text:{pattern}"
    return "", ""


def infer_state_from_postal(postal: Any, country: Any = "") -> Tuple[str, str]:
    """Australia Post state ranges + NZ 4-digit detection.

    AU rules (first digit, sometimes first two):
      1xxx -> NSW (also 2xxx, but 200-299 = ACT, 260-269 = ACT/NSW border, we prefer ACT for 02xx)
      2xxx -> NSW / ACT (02xx = ACT)
      3xxx / 8xxx -> VIC
      4xxx / 9xxx -> QLD
      5xxx -> SA
      6xxx -> WA
      7xxx -> TAS
      0xxx (08xx/09xx) -> NT
    NZ rule: 4-digit postal code AND country/region contains NZ / NEW ZEALAND
    """
    raw = clean(postal)
    if not raw:
        return "", ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return "", ""

    country_text = clean(country).upper()
    if country_text in {"NZ", "NEW ZEALAND"} or "NEW ZEALAND" in country_text:
        return "NZ", f"postal:{raw}"

    # AU postcodes are 4 digits, zero-padded
    if len(digits) < 3 or len(digits) > 4:
        return "", ""
    pc = digits.zfill(4)
    first = pc[0]
    first_two = pc[:2]

    if first_two in {"02"} or (pc >= "0200" and pc <= "0299"):
        return "ACT", f"postal:{raw}"
    if first_two in {"08", "09"} or (pc >= "0800" and pc <= "0999"):
        return "NT", f"postal:{raw}"
    if first == "1" or first == "2":
        return "NSW", f"postal:{raw}"
    if first == "3" or first == "8":
        return "VIC", f"postal:{raw}"
    if first == "4" or first == "9":
        return "QLD", f"postal:{raw}"
    if first == "5":
        return "SA", f"postal:{raw}"
    if first == "6":
        return "WA", f"postal:{raw}"
    if first == "7":
        return "TAS", f"postal:{raw}"
    return "", ""


# States that should be folded into another state for display / aggregation.
# ACT gets rolled into NSW (single-digit volume on this dataset makes it
# invisible on the map and users treat it as part of NSW anyway).
STATE_ALIAS: Dict[str, str] = {
    "ACT": "NSW",
}


def _apply_alias(state: str, source: str) -> Tuple[str, str]:
    aliased = STATE_ALIAS.get(state)
    if aliased and aliased != state:
        return aliased, f"{source}|alias:{state}->{aliased}"
    return state, source


def infer_state_for_row(
    row: Dict[str, str],
    dealer_code_state: Dict[str, str],
    repairshop_hint: Any = "",
) -> Tuple[str, str]:
    # 1. Preferred repair-shop text when Snowy River has a meaningful shop name.
    state, source = infer_state_from_text(repairshop_hint, row.get("Dealer Name"), row.get("Service Technician"), row.get("Dealer"))
    if state:
        return _apply_alias(state, source)
    # 2. Postal-code / country fallback (ticket requester)
    state, source = infer_state_from_postal(row.get("Service Requester Postal Code"), row.get("Country/Region"))
    if state:
        return _apply_alias(state, source)
    # 3. Dealer code fallback
    dealer_code = clean(row.get("Dealer"))
    if dealer_code and dealer_code in dealer_code_state:
        return _apply_alias(dealer_code_state[dealer_code], f"dealer_code:{dealer_code}")
    return "Unknown", ""


# ---------------------------------------------------------------------------
# SAP PO cost and invoice status enrichment
# ---------------------------------------------------------------------------

def _sap_date_to_iso(v: Any) -> str:
    s = clean(v)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _chunks(lst: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def fetch_po_costs(po_numbers: Iterable[str], dsn: str, mandt: str, batch_size: int = 500) -> Dict[str, Dict[str, Any]]:
    """Return { PO_NUM: {amount, currency, source_amount, source_currency} }."""
    unique = sorted({clean(p) for p in po_numbers if clean(p)})
    out: Dict[str, Dict[str, Any]] = {}
    if not unique:
        return out

    try:
        import pyodbc  # local import so the module can be used without SAP
    except ImportError:
        logger.warning("pyodbc not installed; skipping PO cost enrichment. All PO amounts will be 0.")
        return out

    try:
        conn = pyodbc.connect(dsn, autocommit=True)
    except Exception as exc:  # pragma: no cover
        logger.warning("HANA connect failed (%s); skipping PO cost enrichment.", exc)
        return out

    try:
        for batch in _chunks(unique, batch_size):
            in_list = "','".join(p.replace("'", "''") for p in batch)
            sql = f"""
                SELECT
                    ekpo."EBELN" AS "PO",
                    SUM(CASE WHEN COALESCE(ekpo."LOEKZ", '') = '' THEN COALESCE(ekpo."NETWR", 0) ELSE 0 END) AS "ITEM_AMT",
                    MAX(ekko."WAERS") AS "CURRENCY"
                FROM "SAPHANADB"."EKPO" ekpo
                INNER JOIN "SAPHANADB"."EKKO" ekko
                    ON ekko."MANDT" = ekpo."MANDT"
                   AND ekko."EBELN" = ekpo."EBELN"
                WHERE ekpo."MANDT" = '{mandt}'
                  AND ekpo."EBELN" IN ('{in_list}')
                GROUP BY ekpo."EBELN"
            """
            cur = conn.execute(sql)
            for row in cur.fetchall():
                po = clean(row[0])
                if not po:
                    continue
                item_amt = float(row[1] or 0)
                amount = item_amt
                out[po] = {
                    "amount": amount,
                    "currency": clean(row[2]) or "AUD",
                    "source_amount": item_amt,
                    "source_currency": clean(row[2]) or "AUD",
                }
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return out


def fetch_invoice_status(po_numbers: Iterable[str], dsn: str, mandt: str, batch_size: int = 500) -> Dict[str, Dict[str, Any]]:
    """Return { PO_NUM: {invoice_number, invoice_date, invoice_rows} }."""
    unique = sorted({clean(p) for p in po_numbers if clean(p)})
    out: Dict[str, Dict[str, Any]] = {}
    if not unique:
        return out

    try:
        import pyodbc
    except ImportError:
        logger.warning("pyodbc not installed; skipping invoice status enrichment. All tickets will be marked open.")
        return out

    try:
        conn = pyodbc.connect(dsn, autocommit=True)
    except Exception as exc:  # pragma: no cover
        logger.warning("HANA connect failed (%s); skipping invoice status enrichment.", exc)
        return out

    try:
        for batch in _chunks(unique, batch_size):
            in_list = "','".join(p.replace("'", "''") for p in batch)
            sql = f"""
                SELECT
                    "EBELN" AS "PO",
                    MIN("BELNR") AS "FIRST_INV",
                    MAX("BELNR") AS "LAST_INV",
                    MAX("BUDAT") AS "LAST_INV_DATE",
                    COUNT(*) AS "INV_ROWS"
                FROM "SAPHANADB"."EKBE"
                WHERE "MANDT" = '{mandt}'
                  AND "VGABE" = '2'
                  AND "EBELN" IN ('{in_list}')
                GROUP BY "EBELN"
            """
            cur = conn.execute(sql)
            for row in cur.fetchall():
                po = clean(row[0])
                if not po:
                    continue
                out[po] = {
                    "invoice_number": clean(row[2]) or clean(row[1]),
                    "invoice_date": _sap_date_to_iso(row[3]),
                    "invoice_rows": int(row[4] or 0),
                }
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return out


def cny_to_aud(amount: float, currency: str, rate: float) -> float:
    if not amount:
        return 0.0
    cur = clean(currency).upper()
    if cur == "CNY":
        return round(amount / (rate or 5.0), 2)
    return round(amount, 2)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RepairGroup:
    key: str                       # family_key + "|" + state (so same name in two states splits)
    display_name: str              # base name without state
    state: str                     # the state this row is bucketed under
    total_tickets: int = 0
    invoiced_tickets: int = 0
    open_tickets: int = 0
    confirmed_cost_aud: float = 0.0
    pending_amount_aud: float = 0.0
    first_created_on: Optional[str] = None
    last_created_on: Optional[str] = None
    address_counter: Counter = field(default_factory=Counter)
    dealer_counter: Counter = field(default_factory=Counter)
    raw_name_counter: Counter = field(default_factory=Counter)
    # Firebase-derived RepairerBusinessNameID votes for this group. If any
    # tickets in the group carry a value ("Snowy River Perth" style), the
    # top-voted string wins over raw_name for the display label.
    firebase_name_counter: Counter = field(default_factory=Counter)


@dataclass
class StateGroup:
    key: str
    total_tickets: int = 0
    invoiced_tickets: int = 0
    open_tickets: int = 0
    confirmed_cost_aud: float = 0.0
    pending_amount_aud: float = 0.0
    repairer_counter: Counter = field(default_factory=Counter)      # by split-key
    dealer_counter: Counter = field(default_factory=Counter)
    snowy_ticket_count: int = 0
    snowy_confirmed_cost_aud: float = 0.0
    snowy_repairer_counter: Counter = field(default_factory=Counter)


@dataclass
class WeeklyGroup:
    key: str
    total_tickets: int = 0
    invoiced_tickets: int = 0
    open_tickets: int = 0
    confirmed_cost_aud: float = 0.0
    pending_amount_aud: float = 0.0
    state_ticket_counter: Counter = field(default_factory=Counter)
    state_confirmed_counter: Counter = field(default_factory=Counter)
    state_pending_counter: Counter = field(default_factory=Counter)
    state_repairer_counter: Dict[str, set] = field(default_factory=lambda: defaultdict(set))
    repairer_counter: Counter = field(default_factory=Counter)
    repairer_confirmed_counter: Counter = field(default_factory=Counter)
    repairer_pending_counter: Counter = field(default_factory=Counter)


# ---------------------------------------------------------------------------
# CSV read
# ---------------------------------------------------------------------------

FIREBASE_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app",
)
FIREBASE_SA_PATH = os.getenv("FIREBASE_SA_PATH", "firebase-service-account.json")
FIREBASE_ROOT = os.getenv("FIREBASE_ROOT", "c4cTickets_test")


def load_firebase_repairer_names(root: str = FIREBASE_ROOT) -> Dict[str, str]:
    """Fetch every ticket's RepairerBusinessNameID from Firebase.

    Returns {ticket_id: repairer_business_name_id}. Tickets whose Firebase
    record does not carry a RepairerBusinessNameID are omitted, so an
    absent entry in the caller means "fall back to the CSV Service
    Technician value". Firebase sometimes returns the /tickets node as a
    list (auto-array conversion when keys look numeric), so both list and
    dict shapes are handled.

    Failures (no service-account JSON, firebase-admin not installed, network
    error) log a warning and return {}, so the extract still runs on CSV
    data alone.
    """
    if not FIREBASE_SA_PATH or not os.path.exists(FIREBASE_SA_PATH):
        logger.warning(
            "FIREBASE_SA_PATH not found at %r; skipping Firebase RepairerBusinessNameID overlay",
            FIREBASE_SA_PATH,
        )
        return {}
    try:
        import firebase_admin
        from firebase_admin import credentials, db
    except ImportError:
        logger.warning("firebase-admin not installed; skipping Firebase overlay")
        return {}

    try:
        if not getattr(firebase_admin, "_apps", None):
            cred = credentials.Certificate(FIREBASE_SA_PATH)
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
        node = db.reference(f"{root}/tickets").get() or {}
    except Exception as exc:
        logger.warning("Firebase read failed (%s); continuing without overlay", exc)
        return {}

    if isinstance(node, list):
        entries = ((str(i), r) for i, r in enumerate(node) if r)
    elif isinstance(node, dict):
        entries = node.items()
    else:
        return {}

    out: Dict[str, str] = {}
    for _, row in entries:
        if not isinstance(row, dict):
            continue
        ticket = row.get("ticket") if isinstance(row.get("ticket"), dict) else row
        if not isinstance(ticket, dict):
            continue
        tid = clean(
            ticket.get("TicketID")
            or ticket.get("Ticket ID")
            or ticket.get("Ticket")
            or row.get("TicketID")
            or row.get("Ticket ID")
            or row.get("Ticket")
        )
        name = clean(ticket.get("RepairerBusinessNameID"))
        if tid and name:
            out[tid] = name
    logger.info("Firebase RepairerBusinessNameID overlay loaded for %s tickets", len(out))
    return out


def read_rows(source: Path, start_year: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    stats = {
        "source": str(source),
        "year": start_year,
        "start_year": start_year,
        "rows_read": 0,
        "rows_kept": 0,
        "rows_skipped_no_date": 0,
        "rows_skipped_year": 0,
        "rows_skipped_before_year": 0,
        "rows_skipped_customer_like_repairer": 0,
        "rows_skipped_unassigned_repairer": 0,
    }

    with source.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            stats["rows_read"] += 1
            posting = parse_date(raw.get("Posting Date")) or parse_date(raw.get("PostingDate"))
            created = parse_date(raw.get("Created On"))
            decision = posting or created
            if decision is None:
                stats["rows_skipped_no_date"] += 1
                continue
            if decision.year < start_year:
                stats["rows_skipped_year"] += 1
                stats["rows_skipped_before_year"] += 1
                continue

            repairer_raw = clean(raw.get("Service Technician"))
            if is_unassigned_repairer(repairer_raw):
                stats["rows_skipped_unassigned_repairer"] += 1
                continue
            if is_customer_like_repairer(repairer_raw):
                stats["rows_skipped_customer_like_repairer"] += 1
                continue

            rows.append({
                "Created On": clean(raw.get("Created On")),
                "Posting Date": clean(raw.get("Posting Date")),
                "Changed On": clean(raw.get("Changed On")),
                "Ticket ID": clean(raw.get("Ticket ID")),
                "Ticket": clean(raw.get("Ticket")),
                "Ticket Type": clean(raw.get("Ticket Type")),
                "Status": clean(raw.get("Status")),
                "Dealer": clean(raw.get("Dealer")),
                "Dealer Name": clean(raw.get("Dealer Name")),
                "Country/Region": clean(raw.get("Country/Region")),
                "Service Requester Postal Code": clean(raw.get("Service Requester Postal Code")),
                "Service Technician": repairer_raw,
                "Repairshop ID": clean(raw.get("Repairshop ID")),
                "Repair Shop ID": clean(raw.get("Repair Shop ID")),
                "ERP Purchase Order ID": clean(raw.get("ERP Purchase Order ID")),
                "Sales Order": clean(raw.get("Sales Order")),
                "ClaimTotalAmount": parse_amount(raw.get("ClaimTotalAmount")),
                "Factory Parts Claim Total Amount": parse_amount(raw.get("Factory Parts Claim Total Amount")),
                "LabourHoursTotalAmount": parse_amount(raw.get("LabourHoursTotalAmount")),
                "Repairer Parts Claim Total Amount": parse_amount(raw.get("Repairer Parts Claim Total Amount")),
            })

    stats["rows_kept"] = len(rows)
    return rows, stats


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def choose_display(counter: Counter) -> str:
    if not counter:
        return ""
    items = list(counter.items())
    items.sort(key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
    return items[0][0]


def choose_real_display(counter: Counter) -> str:
    if not counter:
        return ""
    items = [(name, count) for name, count in counter.items() if normalize_name(name) != "UNKNOWN"]
    if not items:
        items = list(counter.items())
    items.sort(key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
    return items[0][0]


def week_start_key(value: Any) -> str:
    dt = parse_date(value)
    if dt is None:
        return ""
    start = dt - timedelta(days=dt.weekday())
    return start.date().isoformat()


def week_end_key(week_start: str) -> str:
    dt = parse_date(week_start)
    if dt is None:
        return ""
    return (dt + timedelta(days=6)).date().isoformat()


def week_label(week_start: str) -> str:
    start = parse_date(week_start)
    if start is None:
        return week_start
    end = start + timedelta(days=6)
    return f"{start:%Y-%m-%d} to {end:%Y-%m-%d}"


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------

def build_summaries(
    rows: List[Dict[str, Any]],
    po_cost_map: Dict[str, Dict[str, Any]],
    invoice_map: Dict[str, Dict[str, Any]],
    cny_to_aud_rate: float,
    firebase_name_map: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    repair_groups: Dict[str, RepairGroup] = {}
    state_groups: Dict[str, StateGroup] = {}
    week_groups: Dict[str, WeeklyGroup] = {}
    detail_rows: List[Dict[str, Any]] = []

    # Learn dealer_code -> state from text hits, use as second-pass fallback
    dealer_code_state_candidates: Dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        state, _ = infer_state_from_text(row.get("Dealer Name"), row.get("Service Technician"), row.get("Dealer"))
        dealer_code = clean(row.get("Dealer"))
        if dealer_code and state and state != "Unknown":
            dealer_code_state_candidates[dealer_code][state] += 1
    dealer_code_state = {
        code: counter.most_common(1)[0][0]
        for code, counter in dealer_code_state_candidates.items()
        if counter
    }

    firebase_name_map = firebase_name_map or {}
    logger.info("Firebase RepairerBusinessNameID overlay available for %s tickets", len(firebase_name_map))

    for row in rows:
        ticket_id = clean(row.get("Ticket ID"))
        firebase_name = firebase_name_map.get(ticket_id, "") if ticket_id else ""
        service_tech = clean(row.get("Service Technician"))
        dealer_name = meaningful_text(row.get("Dealer Name")) or "Unassigned"
        dealer_counter_name = meaningful_text(row.get("Dealer Name")) or meaningful_text(row.get("Dealer")) or "Unknown"
        snowy_service_tech = is_snowy_river_service_tech(service_tech)
        repairshop_hint = meaningful_text(row.get("Repairshop ID")) or meaningful_text(row.get("Repair Shop ID")) or meaningful_text(firebase_name)
        raw_name = choose_snowy_river_repair_name(service_tech, repairshop_hint, dealer_name)
        if not raw_name:
            raw_name = "Unassigned"
        base_key = normalize_name(raw_name) or "UNASSIGNED"
        normalized_key = family_key(raw_name) or base_key
        addr = address_group(row)
        created = clean(row.get("Created On"))
        approved_date = clean(row.get("Posting Date")) or created
        state, state_source = infer_state_for_row(row, dealer_code_state, repairshop_hint)
        week_start = week_start_key(approved_date)

        po = clean(row.get("ERP Purchase Order ID"))
        po_info = po_cost_map.get(po) if po else None
        if po_info:
            confirmed_native = float(po_info.get("amount") or 0)
            confirmed_currency = po_info.get("currency") or "AUD"
            confirmed_aud = cny_to_aud(confirmed_native, confirmed_currency, cny_to_aud_rate)
        else:
            confirmed_native = 0.0
            confirmed_currency = ""
            confirmed_aud = 0.0

        inv = invoice_map.get(po) if po else None
        if inv:
            invoice_status = "invoiced"
            invoice_number = inv.get("invoice_number", "")
            invoice_date = inv.get("invoice_date", "")
        else:
            invoice_status = "open"
            invoice_number = ""
            invoice_date = ""
        pending_aud = 0.0

        # Per-state split: same repairer name in two states = two rows
        split_key = f"{base_key}|{state or 'Unknown'}"
        if split_key not in repair_groups:
            repair_groups[split_key] = RepairGroup(key=split_key, display_name=raw_name, state=state or "Unknown")
        rg = repair_groups[split_key]
        rg.total_tickets += 1
        rg.confirmed_cost_aud += confirmed_aud
        if invoice_status == "invoiced":
            rg.invoiced_tickets += 1
        else:
            rg.open_tickets += 1
        rg.raw_name_counter[raw_name] += 1
        if firebase_name:
            rg.firebase_name_counter[firebase_name] += 1
        rg.address_counter[addr] += 1
        rg.dealer_counter[dealer_counter_name] += 1
        if created:
            if rg.first_created_on is None or created < rg.first_created_on:
                rg.first_created_on = created
            if rg.last_created_on is None or created > rg.last_created_on:
                rg.last_created_on = created

        state_key = state or "Unknown"
        if state_key not in state_groups:
            state_groups[state_key] = StateGroup(key=state_key)
        sg = state_groups[state_key]
        sg.total_tickets += 1
        sg.confirmed_cost_aud += confirmed_aud
        if invoice_status == "invoiced":
            sg.invoiced_tickets += 1
        else:
            sg.open_tickets += 1
        sg.repairer_counter[split_key] += 1
        sg.dealer_counter[dealer_counter_name] += 1
        if snowy_service_tech:
            sg.snowy_ticket_count += 1
            sg.snowy_confirmed_cost_aud += confirmed_aud
            sg.snowy_repairer_counter[split_key] += 1

        if week_start:
            wg = week_groups.setdefault(week_start, WeeklyGroup(key=week_start))
            wg.total_tickets += 1
            wg.confirmed_cost_aud += confirmed_aud
            wg.state_confirmed_counter[state_key] += confirmed_aud
            wg.repairer_confirmed_counter[split_key] += confirmed_aud
            if invoice_status == "invoiced":
                wg.invoiced_tickets += 1
            else:
                wg.open_tickets += 1
            wg.state_ticket_counter[state_key] += 1
            wg.state_repairer_counter[state_key].add(split_key)
            wg.repairer_counter[split_key] += 1

        detail_rows.append({
            **row,
            "TicketID": ticket_id,  # explicit alias so HTML doesn't have to key-guess
            "raw_repairer_name": raw_name,
            "normalized_key": normalized_key,
            "repairer_name": f"{raw_name} ({state_key})" if state_key and state_key != "Unknown" else raw_name,
            "repairer_base_name": raw_name,
            "repairer_split_key": split_key,
            "repairshop_id": repairshop_hint,
            "RepairerBusinessNameID": firebase_name,
            "is_snowy_river": snowy_service_tech,
            "state": state_key,
            "state_source": state_source,
            "week_start": week_start,
            "approved_date": approved_date,
            "invoice_status": invoice_status,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "confirmed_cost_native": round(confirmed_native, 2),
            "confirmed_cost_currency": confirmed_currency,
            "confirmed_cost_aud": confirmed_aud,
            "pending_amount_aud": pending_aud,
        })

    # ---- Repairer rows ----
    repairer_rows: List[Dict[str, Any]] = []
    for split_key, grp in repair_groups.items():
        raw_name = choose_display(grp.raw_name_counter) or grp.display_name
        display_name = f"{raw_name} ({grp.state})" if grp.state and grp.state != "Unknown" else raw_name
        avg_confirmed = (grp.confirmed_cost_aud / grp.total_tickets) if grp.total_tickets else 0.0
        raw_variant_parts = [f"{name} ({count})" for name, count in grp.raw_name_counter.most_common()]
        repairer_rows.append({
            "split_key": split_key,
            "repairer_name": display_name,
            "repairer_base_name": raw_name,
            "normalized_key": family_key(raw_name) or normalize_name(raw_name) or "UNASSIGNED",
            "repairer_business_name_id": choose_display(grp.firebase_name_counter) or "",
            "state": grp.state,
            "ticket_count": grp.total_tickets,
            "invoiced_tickets": grp.invoiced_tickets,
            "open_tickets": grp.open_tickets,
            "confirmed_cost": round(grp.confirmed_cost_aud, 2),
            "avg_warranty_cost": round(avg_confirmed, 2),
            "pending_amount": round(grp.pending_amount_aud, 2),
            "avg_confirmed_cost": round(avg_confirmed, 2),
            "unique_address_groups": len(grp.address_counter),
            "raw_name_variants": len(grp.raw_name_counter),
            "raw_name_variants_text": "; ".join(raw_variant_parts),
            "top_address_group": grp.address_counter.most_common(1)[0][0] if grp.address_counter else "",
            "top_dealer_name": choose_real_display(grp.dealer_counter),
            "first_created_on": grp.first_created_on or "",
            "last_created_on": grp.last_created_on or "",
        })
    repairer_rows.sort(key=lambda r: (-r["ticket_count"], -r["confirmed_cost"], r["repairer_name"]))

    # ---- State rows ----
    state_rows: List[Dict[str, Any]] = []
    for key, grp in state_groups.items():
        avg_confirmed = (grp.confirmed_cost_aud / grp.total_tickets) if grp.total_tickets else 0.0
        state_rows.append({
            "state": key,
            "ticket_count": grp.total_tickets,
            "invoiced_tickets": grp.invoiced_tickets,
            "open_tickets": grp.open_tickets,
            "confirmed_cost": round(grp.confirmed_cost_aud, 2),
            "avg_warranty_cost": round(avg_confirmed, 2),
            "pending_amount": round(grp.pending_amount_aud, 2),
            "avg_confirmed_cost": round(avg_confirmed, 2),
            "unique_repairers": len(grp.repairer_counter),
            "snowy_ticket_count": grp.snowy_ticket_count,
            "snowy_confirmed_cost": round(grp.snowy_confirmed_cost_aud, 2),
            "snowy_unique_repairers": len(grp.snowy_repairer_counter),
            "snowy_avg_confirmed_cost": round((grp.snowy_confirmed_cost_aud / grp.snowy_ticket_count) if grp.snowy_ticket_count else 0.0, 2),
            "top_dealer_name": choose_real_display(grp.dealer_counter),
        })
    state_rows.sort(key=lambda r: (-r["confirmed_cost"], -r["ticket_count"], r["state"]))

    repairer_display_map = {r["split_key"]: r["repairer_name"] for r in repairer_rows}

    # ---- Weekly rows ----
    weekly_rows: List[Dict[str, Any]] = []
    for key, grp in sorted(week_groups.items(), key=lambda kv: kv[0]):
        state_rows_for_week: List[Dict[str, Any]] = []
        for state_key in sorted(grp.state_ticket_counter, key=lambda k: -grp.state_ticket_counter[k]):
            tickets = grp.state_ticket_counter[state_key]
            confirmed = grp.state_confirmed_counter.get(state_key, 0.0)
            state_rows_for_week.append({
                "state": state_key,
                "ticket_count": tickets,
                "confirmed_cost": round(confirmed, 2),
                "pending_amount": 0.0,
                "avg_confirmed_cost": round((confirmed / tickets) if tickets else 0.0, 2),
                "unique_repairers": len(grp.state_repairer_counter.get(state_key, set())),
            })
        top_repairers_for_week: List[Dict[str, Any]] = []
        for split_key, ticket_count in grp.repairer_counter.most_common(20):
            confirmed = grp.repairer_confirmed_counter.get(split_key, 0.0)
            top_repairers_for_week.append({
                "split_key": split_key,
                "repairer_name": repairer_display_map.get(split_key, split_key),
                "ticket_count": ticket_count,
                "confirmed_cost": round(confirmed, 2),
                "avg_confirmed_cost": round((confirmed / ticket_count) if ticket_count else 0.0, 2),
            })
        weekly_rows.append({
            "week_start": key,
            "week_end": week_end_key(key),
            "label": week_label(key),
            "ticket_count": grp.total_tickets,
            "invoiced_tickets": grp.invoiced_tickets,
            "open_tickets": grp.open_tickets,
            "confirmed_cost": round(grp.confirmed_cost_aud, 2),
            "pending_amount": 0.0,
            "states": state_rows_for_week,
            "top_repairers": top_repairers_for_week,
        })

    return repairer_rows, state_rows, weekly_rows, detail_rows, state_rows  # last one placeholder, unused


# ---------------------------------------------------------------------------
# JSON writer
# ---------------------------------------------------------------------------

def write_json(
    output_dir: Path,
    stats: Dict[str, Any],
    repairer_rows: List[Dict[str, Any]],
    state_rows: List[Dict[str, Any]],
    weekly_rows: List[Dict[str, Any]],
    detail_rows: List[Dict[str, Any]],
    cny_to_aud_rate: float,
) -> Path:
    total_confirmed = round(sum(r["confirmed_cost_aud"] for r in detail_rows), 2)
    invoiced_tickets = sum(1 for r in detail_rows if r["invoice_status"] == "invoiced")
    open_tickets = sum(1 for r in detail_rows if r["invoice_status"] == "open")
    avg_confirmed = round(total_confirmed / len(detail_rows), 2) if detail_rows else 0.0
    unique_repairers_raw = len({clean(r.get("raw_repairer_name")) for r in detail_rows if clean(r.get("raw_repairer_name"))})
    unique_repairers_normalized = len({clean(r.get("normalized_key")) for r in detail_rows if clean(r.get("normalized_key"))})

    repairer_display_map = {r["split_key"]: r["repairer_name"] for r in repairer_rows}
    address_buckets: Dict[str, Dict[str, Any]] = {}
    variant_rows: List[Dict[str, Any]] = []
    for row in detail_rows:
        address = clean(row.get("address_group")) or "Unknown"
        bucket = address_buckets.setdefault(address, {
            "address_group": address,
            "ticket_count": 0,
            "total_warranty_cost": 0.0,
            "state_counter": Counter(),
            "repairer_counter": Counter(),
            "dealer_counter": Counter(),
        })
        bucket["ticket_count"] += 1
        bucket["total_warranty_cost"] += float(row.get("confirmed_cost_aud") or 0.0)
        bucket["state_counter"][clean(row.get("state")) or "Unknown"] += 1
        bucket["repairer_counter"][clean(row.get("repairer_split_key")) or "Unknown"] += 1
        dealer_name = meaningful_text(row.get("Dealer Name")) or meaningful_text(row.get("Dealer")) or "Unknown"
        bucket["dealer_counter"][dealer_name] += 1

        variant_rows.append({
            "raw_repairer_name": clean(row.get("raw_repairer_name")) or clean(row.get("Service Technician")) or "",
            "normalized_key": clean(row.get("normalized_key")) or "",
            "state": clean(row.get("state")) or "",
            "state_source": clean(row.get("state_source")) or "",
            "address_group": address,
            "dealer_name": dealer_name,
            "dealer_code": clean(row.get("Dealer")) or "",
            "country_region": clean(row.get("Country/Region")) or "",
            "postal_code": clean(row.get("Service Requester Postal Code")) or "",
            "ticket_id": clean(row.get("Ticket ID")) or clean(row.get("TicketID")) or "",
            "created_on": clean(row.get("Created On")) or "",
            "status": clean(row.get("Status")) or "",
            "claim_total_amount": float(row.get("ClaimTotalAmount") or 0.0),
        })

    address_rows: List[Dict[str, Any]] = []
    for bucket in address_buckets.values():
        ticket_count = bucket["ticket_count"]
        total_cost = round(bucket["total_warranty_cost"], 2)
        top_state = bucket["state_counter"].most_common(1)[0][0] if bucket["state_counter"] else ""
        top_repairer_key = bucket["repairer_counter"].most_common(1)[0][0] if bucket["repairer_counter"] else ""
        top_dealer = bucket["dealer_counter"].most_common(1)[0][0] if bucket["dealer_counter"] else ""
        address_rows.append({
            "address_group": bucket["address_group"],
            "ticket_count": ticket_count,
            "total_warranty_cost": total_cost,
            "avg_warranty_cost": round(total_cost / ticket_count, 2) if ticket_count else 0.0,
            "unique_repairers": len(bucket["repairer_counter"]),
            "top_state": top_state,
            "top_repairer": repairer_display_map.get(top_repairer_key, top_repairer_key),
            "top_dealer_name": top_dealer,
        })
    address_rows.sort(key=lambda r: (-r["ticket_count"], -r["total_warranty_cost"], r["address_group"]))

    payload = {
        "meta": {
            **stats,
            "cny_to_aud_rate": cny_to_aud_rate,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "unique_repairers_raw": unique_repairers_raw,
            "unique_repairers_normalized": unique_repairers_normalized,
            "unique_addresses": len(address_rows),
        },
        "summary": {
            "total_tickets": len(detail_rows),
            "invoiced_tickets": invoiced_tickets,
            "open_tickets": open_tickets,
            "confirmed_cost": total_confirmed,
            "total_warranty_cost": total_confirmed,
            "pending_amount": 0.0,
            "avg_confirmed_cost": avg_confirmed,
            "avg_warranty_cost": avg_confirmed,
            "unique_repairers": len(repairer_rows),
            "unique_repairers_raw": unique_repairers_raw,
            "unique_repairers_normalized": unique_repairers_normalized,
            "unique_states": len(state_rows),
            "unique_addresses": len(address_rows),
            "unique_weeks": len(weekly_rows),
            "top_repairers": repairer_rows[:20],
        },
        "repairers": repairer_rows,
        "addresses": address_rows,
        "states": state_rows,
        "weekly": weekly_rows,
        "variants": variant_rows,
        "details": detail_rows,
    }
    path = output_dir / "repairers_2026_data.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_js(output_dir: Path, payload_path: Path) -> Path:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    js_path = output_dir / "repairers_2026_data.js"
    js_path.write_text(
        "globalThis.REPAIRERS_2026_ANALYSIS = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )
    return js_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract repairers and enrich with SAP PO cost and invoice status.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Source SAP CSV file")
    parser.add_argument("--year", type=int, default=2025, help="Earliest approved/posting year to keep")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--cny-to-aud", type=float, default=DEFAULT_CNY_TO_AUD, help="CNY->AUD divisor for PO currency conversion")
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="SAP HANA ODBC DSN string")
    parser.add_argument("--mandt", default=DEFAULT_MANDT, help="SAP client (default 800)")
    parser.add_argument("--skip-invoice", action="store_true", help="Skip SAP invoice enrichment (all tickets = open)")
    parser.add_argument("--skip-firebase", action="store_true", help="Skip Firebase RepairerBusinessNameID overlay (use only CSV Service Technician for names)")
    parser.add_argument("--firebase-root", default=FIREBASE_ROOT, help=f"Firebase root node (default: {FIREBASE_ROOT})")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    source = Path(args.source)
    if not source.exists() and source == DEFAULT_SOURCE and LEGACY_DEFAULT_SOURCE.exists():
        source = LEGACY_DEFAULT_SOURCE
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Reading CSV: %s", source)
    rows, stats = read_rows(source, args.year)
    logger.info("Rows kept: %s (skipped: unassigned=%s, customer_like=%s, before_year=%s, no_date=%s)",
                stats["rows_kept"], stats["rows_skipped_unassigned_repairer"],
                stats["rows_skipped_customer_like_repairer"], stats["rows_skipped_before_year"],
                stats["rows_skipped_no_date"])

    po_numbers = [clean(r.get("ERP Purchase Order ID")) for r in rows]
    po_numbers = [p for p in po_numbers if p]
    unique_pos = len(set(po_numbers))
    logger.info("Unique POs to check for cost and invoice status: %s", unique_pos)

    po_cost_map = fetch_po_costs(po_numbers, dsn=args.dsn, mandt=args.mandt)
    logger.info("Found PO cost rows for %s / %s unique POs", len(po_cost_map), unique_pos)

    if args.skip_invoice:
        logger.warning("--skip-invoice set: all tickets will be marked open.")
        invoice_map: Dict[str, Dict[str, Any]] = {}
    else:
        invoice_map = fetch_invoice_status(po_numbers, dsn=args.dsn, mandt=args.mandt)
        logger.info("Found invoices for %s / %s unique POs", len(invoice_map), unique_pos)

    if args.skip_firebase:
        logger.warning("--skip-firebase set: repairer names will be CSV Service Technician only")
        firebase_name_map: Dict[str, str] = {}
    else:
        firebase_name_map = load_firebase_repairer_names(root=args.firebase_root)

    repairer_rows, state_rows, weekly_rows, detail_rows, _ = build_summaries(
        rows, po_cost_map, invoice_map, args.cny_to_aud, firebase_name_map=firebase_name_map,
    )

    json_path = write_json(output_dir, stats, repairer_rows, state_rows, weekly_rows, detail_rows, args.cny_to_aud)
    js_path = write_js(output_dir, json_path)

    summary = {
        "source": str(source),
        "year": args.year,
        "cny_to_aud_rate": args.cny_to_aud,
        "tickets_kept": len(detail_rows),
        "invoiced_tickets": sum(1 for r in detail_rows if r["invoice_status"] == "invoiced"),
        "open_tickets": sum(1 for r in detail_rows if r["invoice_status"] == "open"),
        "unique_pos": unique_pos,
        "unique_cost_pos": len(po_cost_map),
        "unique_invoiced_pos": len(invoice_map),
        "unique_repairers": len(repairer_rows),
        "unique_states": len(state_rows),
        "unique_weeks": len(weekly_rows),
        "confirmed_cost_aud": round(sum(r["confirmed_cost_aud"] for r in detail_rows), 2),
        "pending_amount_aud": 0.0,
        "state_breakdown": {r["state"]: r["ticket_count"] for r in state_rows},
        "firebase_overlay_count": len(firebase_name_map),
        "firebase_overlay_used": sum(1 for r in detail_rows if r.get("RepairerBusinessNameID")),
        "output_json": str(json_path),
        "output_js": str(js_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
