# -*- coding: utf-8 -*-
"""
CTM V44 HISTORY-SAFE 1-HOUR MONITOR

Purpose:
- Keep Critical Status Change Log permanently.
- Normal hourly runs NEVER delete /ctmTicketStatusMonitorV44/history.
- Unprocessed rows stay in Firebase until you manually mark them processed in the webpage.
- Baseline/currentStatus can be refreshed without clearing historical logs.

What this file does:
1) RESET BASELINE:
   - Rebuilds /ctmTicketStatusMonitorV44/currentStatus from current Firebase tickets.
   - Creates ZERO change logs.
   - DOES NOT delete /history unless you explicitly pass --clear-history-on-reset.

2) NORMAL RUN:
   - Runs the original company fetch .py first.
   - Then reads Firebase.
   - Compares current critical status with /ctmTicketStatusMonitorV44/currentStatus.
   - Only real changes are PATCHED/appended to /ctmTicketStatusMonitorV44/history.

3) AUTO RUN:
   - Repeats NORMAL RUN every 1 hour when --interval-hours 1 is used.

This script never adds child nodes inside original ticket records.
It only writes under /ctmTicketStatusMonitorV44.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Iterable

import firebase_admin
from firebase_admin import credentials, db


DEFAULT_DB_URL = "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app"
DEFAULT_SOURCE_ROOT = "c4cTickets_test"
DEFAULT_MONITOR_ROOT = "ctmTicketStatusMonitorV44"
DEFAULT_COMPANY_FILE = "fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py"

CRITICAL_FALLBACK_CODES = {"Y6", "YA", "YC", "Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "ZM", "ZR", "ZV"}
DASHBOARD_MIN_DATE = "2026-05-25"

SIGNATURE_FIELDS = [
    "TicketStatus", "TicketStatusText", "TicketSeverity",
    "Responded", "AmountIncludingTax",
    "ApprovalDate", "ApprovalNumber",
    "ERPInvoiceNumber", "ERPPurchaseOrder", "ERPFreeOrder",
    "Sales Order", "SO Created Date",
    "Issue Status", "Order Rejection Status",
    "TicketName", "TicketType", "TicketTypeText",
    "DealerID", "DealerName", "WarrantyHandlingDealerID",
    "RepairerBusinessNameID", "RepairerEmail", "RepairerPhoneNumber",
    "RepairerNamePointOfContact",
    "ServiceRequesterEmail", "Z1Z8TimeConsumed",
    "ChassisNumber", "SerialID",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def safe_key(v: Any) -> str:
    s = clean(v)
    for ch in [".", "$", "#", "[", "]", "/"]:
        s = s.replace(ch, "_")
    return s[:180]


def parse_amount(v: Any) -> float:
    try:
        return float(str(v or "0").replace(",", "").replace("$", "").strip() or 0)
    except Exception:
        return 0.0


def stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def parse_time_consumed_minutes(v: Any) -> int:
    """Parse C4C Z1Z8TimeConsumed such as '131 D 17 H 32 M' into minutes.

    Business rule: approved = Z1Z8TimeConsumed parses to totalMinutes > 0;
    unapproved = empty or <= 0.
    """
    raw = clean(v)
    if not raw:
        return 0
    # Plain numeric values are treated as minutes.
    try:
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw):
            return max(0, int(float(raw)))
    except Exception:
        pass
    total = 0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([DdHhMm])", raw):
        n = float(num)
        u = unit.lower()
        if u == "d":
            total += int(n * 24 * 60)
        elif u == "h":
            total += int(n * 60)
        elif u == "m":
            total += int(n)
    return max(0, total)


def is_approved_by_z1z8(ticket_or_snapshot: Dict[str, Any]) -> bool:
    return parse_time_consumed_minutes(ticket_or_snapshot.get("Z1Z8TimeConsumed") or ticket_or_snapshot.get("z1z8TimeConsumed")) > 0


def init_firebase(db_url: str, sa_path: str) -> None:
    if firebase_admin._apps:
        return
    p = Path(sa_path)
    if not p.exists():
        raise SystemExit(f"Firebase service account json not found:\n{p}")
    firebase_admin.initialize_app(credentials.Certificate(str(p)), {"databaseURL": db_url})


def normalize_row(row: Any, fallback_key: str = "") -> tuple[Dict[str, Any], Dict[str, Any], str]:
    raw = row if isinstance(row, dict) else {}
    ticket = raw.get("ticket") if isinstance(raw.get("ticket"), dict) else raw
    roles = raw.get("roles") if isinstance(raw.get("roles"), dict) else {}
    tid = clean(ticket.get("TicketID") or ticket.get("ticketID") or ticket.get("id") or fallback_key)
    return ticket, roles, tid



# ===== Pre-calculation helper functions for dashboard analytics =====
def field_norm_key(v: Any) -> str:
    return clean(v).lower().replace(" ", "").replace("_", "").replace("-", "").replace("/", "").replace("(", "").replace(")", "")


def get_field(obj: Any, candidates: Iterable[str]) -> str:
    if not isinstance(obj, dict):
        return ""
    idx = {field_norm_key(k): k for k in obj.keys()}
    for c in candidates:
        if c in obj and clean(obj.get(c)):
            return clean(obj.get(c))
        k = idx.get(field_norm_key(c))
        if k and clean(obj.get(k)):
            return clean(obj.get(k))
    return ""


def parse_date_any(v: Any) -> Optional[datetime]:
    if v is None or clean(v) == "":
        return None
    if isinstance(v, (int, float)):
        try:
            # Firebase timestamps may be milliseconds.
            if v > 10_000_000_000:
                return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except Exception:
            return None
    s = clean(v)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y%m%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        # Handles 2026-05-28T10:20:30+00:00 and 2026-05-28T10:20:30Z
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def date_key(v: Any) -> str:
    d = parse_date_any(v)
    return d.date().isoformat() if d else ""


def normalized_claim_type(v: Any) -> str:
    s = clean(v).lower().replace("_", " ").replace("-", " ")
    s = " ".join(s.split())
    if not s:
        return ""
    if "pre delivery" in s or "predelivery" in s or ("pre" in s and "delivery" in s):
        return "Pre Delivery Warranty Claims"
    if "in field" in s or "field warranty" in s or ("field" in s and "warranty" in s):
        return "In Field Warranty Claims"
    return clean(v)


def ticket_claim_type(ticket: Dict[str, Any]) -> str:
    raw = get_field(ticket, [
        "TicketTypeText", "Ticket Type Text",
        "TicketType", "Ticket Type",
        "ClaimType", "Claim Type",
    ])
    return normalized_claim_type(raw) or "In Field Warranty Claims"


def find_involved_party_name_by_role(node: Any, role_id: str) -> str:
    """Find C4C involved party name from either roles/40 or roles/1/40 style trees."""
    if not isinstance(node, (dict, list)):
        return ""
    if isinstance(node, list):
        for item in node:
            name = find_involved_party_name_by_role(item, role_id)
            if name:
                return name
        return ""

    if clean(node.get("InvolvedPartyRoleID")) == role_id and clean(node.get("InvolvedPartyName")):
        return clean(node.get("InvolvedPartyName"))

    direct = node.get(role_id)
    if isinstance(direct, dict) and clean(direct.get("InvolvedPartyName")):
        return clean(direct.get("InvolvedPartyName"))
    if isinstance(direct, (dict, list)):
        name = find_involved_party_name_by_role(direct, role_id)
        if name:
            return name

    for v in node.values():
        name = find_involved_party_name_by_role(v, role_id)
        if name:
            return name
    return ""


ASSIGNED_TO_FIELD_CANDIDATES = [
    "AssignedToRaw",
    "Assigned to", "Assigned To", "AssignedTo", "AssignedToName", "Assigned To Name",
    "Assignee", "AssignedUser", "Assigned User", "OwnerPartyName",
]


def assigned_to_queue_value(ticket: Dict[str, Any]) -> str:
    """Return the raw ticket-level C4C Assigned To value when it exists.

    Important business rule:
    - roles/40/InvolvedPartyName is the real Assign To employee for this dashboard.
    - ticket-level Assigned To can contain Queue Warranty / queue ids and can appear
      on many non-Z1 statuses. It must NOT be treated as an employee mapping.
    - Queue Warranty should only be used as a visible workload bucket for Z1 New Claim
      records where role 40 is still blank.
    """
    return get_field(ticket, ASSIGNED_TO_FIELD_CANDIDATES)


def is_new_claim_status(ticket: Dict[str, Any]) -> bool:
    code = clean(ticket.get("TicketStatus") or ticket.get("statusCode") or ticket.get("Status"))
    text = clean(ticket.get("TicketStatusText") or ticket.get("statusText")).lower()
    return code == "Z1" or "new claim" in text


def employee_from_ticket(ticket: Dict[str, Any], roles: Dict[str, Any]) -> str:
    # Source of truth: C4C role 40 = Assign To. InvolvedPartyName is the real
    # employee owner. Do not use ticket-level Assigned To as an employee name.
    role40_name = find_involved_party_name_by_role(roles, "40")
    if role40_name:
        return clean(role40_name)

    # Only brand-new claims waiting for assignment should be shown as the
    # Queue Warranty workload bucket. Queue Warranty on other statuses is not
    # enough to classify the ticket as Queue Warranty.
    if is_new_claim_status(ticket):
        return "Queue Warranty"

    # Non-Z1 critical tickets without role 40 are an audit bucket. They still
    # count toward the blue-bar reconciliation, but they are not a real employee.
    return "Missing role 40"




def normalized_employee_name(name: Any) -> str:
    return " ".join(clean(name).lower().split())


def is_excluded_employee_name(name: Any) -> bool:
    """Non-workload names that should be hidden from employee analytics.

    Important: C4C role 40 / Assign To can legitimately be "Queue Warranty"
    for newly-created Z1 New Claim tickets waiting for assignment. That is still
    the Assign To value from C4C, so it MUST be shown as its own workload bucket
    instead of being excluded or treated as Not Assigned.
    """
    s = normalized_employee_name(name)
    if not s:
        return False
    if s in {"admin", "admin a b.a.", "admin a b.a"}:
        return True
    if s.startswith("admin"):
        return True
    if any(x in s for x in ["test", "demo", "dummy", "sample"]):
        return True
    return False


def is_not_assigned_employee_name(name: Any) -> bool:
    """Only genuinely missing/unknown owners count as Not Assigned."""
    s = normalized_employee_name(name)
    if not s:
        return True
    if s in {"unknown", "undefined", "null", "n/a", "na", "none", "unassigned", "not assigned"}:
        return True
    if "unknown" in s:
        return True
    return False


def is_real_employee_name(name: Any) -> bool:
    return (not is_excluded_employee_name(name)) and (not is_not_assigned_employee_name(name))

def history_object_to_list(obj: Any) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []

    def walk(node: Any, path: list[str]) -> None:
        if not isinstance(node, dict):
            return
        looks_like_event = any(clean(node.get(k)) for k in [
            "detectedAt", "createdAt", "time", "timestamp", "dataSyncAt",
            "type", "changeType", "fromStatus", "toStatus", "oldStatus", "newStatus",
        ])
        if looks_like_event:
            event = dict(node)
            event["_key"] = "/".join(path)
            out.append(event)
            return
        for k, v in node.items():
            walk(v, path + [clean(k)])

    walk(obj, [])
    return out


def dedupe_events(events: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    seen: set[str] = set()
    out: list[Dict[str, Any]] = []
    for e in events:
        tid = clean(e.get("id") or e.get("ticketId") or e.get("ticketID") or e.get("TicketID") or e.get("localTicketId"))
        key = "|".join([
            tid,
            clean(e.get("type") or e.get("changeType") or e.get("eventType")),
            clean(e.get("detectedAt") or e.get("createdAt") or e.get("time") or e.get("timestamp") or e.get("dataSyncAt")),
            clean(e.get("fromCode") or e.get("oldCode")),
            clean(e.get("toCode") or e.get("newCode")),
        ])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def event_type(e: Dict[str, Any]) -> str:
    t = clean(e.get("type") or e.get("changeType") or e.get("eventType")).lower()
    from_s = clean(e.get("fromStatus") or e.get("oldStatus") or e.get("fromStatusText") or e.get("oldStatusText"))
    to_s = clean(e.get("toStatus") or e.get("newStatus") or e.get("toStatusText") or e.get("newStatusText"))
    if "enter" in t:
        return "entered"
    if "exit" in t or "removed" in t:
        return "exited"
    if "move" in t or "changed" in t or from_s or to_s:
        return "moved"
    return ""


def event_ticket_id(e: Dict[str, Any]) -> str:
    return clean(e.get("id") or e.get("ticketId") or e.get("ticketID") or e.get("TicketID") or e.get("localTicketId"))


def event_date_key(e: Dict[str, Any]) -> str:
    return date_key(e.get("detectedAt") or e.get("createdAt") or e.get("time") or e.get("timestamp") or e.get("dataSyncAt"))


def event_time(e: Dict[str, Any]) -> str:
    return clean(e.get("detedAt") or e.get("detectedAt") or e.get("createdAt") or e.get("time") or e.get("timestamp") or e.get("dataSyncAt"))


def event_employee_from_snapshot(e: Dict[str, Any], ticket_by_id: Dict[str, Dict[str, Any]]) -> str:
    """Resolve employee for a history event.

    Correct rule:
    1) Use the current ticket snapshot built from roles/40/InvolvedPartyName.
       This is the documented C4C Assign To employee source.
    2) If role 40 is empty and current ticket is Z1/New Claim, snapshot employee is Queue Warranty.
    3) If role 40 is empty and not Z1/New Claim, snapshot employee is Missing role 40.
    4) Only fall back to old event fields when the ticket cannot be found.
    """
    tid = event_ticket_id(e)
    t = ticket_by_id.get(tid, {}) if tid else {}
    if isinstance(t, dict):
        emp = clean(t.get("employee"))
        if emp and not is_not_assigned_employee_name(emp):
            return emp

    return clean(
        e.get("employee")
        or e.get("role40Employee")
        or e.get("assignedTo")
        or e.get("AssignedTo")
        or e.get("Assigned to")
        or e.get("Assigned To")
        or "Unknown"
    )


def make_unmapped_removed_event_row(e: Dict[str, Any], ticket: Dict[str, Any], reason: str) -> Dict[str, Any]:
    tid = event_ticket_id(e)
    return {
        "id": tid,
        "ticketId": tid,
        "detectedAt": event_time(e),
        "date": event_date_key(e),
        "reason": reason,
        "employeeRaw": clean(e.get("employee") or e.get("assignedTo") or e.get("AssignedTo") or e.get("Assigned to") or e.get("Assigned To")),
        "employeeFromRole40": clean(ticket.get("role40Employee") if isinstance(ticket, dict) else ""),
        "assignedToRaw": clean(ticket.get("assignedToRaw") if isinstance(ticket, dict) else ""),
        "resolvedEmployee": clean(ticket.get("employee") if isinstance(ticket, dict) else ""),
        "dealer": clean((ticket or {}).get("dealer") or e.get("dealer")),
        "claimType": clean((ticket or {}).get("claimType") or e.get("claimType") or e.get("TicketTypeText")),
        "fromCode": clean(e.get("fromCode") or e.get("oldCode")),
        "fromStatus": clean(e.get("fromStatus") or e.get("oldStatus")),
        "toCode": clean(e.get("toCode") or e.get("newCode")),
        "toStatus": clean(e.get("toStatus") or e.get("newStatus")),
        "currentStatus": clean((ticket or {}).get("statusText")),
        "customer": clean((ticket or {}).get("name") or e.get("name")),
        "created": clean((ticket or {}).get("created") or e.get("created")),
        "amount": (ticket or {}).get("amount", e.get("amount", 0)),
    }

def load_mapping() -> Dict[str, Any]:
    m = db.reference("ticketStatusMapping").get() or {}
    return m if isinstance(m, dict) else {}


def critical_codes_from_mapping(mapping: Dict[str, Any]) -> set[str]:
    codes = set()
    for code, val in (mapping or {}).items():
        if isinstance(val, dict) and clean(val.get("firstLevelStatus")).lower() == "critical":
            codes.add(clean(code))
    return codes or set(CRITICAL_FALLBACK_CODES)


def status_text_for(code: str, ticket_text: str, mapping: Dict[str, Any]) -> str:
    m = mapping.get(code) if isinstance(mapping, dict) else None
    if isinstance(m, dict):
        return clean(ticket_text or m.get("ticketStatusText") or m.get("statusText") or code)
    return clean(ticket_text or code)


def is_critical(ticket: Dict[str, Any], mapping: Dict[str, Any]) -> bool:
    code = clean(ticket.get("TicketStatus") or ticket.get("statusCode") or ticket.get("Status"))
    codes = critical_codes_from_mapping(mapping)
    if code in codes:
        return True

    sev = clean(ticket.get("TicketSeverity")).lower()
    txt = clean(ticket.get("TicketStatusText")).lower()
    if sev == "critical":
        return True
    if txt == "critical":
        return True
    return False


def mapped_dealer(ticket: Dict[str, Any], roles: Dict[str, Any]) -> str:
    # Same priority as the original monitor logic, with recursive role lookup added
    # only so analytics can read both roles/1001 and roles/1/1001 structures.
    return clean(
        ticket.get("DealerName")
        or ticket.get("WarrantyHandlingDealerName")
        or (roles.get("1001") or {}).get("InvolvedPartyName")
        or find_involved_party_name_by_role(roles, "1001")
        or "Unknown"
    )


def build_snapshot(source_root: str) -> Dict[str, Dict[str, Any]]:
    mapping = load_mapping()
    root = db.reference(f"{source_root}/tickets").get() or {}
    if isinstance(root, list):
        entries = [(str(i), x) for i, x in enumerate(root) if x]
    elif isinstance(root, dict):
        entries = list(root.items())
    else:
        entries = []

    snap: Dict[str, Dict[str, Any]] = {}

    for key, row in entries:
        ticket, roles, tid = normalize_row(row, key)
        if not tid or not isinstance(ticket, dict):
            continue

        code = clean(ticket.get("TicketStatus") or ticket.get("statusCode") or ticket.get("Status"))
        text = status_text_for(code, clean(ticket.get("TicketStatusText") or ticket.get("statusText")), mapping)
        critical = is_critical(ticket, mapping)

        sig_obj = {k: ticket.get(k) for k in SIGNATURE_FIELDS if k in ticket}
        sig = stable_hash(sig_obj)

        snap[safe_key(tid)] = {
            "id": clean(tid),
            "code": code,
            "statusCode": code,
            "statusText": text,
            "isCritical": bool(critical),
            "signature": sig,
            "dealer": mapped_dealer(ticket, roles),
            "dealerId": clean(ticket.get("DealerID") or ticket.get("WarrantyHandlingDealerID")),
            "employee": employee_from_ticket(ticket, roles),
            "role40Employee": find_involved_party_name_by_role(roles, "40"),
            "assignedToRaw": assigned_to_queue_value(ticket),
            "claimType": ticket_claim_type(ticket),
            "name": clean(ticket.get("TicketName")),
            "amount": parse_amount(ticket.get("AmountIncludingTax")),
            "z1z8TimeConsumed": clean(ticket.get("Z1Z8TimeConsumed")),
            "isApproved": is_approved_by_z1z8(ticket),
            "isUnapproved": not is_approved_by_z1z8(ticket),
            "created": ticket.get("CreatedOn") or ticket.get("createdOn") or ticket.get("CreatedAt") or "",
            "chassis": clean(ticket.get("ChassisNumber")),
            "serial": clean(ticket.get("SerialID")),
            "lastSeenAt": now_iso(),
        }

    return snap



# ===== Dealer / Employee / Team analytics pre-calculation =====
# Dealer workspace pre-calculation config copied from Dealer Workbench.
CONFIG_ALLOWED_DEALER_DISPLAYS = set(['ABCO', 'Auswide', 'Bendigo', 'Bundaberg', 'CMG Campers', 'Caravans WA', 'Christchurch', 'Dario', 'Destiny RV', 'Forest Glen', 'Frankston', 'Geelong', 'Gympie', 'Heatherbrae', 'Launceston', 'Marsden Point', 'MotorHub', 'Newcastle Caravans & RV', 'ST James', 'Slacks Creek', 'Toowoomba', 'Townsville', 'Traralgon', 'Vanari', 'Wangaratta', 'Warrnambool', 'darwin-caravans', 'newcastle-rv', 'pending', 'snowy-stock'])
CONFIG_ID_TO_DISPLAY = {'204673': 'ABCO', '204669': 'Auswide', '201223': 'Bendigo', '3138': 'Bundaberg', '505014': 'Caravans WA', 'Christchurch': 'Christchurch', '204680': 'CMG Campers', 'Dario': 'Dario', 'darwin-caravans': 'darwin-caravans', '503257': 'Destiny RV', '204642': 'Forest Glen', '3141': 'Frankston', '3128': 'Geelong', '3137': 'Gympie', '200035': 'Heatherbrae', '3126': 'Launceston', '204679': 'Marsden Point', 'morisset': 'Heatherbrae', '505491': 'MotorHub', '503201': 'Newcastle Caravans & RV', 'newcastle-rv': 'newcastle-rv', 'pending': 'pending', '204670': 'Slacks Creek', 'snowy-stock': 'snowy-stock', '3121': 'ST James', '3135': 'Toowoomba', '204677': 'Townsville', '3123': 'Traralgon', '504620': 'Wangaratta', '204025': 'Warrnambool', '204645': 'ST James', '204672': 'Traralgon', '3140': 'Frankston', '3151': 'Frankston', '204678': 'Frankston', '211197': 'Frankston', '3133': 'Heatherbrae', '3134': 'Heatherbrae', '204647': 'Heatherbrae', '204661': 'Heatherbrae', '204646': 'Heatherbrae', '204960': 'Bendigo', '204961': 'Warrnambool', '506060': 'Christchurch', '202933': 'CMG Campers', '505490': 'MotorHub'}
CONFIG_ALIAS_TO_DISPLAY = {'abco': 'ABCO', 'ABCO': 'ABCO', '204673': 'ABCO', 'bl9npa': 'ABCO', 'auswide': 'Auswide', 'Auswide': 'Auswide', '204669': 'Auswide', 'rza9mg': 'Auswide', 'bendigo': 'Bendigo', 'Bendigo': 'Bendigo', '201223': 'Bendigo', 'dv4k3q': 'Bendigo', 'bundaberg': 'Bundaberg', 'Bundaberg': 'Bundaberg', '3138': 'Bundaberg', 'ab0lfd': 'Bundaberg', 'caravans-wa': 'Caravans WA', 'Caravans WA': 'Caravans WA', 'caravans wa': 'Caravans WA', '505014': 'Caravans WA', 'dwsk74': 'Caravans WA', 'christchurch': 'Christchurch', 'Christchurch': 'Christchurch', 'fomb53': 'Christchurch', 'cmg-campers': 'CMG Campers', 'CMG Campers': 'CMG Campers', 'cmg campers': 'CMG Campers', '204680': 'CMG Campers', 'xuwmrl': 'CMG Campers', 'dario': 'Dario', 'Dario': 'Dario', 'miqysu': 'Dario', 'darwin-caravans': 'darwin-caravans', 'DARWIN_CARAVANS': 'darwin-caravans', 'darwin_caravans': 'darwin-caravans', 'destiny-rv': 'Destiny RV', 'Destiny RV': 'Destiny RV', 'destiny rv': 'Destiny RV', '503257': 'Destiny RV', 't9v3hq': 'Destiny RV', 'forest-glen': 'Forest Glen', 'Forest Glen': 'Forest Glen', 'forest glen': 'Forest Glen', '204642': 'Forest Glen', 'vttyl4': 'Forest Glen', 'frankston': 'Frankston', 'Frankston': 'Frankston', '3141': 'Frankston', '6jtjp0': 'Frankston', 'geelong': 'Geelong', 'Geelong': 'Geelong', '3128': 'Geelong', 'rhlh5x': 'Geelong', 'gympie': 'Gympie', 'Gympie': 'Gympie', '3137': 'Gympie', 'vvty3d': 'Gympie', 'heatherbrae': 'Heatherbrae', 'Heatherbrae': 'Heatherbrae', '200035': 'Heatherbrae', 'qdhnig': 'Heatherbrae', 'launceston': 'Launceston', 'Launceston': 'Launceston', '3126': 'Launceston', 'f73sk0': 'Launceston', 'marsden-point': 'Marsden Point', 'Marsden Point': 'Marsden Point', 'marsden point': 'Marsden Point', '204679': 'Marsden Point', '9euw8r': 'Marsden Point', 'morisset': 'Heatherbrae', 'MORISSET': 'Heatherbrae', 'motorhub': 'MotorHub', 'MotorHub': 'MotorHub', '505491': 'MotorHub', 'g8j57g': 'MotorHub', 'newcastle-caravans-rv': 'Newcastle Caravans & RV', 'Newcastle Caravans & RV': 'Newcastle Caravans & RV', 'newcastle caravans & rv': 'Newcastle Caravans & RV', '503201': 'Newcastle Caravans & RV', 'jpf7g8': 'Newcastle Caravans & RV', 'newcastle-rv': 'newcastle-rv', 'NEWCASTLE_RV': 'newcastle-rv', 'newcastle_rv': 'newcastle-rv', 'pending': 'pending', 'PENDING': 'pending', 'slacks-creek': 'Slacks Creek', 'Slacks Creek': 'Slacks Creek', 'slacks creek': 'Slacks Creek', '204670': 'Slacks Creek', 'txffxh': 'Slacks Creek', 'snowy-stock': 'snowy-stock', 'SNOWY_STOCK': 'snowy-stock', 'snowy_stock': 'snowy-stock', 'st-james': 'ST James', 'ST James': 'ST James', 'st james': 'ST James', '3121': 'ST James', '0yeqb3': 'ST James', 'toowoomba': 'Toowoomba', 'Toowoomba': 'Toowoomba', '3135': 'Toowoomba', '5m5wtx': 'Toowoomba', 'townsville': 'Townsville', 'Townsville': 'Townsville', '204677': 'Townsville', 'j57yym': 'Townsville', 'traralgon': 'Traralgon', 'Traralgon': 'Traralgon', '3123': 'Traralgon', 'trb2ep': 'Traralgon', 'vanari': 'Vanari', 'Vanari': 'Vanari', 'xyo34i': 'Vanari', 'wangaratta': 'Wangaratta', 'Wangaratta': 'Wangaratta', '504620': 'Wangaratta', 'a3g1g9': 'Wangaratta', 'warrnambool': 'Warrnambool', 'Warrnambool': 'Warrnambool', '204025': 'Warrnambool', 'j4xbu7': 'Warrnambool', '204645': 'ST James', '204672': 'Traralgon', '3140': 'Frankston', '3151': 'Frankston', '204678': 'Frankston', '211197': 'Frankston', '3133': 'Heatherbrae', '3134': 'Heatherbrae', '204647': 'Heatherbrae', '204661': 'Heatherbrae', '204646': 'Heatherbrae', '204960': 'Bendigo', '204961': 'Warrnambool', '506060': 'Christchurch', '202933': 'CMG Campers', '505490': 'MotorHub', 'NEWGEN Caravan - Morisset': 'Heatherbrae', 'newgen caravan - morisset': 'Heatherbrae', 'newgen caravan-morisset': 'Heatherbrae', 'NEWGEN Caravan- Morisset': 'Heatherbrae', 'newgen caravan- morisset': 'Heatherbrae', 'Newgen Caravan Morisset': 'Heatherbrae', 'newgen caravan morisset': 'Heatherbrae', 'Leisure Lion Pty Ltd - Morisset': 'Heatherbrae', 'leisure lion pty ltd - morisset': 'Heatherbrae', 'leisure lion pty ltd-morisset': 'Heatherbrae', 'Leisure Lion Pty Ltd-Morisset': 'Heatherbrae', 'Leisure Lion - New Castle': 'Heatherbrae', 'leisure lion - new castle': 'Heatherbrae', 'leisure lion-new castle': 'Heatherbrae', 'Leisure Lion-New Castle': 'Heatherbrae', 'Leisure Lion - Newcastle': 'Heatherbrae', 'leisure lion - newcastle': 'Heatherbrae', 'leisure lion-newcastle': 'Heatherbrae', 'Leisure Lion-Newcastle': 'Heatherbrae', 'Lesiure Lion - New Castle': 'Heatherbrae', 'lesiure lion - new castle': 'Heatherbrae', 'lesiure lion-new castle': 'Heatherbrae', 'Lesiure Lion-New Castle': 'Heatherbrae', 'Lesiure Lion - Newcastle': 'Heatherbrae', 'lesiure lion - newcastle': 'Heatherbrae', 'lesiure lion-newcastle': 'Heatherbrae', 'Lesiure Lion-Newcastle': 'Heatherbrae', 'LEISURE LION - NEW CASTLE': 'Heatherbrae', 'The Caravan Hub - Townsville': 'Heatherbrae', 'the caravan hub - townsville': 'Heatherbrae', 'the caravan hub-townsville': 'Heatherbrae', 'THE CARAVAN HUB': 'Heatherbrae', 'the caravan hub': 'Heatherbrae', 'THE CARAVAN HUB Repairs': 'Heatherbrae', 'the caravan hub repairs': 'Heatherbrae', 'Newcastle RV Super Centre - Beresfield': 'Heatherbrae', 'newcastle rv super centre - beresfield': 'Heatherbrae', 'newcastle rv super centre-beresfield': 'Heatherbrae', 'Newcastle RV Super Centre - Berefield': 'Heatherbrae', 'newcastle rv super centre - berefield': 'Heatherbrae', 'newcastle rv super centre-berefield': 'Heatherbrae', 'NewcastleRV': 'Newcastle Caravans & RV', 'newcastlerv': 'Newcastle Caravans & RV', 'NEWCASTLE CARAVANS & RVS': 'Newcastle Caravans & RV', 'newcastle caravans & rvs': 'Newcastle Caravans & RV', 'Newcastle Caravans & RVs': 'Newcastle Caravans & RV', 'Regent RV - Perth': 'ST James', 'regent rv - perth': 'ST James', 'regent rv-perth': 'ST James', 'St James': 'ST James', 'Regent RV - Traralgon': 'Traralgon', 'regent rv - traralgon': 'Traralgon', 'regent rv-traralgon': 'Traralgon', 'Regent RV - Frankston': 'Frankston', 'regent rv - frankston': 'Frankston', 'regent rv-frankston': 'Frankston', 'Snowy River Frankston': 'Frankston', 'snowy river frankston': 'Frankston', 'Snowy River Geelong': 'Geelong', 'snowy river geelong': 'Geelong', 'Snowy River Launceston': 'Launceston', 'snowy river launceston': 'Launceston', 'Regent RV - Townsville': 'Townsville', 'regent rv - townsville': 'Townsville', 'regent rv-townsville': 'Townsville', 'Regent RV - Toowoomba': 'Toowoomba', 'regent rv - toowoomba': 'Toowoomba', 'regent rv-toowoomba': 'Toowoomba', 'QCCC - Gympie': 'Gympie', 'qccc - gympie': 'Gympie', 'qccc-gympie': 'Gympie', 'Leisure Lion Pty Ltd - Gympie': 'Gympie', 'leisure lion pty ltd - gympie': 'Gympie', 'leisure lion pty ltd-gympie': 'Gympie', 'Leisure Lion - Bundaberg': 'Bundaberg', 'leisure lion - bundaberg': 'Bundaberg', 'leisure lion-bundaberg': 'Bundaberg', 'Green RV - Forest Glen': 'Forest Glen', 'green rv - forest glen': 'Forest Glen', 'green rv-forest glen': 'Forest Glen', 'GREEN RV PTY LTD': 'Forest Glen', 'green rv pty ltd': 'Forest Glen', 'Green - Forest Glen': 'Forest Glen', 'green - forest glen': 'Forest Glen', 'green-forest glen': 'Forest Glen', 'Green RV - Slacks Creek': 'Slacks Creek', 'green rv - slacks creek': 'Slacks Creek', 'green rv-slacks creek': 'Slacks Creek', 'Green - Slacks Creek': 'Slacks Creek', 'green - slacks creek': 'Slacks Creek', 'green-slacks creek': 'Slacks Creek', 'ABCO Caravans - Boambee Valley': 'ABCO', 'abco caravans - boambee valley': 'ABCO', 'abco caravans-boambee valley': 'ABCO', 'Auswide Caravans - South Nowra': 'Auswide', 'auswide caravans - south nowra': 'Auswide', 'auswide caravans-south nowra': 'Auswide', 'Bendigo Caravan Group - Bendigo': 'Bendigo', 'bendigo caravan group - bendigo': 'Bendigo', 'bendigo caravan group-bendigo': 'Bendigo', 'Great Ocean Road RV & Caravans - Warrnambool': 'Warrnambool', 'great ocean road rv & caravans - warrnambool': 'Warrnambool', 'great ocean road rv & caravans-warrnambool': 'Warrnambool', 'Vanari Caravans - Marden Point': 'Marsden Point', 'vanari caravans - marden point': 'Marsden Point', 'vanari caravans-marden point': 'Marsden Point', 'Vanari Caravans - Christchurch': 'Christchurch', 'vanari caravans - christchurch': 'Christchurch', 'vanari caravans-christchurch': 'Christchurch', 'CMG Campers - Christchurch': 'CMG Campers', 'cmg campers - christchurch': 'CMG Campers', 'cmg campers-christchurch': 'CMG Campers', 'Destiny RV(Snowy River Adelaide)': 'Destiny RV', 'destiny rv(snowy river adelaide)': 'Destiny RV', 'Snowy River Wangaratta': 'Wangaratta', 'snowy river wangaratta': 'Wangaratta', 'Motorhub Ltd': 'MotorHub', 'motorhub ltd': 'MotorHub'}


def config_norm(v: Any) -> str:
    return " ".join(clean(v).lower().split())



# Dealer collection/group views are disabled.
# Green Show has been removed from Dealer Workbench display and analytics.
CONFIG_GROUP_DEALERS = {}


def dealer_group_members(display_name: Any) -> set[str]:
    name = clean(display_name)
    members = CONFIG_GROUP_DEALERS.get(name) or []
    return {clean(x) for x in members if clean(x)}


def is_group_dealer(display_name: Any) -> bool:
    return clean(display_name) in CONFIG_GROUP_DEALERS

def config_display_from(v: Any) -> str:
    raw = clean(v)
    if not raw:
        return ""
    numeric = str(int(raw)) if raw.isdigit() else raw
    compact_dash = re.sub(r"\s*-\s*", "-", raw)
    norm = config_norm(raw)
    loose = re.sub(r"[^a-z0-9]+", "", raw.lower())
    # First use explicit config maps, then a loose compare that ignores spaces, hyphens, underscores and punctuation.
    direct = (
        CONFIG_ID_TO_DISPLAY.get(raw)
        or CONFIG_ID_TO_DISPLAY.get(numeric)
        or CONFIG_ALIAS_TO_DISPLAY.get(raw)
        or CONFIG_ALIAS_TO_DISPLAY.get(raw.lower())
        or CONFIG_ALIAS_TO_DISPLAY.get(norm)
        or CONFIG_ALIAS_TO_DISPLAY.get(compact_dash)
        or CONFIG_ALIAS_TO_DISPLAY.get(compact_dash.lower())
    )
    if direct:
        return clean(direct)
    if loose:
        for alias_key, display in CONFIG_ALIAS_TO_DISPLAY.items():
            if re.sub(r"[^a-z0-9]+", "", clean(alias_key).lower()) == loose:
                return clean(display)
    return ""


def final_configured_dealer_display(dealer_id: Any, raw_name: Any, mapped_name: Any = "", fallback: Any = "") -> str:
    d = clean(config_display_from(dealer_id) or config_display_from(mapped_name) or config_display_from(raw_name) or config_display_from(fallback))
    return d if d in CONFIG_ALLOWED_DEALER_DISPLAYS else ""


def strict_dealer_display(ticket: Dict[str, Any], roles: Dict[str, Any]) -> tuple[str, str, bool]:
    raw_warranty_dealer_id = get_field(ticket, ["WarrantyHandlingDealerID", "Warranty Handling Dealer(Assign)", "Warranty Handling Dealer Assign", "Warranty Handling Dealer ID", "WarrantyHandlingDealerAssign", "WarrantyHandlingDealerAssigned", "WarrantyHandlingDealer", "DealerID", "Dealer ID"])
    direct_name = get_field(ticket, ["DealerName", "Dealer Name", "WarrantyHandlingDealerName", "Warranty Handling Dealer Name", "Warranty Handling Dealer", "RepairerBusinessNameID", "Repairer Business Name", "Repairer"])
    role1001 = find_involved_party_name_by_role(roles, "1001")
    display = final_configured_dealer_display(raw_warranty_dealer_id, direct_name, "", role1001)
    if display:
        return display, raw_warranty_dealer_id, False
    return "Hidden", raw_warranty_dealer_id, bool(raw_warranty_dealer_id)


def role_name(roles: Dict[str, Any], role_id: str) -> str:
    return find_involved_party_name_by_role(roles, role_id)


def parse_sales_order_details(value: Any) -> list[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if isinstance(value, list):
        out: list[Dict[str, Any]] = []
        for item in value:
            out.extend(parse_sales_order_details(item))
        return out
    if not isinstance(value, dict):
        return []
    if set(value.keys()) & {"Delivery Count", "Description", "Material", "Order Qty"}:
        try:
            delivery_count = int(str(value.get("Delivery Count", "")).strip())
        except Exception:
            delivery_count = None
        try:
            order_qty = float(str(value.get("Order Qty", "0")).replace(",", "") or 0)
        except Exception:
            order_qty = 0.0
        return [{
            "description": clean(value.get("Description") or "-"),
            "material": clean(value.get("Material") or "-"),
            "deliveryCount": delivery_count,
            "orderQty": order_qty,
            "salesUnit": clean(value.get("Sales Unit") or ""),
            "salesOrderItem": clean(value.get("Sales Order Item") or ""),
        }]
    out: list[Dict[str, Any]] = []
    for nested in value.values():
        out.extend(parse_sales_order_details(nested))
    return out


def aging_days(v: Any) -> int:
    d = parse_date_any(v)
    if not d:
        return 0
    return max(0, int((datetime.now(timezone.utc) - d).total_seconds() // 86400))


def first_level_status(code: str, status: str, mapping: Dict[str, Any]) -> str:
    m = mapping.get(code) if isinstance(mapping, dict) else None
    if isinstance(m, dict):
        return clean(m.get("firstLevelStatus") or m.get("ticketStatusText") or status or code)
    return clean(status or code)


def is_closed_ticket_row(row: Dict[str, Any]) -> bool:
    return "closed" in clean(row.get("firstLevelStatus")).lower()


def is_partially_rejected_row(row: Dict[str, Any]) -> bool:
    return clean(row.get("orderRejectionStatus")).lower() == "partially rejected"


def build_dealer_ticket_rows(source_root: str, mapping: Dict[str, Any]) -> list[Dict[str, Any]]:
    root = db.reference(f"{source_root}/tickets").get() or {}
    if isinstance(root, list):
        entries = [(str(i), x) for i, x in enumerate(root) if x]
    elif isinstance(root, dict):
        entries = list(root.items())
    else:
        entries = []
    rows: list[Dict[str, Any]] = []
    for key, raw in entries:
        ticket, roles, tid = normalize_row(raw, key)
        if not tid or not isinstance(ticket, dict):
            continue
        dealer, dealer_id, hidden = strict_dealer_display(ticket, roles)
        if hidden or dealer == "Hidden":
            continue
        code = clean(ticket.get("TicketStatus") or ticket.get("statusCode") or ticket.get("Status"))
        status = status_text_for(code, clean(ticket.get("TicketStatusText") or ticket.get("statusText")), mapping)
        created = ticket.get("CreatedOn") or ticket.get("createdOn") or ticket.get("CreatedAt") or ""
        rows.append({
            "key": safe_key(tid), "id": clean(tid), "dealer": dealer, "dealerId": dealer_id,
            "code": code, "status": status, "statusText": status, "firstLevelStatus": first_level_status(code, status, mapping),
            "claim": ticket_claim_type(ticket),
            "customer": clean(ticket.get("TicketName") or ticket.get("CustomerName") or ticket.get("ServiceRequesterEmail") or ""),
            "amount": parse_amount(ticket.get("AmountIncludingTax")), "z1z8TimeConsumed": clean(ticket.get("Z1Z8TimeConsumed")), "isApproved": is_approved_by_z1z8(ticket), "isUnapproved": not is_approved_by_z1z8(ticket), "created": created, "agingDays": aging_days(created),
            "repair": clean(role_name(roles, "43") or ticket.get("RepairerBusinessNameID") or ticket.get("RepairerNamePointOfContact") or ticket.get("RepairerEmail") or "-"),
            "employee": employee_from_ticket(ticket, roles), "details": parse_sales_order_details(ticket.get("Sales Order Details")),
            "isCritical": is_critical(ticket, mapping), "issueStatus": clean(ticket.get("Issue Status")),
            "orderRejectionStatus": clean(ticket.get("Order Rejection Status")), "salesOrder": clean(ticket.get("Sales Order")),
            "soCreatedDate": clean(ticket.get("SO Created Date")), "chassis": clean(ticket.get("ChassisNumber")), "serial": clean(ticket.get("SerialID")),
        })
    return rows


def event_dealer_display(e: Dict[str, Any]) -> str:
    raw = clean(e.get("dealer"))
    return final_configured_dealer_display("", raw, "", raw) or config_display_from(raw)


def group_dealer_materials(rows: list[Dict[str, Any]], delivered: bool = False) -> list[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for t in rows:
        if is_closed_ticket_row(t) or is_partially_rejected_row(t):
            continue
        for item in t.get("details") or []:
            try:
                delivery_count = int(item.get("deliveryCount"))
            except Exception:
                continue
            is_delivered = delivery_count > 0
            if delivered != is_delivered:
                continue
            code = clean(item.get("material")); desc = clean(item.get("description"))
            if not code or not desc:
                continue
            key = safe_key(code + "|" + desc)
            g = groups.setdefault(key, {"code": code, "desc": desc, "totalQty": 0, "deliveryCount": 0, "ticketIds": set(), "amountByTicket": {}, "items": []})
            qty = float(item.get("orderQty") or 1)
            amount = parse_amount(t.get("amount"))
            ticket_id = clean(t.get("id"))
            g["totalQty"] += qty
            g["deliveryCount"] += delivery_count
            g["ticketIds"].add(ticket_id)
            if ticket_id and ticket_id not in g["amountByTicket"]:
                g["amountByTicket"][ticket_id] = amount
            g["items"].append({
                "ticketId": t.get("id"), "status": t.get("status"), "qty": qty,
                "deliveryCount": delivery_count, "amount": amount,
                "created": t.get("created"), "agingDays": t.get("agingDays"),
                "customer": t.get("customer"), "claim": t.get("claim"), "repair": t.get("repair"),
                "employee": t.get("employee"), "soCreatedDate": t.get("soCreatedDate"),
                "oldestApprovedAge": aging_days(t.get("soCreatedDate")),
                "salesUnit": item.get("salesUnit"), "salesOrderItem": item.get("salesOrderItem"),
            })
    out=[]
    for g in groups.values():
        items = g["items"]
        out.append({
            "code": g["code"], "desc": g["desc"], "totalQty": g["totalQty"],
            "deliveryCount": g["deliveryCount"], "ticketCount": len(g["ticketIds"]),
            "amount": round(sum(g["amountByTicket"].values()), 2),
            "oldestAge": max([int(x.get("agingDays") or 0) for x in items] or [0]),
            "oldestApprovedAge": max([int(x.get("oldestApprovedAge") or 0) for x in items] or [0]),
            "items": items,
        })
    out.sort(key=lambda x: (-float(x.get("totalQty") or 0), clean(x.get("code"))))
    return out


def dealer_age_buckets(rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    vals = [0,0,0,0,0]
    for t in rows:
        a = int(t.get("agingDays") or 0)
        if a <= 7: vals[0]+=1
        elif a <= 30: vals[1]+=1
        elif a <= 60: vals[2]+=1
        elif a <= 90: vals[3]+=1
        else: vals[4]+=1
    labels = ["0-7 days","8-30 days","31-60 days","61-90 days","90+ days"]
    return [{"label": labels[i], "count": vals[i]} for i in range(5)]


def build_dealer_trend(critical_rows: list[Dict[str, Any]], logs: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Build dealer trend focused on flow, not the current New Claim snapshot.

    Blue line is Entered Critical cumulative in the selected window.
    Green line is Exited Critical cumulative in the selected window.
    Orange line is reconstructed current critical stock and uses the right axis
    in the webpage, so it does not flatten the in/out flow lines.

    Previous versions used a reconstructed New Claim stock line. That looked
    flat for many dealers because we only have the current ticket snapshot, not
    a full historical New Claim stock snapshot by day.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    start = DASHBOARD_MIN_DATE
    d = parse_date_any(start)
    end = parse_date_any(today)
    if not d or not end:
        return []

    dates: list[str] = []
    while d.date() <= end.date() and len(dates) < 370:
        dates.append(d.date().isoformat())
        d = datetime.fromtimestamp(d.timestamp() + 86400, tz=timezone.utc)

    entered_by: Dict[str, int] = {}
    exited_by: Dict[str, int] = {}
    for e in logs:
        dk = event_date_key(e)
        if not dk or dk < start:
            continue
        cls = clean(e.get("cls"))
        typ = event_type(e)
        if cls == "enter" or typ == "entered":
            entered_by[dk] = entered_by.get(dk, 0) + 1
        elif cls == "exit" or typ == "exited":
            exited_by[dk] = exited_by.get(dk, 0) + 1

    current_critical_now = len(critical_rows)

    out: list[Dict[str, Any]] = []
    entered_cumulative = 0
    exited_cumulative = 0

    for dk in dates:
        entered_cumulative += entered_by.get(dk, 0)
        exited_cumulative += exited_by.get(dk, 0)

        # Reconstruct end-of-day critical stock by working backwards from the
        # current stock. The last point equals current critical count.
        entered_after = sum(v for day, v in entered_by.items() if day > dk)
        exited_after = sum(v for day, v in exited_by.items() if day > dk)
        critical_stock = max(0, current_critical_now - entered_after + exited_after)

        out.append({
            "date": dk,
            # Keep the old field name for frontend compatibility, but it now
            # means Entered Critical cumulative, not New Claim stock.
            "newClaim": int(entered_cumulative),
            "entered": int(entered_cumulative),
            "critical": int(critical_stock),
            "exited": int(exited_cumulative),
        })
    return out


def _duration_bucket(days: float) -> str:
    if days <= 1:
        return "leOneDay"
    if days <= 3:
        return "oneToThreeDays"
    if days <= 7:
        return "threeToSevenDays"
    if days <= 14:
        return "eightToFourteenDays"
    return "overFourteenDays"


def calculate_handling_speed(logs: list[Dict[str, Any]], current_critical_rows: list[Dict[str, Any]], ticket_by_id: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Pre-calculate handling speed using the true full available history.

    Important:
    - Do NOT clip anything to DASHBOARD_MIN_DATE / 2026-05-25.
    - For each exited-critical event, duration is:
        exited critical time - previous entered critical time
      If the old entered-critical log is missing, use the ticket's original
      CreatedOn/created date from the full tickets snapshot as a fallback start. This prevents old tickets from
      being incorrectly counted as resolved within 14 days just because the
      dashboard history started later.
    - 2-week benchmark = resolved tickets within 14 days / all resolved tickets
      with either a real entered-critical date or a created-date fallback.
    - Estimated clear time = current critical total / average exited-critical
      speed across the full observed period, not from 2026-05-25.
    """
    buckets = {
        "leOneDay": 0,
        "oneToThreeDays": 0,
        "threeToSevenDays": 0,
        "eightToFourteenDays": 0,
        "overFourteenDays": 0,
    }
    labels = {
        "leOneDay": "≤ 1 day",
        "oneToThreeDays": "1–3 days",
        "threeToSevenDays": "3–7 days",
        "eightToFourteenDays": "8–14 days",
        "overFourteenDays": "> 14 days",
    }

    now_dt = datetime.now(timezone.utc)
    ticket_by_id = ticket_by_id or {}
    enter_times_by_ticket: Dict[str, list[datetime]] = {}
    exit_items: list[tuple[str, datetime, Dict[str, Any]]] = []
    entered_daily: Dict[str, int] = {}
    exited_daily: Dict[str, int] = {}
    all_period_dates: list[datetime] = []

    def ticket_created_dt_by_id(tid: str) -> Optional[datetime]:
        t = ticket_by_id.get(clean(tid), {}) if tid else {}
        if not isinstance(t, dict):
            return None
        for k in [
            "created", "CreatedOn", "createdOn", "CreatedAt", "createdAt",
            "ticketCreatedOn", "ticketCreated", "TicketCreatedOn", "TicketCreated",
            "creationDate", "CreationDate",
        ]:
            d = parse_date_any(t.get(k))
            if d:
                return d
        return None

    def event_created_dt(e: Dict[str, Any]) -> Optional[datetime]:
        # First use explicit fields stored in the log row, then fall back to
        # the full ticket snapshot by ticket id. This is the key fix: when an
        # entered-critical event is not available, the calculation uses the
        # ticket's original CreatedOn instead of 2026-05-25 or skipping it.
        for k in [
            "created", "CreatedOn", "createdOn", "CreatedAt", "createdAt",
            "ticketCreatedOn", "ticketCreated",
            "TicketCreatedOn", "TicketCreated", "creationDate", "CreationDate",
        ]:
            d = parse_date_any(e.get(k))
            if d:
                return d
        return ticket_created_dt_by_id(event_ticket_id(e))

    for e in logs or []:
        tid = event_ticket_id(e)
        if not tid:
            continue
        dt = parse_date_any(event_time(e) or e.get("detectedAt") or e.get("createdAt") or e.get("dataSyncAt"))
        if not dt:
            continue

        all_period_dates.append(dt)
        created_dt = event_created_dt(e)
        if created_dt:
            all_period_dates.append(created_dt)

        cls = clean(e.get("cls"))
        typ = event_type(e)
        if cls == "enter" or typ == "entered":
            enter_times_by_ticket.setdefault(tid, []).append(dt)
            dk = dt.date().isoformat()
            entered_daily[dk] = entered_daily.get(dk, 0) + 1
        elif cls == "exit" or typ == "exited":
            exit_items.append((tid, dt, e))
            dk = dt.date().isoformat()
            exited_daily[dk] = exited_daily.get(dk, 0) + 1

    # Include current open critical tickets' original created dates in the full
    # observed period, so the clear-time speed is not accidentally anchored to
    # the dashboard go-live date.
    for t in current_critical_rows or []:
        d = parse_date_any(t.get("created") or t.get("CreatedOn") or t.get("createdOn"))
        if d:
            all_period_dates.append(d)

    for vals in enter_times_by_ticket.values():
        vals.sort()

    resolved_durations: list[float] = []
    resolved_rows: list[Dict[str, Any]] = []
    start_source_counts = {"enteredCritical": 0, "ticketCreated": 0, "missingStart": 0}
    for tid, exit_dt, e in exit_items:
        candidates = [x for x in enter_times_by_ticket.get(tid, []) if x <= exit_dt]
        if candidates:
            start_dt = candidates[-1]
            start_source_counts["enteredCritical"] += 1
        else:
            start_dt = event_created_dt(e)
            if start_dt and start_dt <= exit_dt:
                start_source_counts["ticketCreated"] += 1
            else:
                # No true enter date and no created date. Do not fake 2026-05-25.
                start_source_counts["missingStart"] += 1
                continue

        days = max(0.0, (exit_dt - start_dt).total_seconds() / 86400.0)
        bucket_key = _duration_bucket(days)
        resolved_durations.append(days)
        buckets[bucket_key] += 1
        resolved_rows.append({
            "ticketId": tid,
            "exitDate": exit_dt.date().isoformat(),
            "exitAt": exit_dt.isoformat(),
            "startAt": start_dt.isoformat(),
            "durationDays": round(days, 4),
            "bucket": bucket_key,
        })

    resolved_total = len(resolved_durations)
    within_two_weeks = (
        buckets["leOneDay"]
        + buckets["oneToThreeDays"]
        + buckets["threeToSevenDays"]
        + buckets["eightToFourteenDays"]
    )
    two_week_rate = round((within_two_weeks / resolved_total) * 100, 1) if resolved_total else 0
    avg_days = round(sum(resolved_durations) / resolved_total, 1) if resolved_total else 0

    current_stock = int(len(current_critical_rows or []))

    if all_period_dates:
        start_date = min(all_period_dates).date()
        end_date = max(max(all_period_dates).date(), now_dt.date())
    else:
        start_date = now_dt.date()
        end_date = now_dt.date()
    total_days = max(1, (end_date - start_date).days + 1)

    from datetime import timedelta

    window_dates = [(end_date - timedelta(days=i)).isoformat() for i in range(total_days)]
    total_entered = sum(entered_daily.get(d, 0) for d in window_dates)
    total_exited = sum(exited_daily.get(d, 0) for d in window_dates)
    all_enter_avg = total_entered / total_days
    all_exit_avg = total_exited / total_days

    # Clear-time estimate requested by the business:
    # current critical total / recent active-day average net clearing speed.
    # Net clearing speed = exited critical per day - entered critical per day.
    #
    # Important fix:
    # Use the last 7 ACTIVE event dates, not the last 7 calendar dates up to today.
    # Otherwise weekends / days where the PowerShell task did not run can dilute
    # the speed and produce unrealistic values such as 265d.
    active_dates = sorted(set(entered_daily.keys()) | set(exited_daily.keys()))
    recent_dates = active_dates[-7:] if active_dates else []
    recent_days = max(1, len(recent_dates))
    recent_entered = sum(entered_daily.get(d, 0) for d in recent_dates)
    recent_exited = sum(exited_daily.get(d, 0) for d in recent_dates)
    recent_enter_avg = recent_entered / recent_days
    recent_exit_avg = recent_exited / recent_days
    recent_net_clear_per_day = recent_exit_avg - recent_enter_avg

    avg_solution_speed = recent_net_clear_per_day
    if current_stock <= 0:
        estimated_clear_days = 0
    elif recent_net_clear_per_day > 0:
        estimated_clear_days = int(math.ceil(current_stock / recent_net_clear_per_day))
    else:
        estimated_clear_days = None

    net_clear_per_day = recent_net_clear_per_day
    current_new_claim_stock = sum(1 for t in (current_critical_rows or []) if clean(t.get("code")) == "Z1" or "new claim" in clean(t.get("status")).lower())
    forecast_backlog = current_stock

    order = ["leOneDay", "oneToThreeDays", "threeToSevenDays", "eightToFourteenDays", "overFourteenDays"]
    rows = [{"key": k, "label": labels[k], "count": int(buckets[k])} for k in order]
    return {
        "buckets": rows,
        "resolvedRows": resolved_rows,
        "enteredDaily": entered_daily,
        "exitedDaily": exited_daily,
        "bucketLabels": labels,
        "twoWeekBenchmarkRate": two_week_rate,
        "resolvedWithinTwoWeeksRate": two_week_rate,
        # Backward-compatible field name for older HTML. It now carries the
        # 2-week benchmark rate, not the old 3-day rate.
        "resolvedWithinThreeDaysRate": two_week_rate,
        "avgResolutionDays": avg_days,
        "resolvedTotal": resolved_total,
        "resolvedWithinTwoWeeks": within_two_weeks,
        "durationStartSourceCounts": start_source_counts,
        "skippedResolvedWithoutEnter": start_source_counts["missingStart"],
        "currentCritical": current_stock,
        "currentNewClaimStock": int(current_new_claim_stock),
        "forecastBacklog": int(forecast_backlog),
        "estimatedClearDays": estimated_clear_days,
        "avgEnteredPerDay": round(all_enter_avg, 2),
        "avgExitedPerDay": round(all_exit_avg, 2),
        "recent7EnteredPerDay": round(recent_enter_avg, 2),
        "recent7ExitedPerDay": round(recent_exit_avg, 2),
        "recent7NetClearPerDay": round(recent_net_clear_per_day, 2),
        "recent7ActiveDays": int(len(recent_dates)),
        "recent7ActiveDateRange": {"from": recent_dates[0] if recent_dates else "", "to": recent_dates[-1] if recent_dates else ""},
        "netClearPerDay": round(net_clear_per_day, 2),
        "exitCapacityPerDay": round(recent_exit_avg, 2),
        "averageSolutionSpeedPerDay": round(avg_solution_speed, 2),
        "inflowPressurePerDay": 0,
        "historyStartDate": start_date.isoformat(),
        "historyEndDate": end_date.isoformat(),
        "historyDays": total_days,
        "startDate": start_date.isoformat(),
        "note": "2-week benchmark uses true entered-critical time; if missing, ticket CreatedOn/created date from full ticket snapshot is used. Estimated clear time = current critical total divided by recent last-7-active-days net clearing speed (exited critical/day minus entered critical/day).",
    }

def build_dealer_analytics(source_root: str, monitor_root: str, history_events: list[Dict[str, Any]], generated_at: str) -> Dict[str, Any]:
    mapping = load_mapping()
    rows = build_dealer_ticket_rows(source_root, mapping)
    ticket_by_id = {clean(t.get("id")): t for t in rows if clean(t.get("id"))}
    base_dealers = sorted({clean(t.get("dealer")) for t in rows if clean(t.get("dealer"))})
    # No extra collection dealers are generated; Green Show is intentionally excluded.
    dealers = sorted(set(base_dealers) | set(CONFIG_GROUP_DEALERS.keys()))
    claim_labels = ["All Claims", "In Field Warranty Claims", "Pre Delivery Warranty Claims"]

    def claim_key(label: str) -> str:
        return _claim_key(label)

    def row_matches_claim(t: Dict[str, Any], label: str) -> bool:
        if label == "All Claims":
            return True
        return clean(t.get("claim")) == label

    def event_matches_claim(e: Dict[str, Any], t: Optional[Dict[str, Any]], label: str) -> bool:
        if label == "All Claims":
            return True
        # Prefer the current ticket snapshot when we have it. It uses the same
        # C4C claim extraction as the tickets table, so the dealer view, log and
        # parts view all switch consistently.
        if isinstance(t, dict) and t:
            return clean(t.get("claim")) == label
        raw = clean(
            e.get("claimType")
            or e.get("ClaimType")
            or e.get("ticketClaimType")
            or e.get("TicketClaimType")
            or e.get("TicketTypeText")
            or e.get("TicketType")
            or e.get("processType")
            or e.get("ProcessType")
        )
        if raw:
            return normalized_claim_type(raw) == label
        # If the historical record has no ticket and no claim info, only the All
        # Claims view should contain it. This avoids polluting In Field / Pre Delivery.
        return False

    def build_view_for_dealer(dealer: str, label: str) -> Dict[str, Any]:
        group_members = dealer_group_members(dealer)

        def row_dealer_matches(t: Dict[str, Any]) -> bool:
            d = clean(t.get("dealer"))
            if group_members:
                return d in group_members
            return d == dealer

        def event_dealer_matches(e: Dict[str, Any], t: Optional[Dict[str, Any]]) -> bool:
            if isinstance(t, dict) and t:
                d = clean(t.get("dealer"))
                return d in group_members if group_members else d == dealer
            ed = event_dealer_display(e)
            return ed in group_members if group_members else ed == dealer

        all_rows = [t for t in rows if row_dealer_matches(t) and row_matches_claim(t, label)]
        critical_rows = [t for t in all_rows if t.get("isCritical")]
        logs: list[Dict[str, Any]] = []
        for e in history_events:
            tid = event_ticket_id(e)
            t = ticket_by_id.get(tid)
            dealer_match = event_dealer_matches(e, t)
            if not dealer_match or not event_matches_claim(e, t, label):
                continue
            cls = clean(e.get("cls")) or ("exit" if event_type(e) == "exited" else ("enter" if event_type(e) == "entered" else "move"))
            logs.append({
                "id": tid,
                "type": clean(e.get("type") or e.get("changeType") or e.get("eventType")),
                "cls": cls,
                "detectedAt": event_time(e),
                "dataSyncAt": clean(e.get("dataSyncAt")),
                "fromCode": clean(e.get("fromCode") or e.get("oldCode")),
                "fromStatus": clean(e.get("fromStatus") or e.get("oldStatus")),
                "toCode": clean(e.get("toCode") or e.get("newCode")),
                "toStatus": clean(e.get("toStatus") or e.get("newStatus")),
                "name": clean(e.get("name") or e.get("customer") or (t or {}).get("customer")),
                "customer": clean(e.get("name") or e.get("customer") or (t or {}).get("customer")),
                "amount": e.get("amount") or (t or {}).get("amount") or 0,
                "dealer": dealer,
                "claim": label if label != "All Claims" else clean((t or {}).get("claim")) or _event_claim_type(e, {k: {"claimType": v.get("claim")} for k, v in ticket_by_id.items()}),
                "currentCode": clean((t or {}).get("code")),
                "currentStatus": clean((t or {}).get("status")),
                "repair": clean((t or {}).get("repair")),
                "employee": clean((t or {}).get("employee")),
                "created": clean((t or {}).get("created")),
                "agingDays": (t or {}).get("agingDays") or 0,
                "chassis": clean((t or {}).get("chassis")),
                "serial": clean((t or {}).get("serial")),
                "salesOrder": clean((t or {}).get("salesOrder")),
                "issueStatus": clean((t or {}).get("issueStatus")),
                "orderRejectionStatus": clean((t or {}).get("orderRejectionStatus")),
            })
        logs.sort(key=lambda e: clean(e.get("detectedAt")), reverse=True)
        material_groups = group_dealer_materials(all_rows)
        delivered_material_groups = group_dealer_materials(all_rows, delivered=True)
        status_counts: Dict[str, int] = {}
        for t in critical_rows:
            k = clean(t.get("status")) or "Unknown"
            status_counts[k] = status_counts.get(k, 0) + 1
        approved_rows = [t for t in all_rows if bool(t.get("isApproved"))]
        unapproved_rows = [t for t in all_rows if not bool(t.get("isApproved"))]
        return {
            "label": label,
            "key": claim_key(label),
            "presetRanges": _preset_ranges_for(generated_at, DASHBOARD_MIN_DATE),
            "summary": {
                "totalTickets": len(all_rows),
                "approvedTickets": len(approved_rows),
                "unapprovedTickets": len(unapproved_rows),
                "approvedValue": round(sum(parse_amount(t.get("amount")) for t in approved_rows), 2),
                "unapprovedValue": round(sum(parse_amount(t.get("amount")) for t in unapproved_rows), 2),
                "approvalRule": "Approved = Z1Z8TimeConsumed totalMinutes > 0; Unapproved = empty or <= 0.",
                "criticalTickets": len(critical_rows),
                "openMaterialTypes": len(material_groups),
                "deliveredMaterialTypes": len(delivered_material_groups),
                "deliveredMaterialAmount": round(sum(parse_amount(g.get("amount")) for g in delivered_material_groups), 2),
                "logTotal": len(logs),
                "entered": sum(1 for e in logs if clean(e.get("cls")) == "enter"),
                "moved": sum(1 for e in logs if clean(e.get("cls")) == "move"),
                "exited": sum(1 for e in logs if clean(e.get("cls")) == "exit"),
            },
            "dashboard": {
                "statusDistribution": [{"label": k, "count": v} for k, v in sorted(status_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
                "handlingSpeed": calculate_handling_speed(logs, critical_rows, ticket_by_id),
                "ageBuckets": dealer_age_buckets(critical_rows),
                "trend": build_dealer_trend(critical_rows, logs),
            },
            "tickets": critical_rows,
            "logs": logs,
            "materials": material_groups,
            "deliveredMaterials": delivered_material_groups,
        }

    by_dealer: Dict[str, Any] = {}
    index_stats: list[Dict[str, Any]] = []
    for dealer in dealers:
        views = {claim_key(label): build_view_for_dealer(dealer, label) for label in claim_labels}
        all_view = views["all"]
        dealer_key = safe_key(dealer)
        payload = {
            "generatedAt": generated_at,
            "dealer": dealer,
            "isGroup": is_group_dealer(dealer),
            "groupMembers": sorted(dealer_group_members(dealer)),
            "minDate": DASHBOARD_MIN_DATE,
            "claimOptions": [{"key": claim_key(label), "label": label} for label in claim_labels],
            "defaultView": "all",
            "views": views,
            # Backward-compatible fields for existing HTML.
            "summary": all_view["summary"],
            "dashboard": all_view["dashboard"],
            "tickets": all_view["tickets"],
            "logs": all_view["logs"],
            "materials": all_view["materials"],
            "deliveredMaterials": all_view.get("deliveredMaterials", []),
        }
        by_dealer[dealer_key] = payload
        index_stats.append({"dealer": dealer, "key": dealer_key, "isGroup": is_group_dealer(dealer), "groupMembers": sorted(dealer_group_members(dealer)), **all_view["summary"]})
    index_stats.sort(key=lambda x: clean(x.get("dealer")))
    return {
        "generatedAt": generated_at,
        "minDate": DASHBOARD_MIN_DATE,
        "claimOptions": [{"key": claim_key(label), "label": label} for label in claim_labels],
        "defaultView": "all",
        "summary": {
            "dealers": len(index_stats),
            "totalTickets": len(rows),
            # Avoid double-counting collection/group dealers in the top-level dealer summary.
            "criticalTickets": sum(int(x.get("criticalTickets") or 0) for x in index_stats if not x.get("isGroup")),
        },
        "stats": index_stats,
        "byDealer": by_dealer,
        "storageNote": "Pre-calculated by Python. Dealer Workbench fetches index first, then selected dealer payload. Claim buttons switch pre-built views only. Green Show is intentionally excluded from dealer analytics.",
    }


def load_all_history(monitor_root: str) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    for path in (f"{monitor_root}/history", "ctmCriticalStatusLog/history"):
        try:
            obj = db.reference(path).get() or {}
            events.extend(history_object_to_list(obj))
        except Exception as e:
            print(f"[ANALYTICS WARN] Cannot read /{path}: {e}")
    return dedupe_events(events)


def build_employee_analytics(snap: Dict[str, Dict[str, Any]], history_events: list[Dict[str, Any]], generated_at: str) -> Dict[str, Any]:
    ticket_by_id = {clean(v.get("id")): v for v in snap.values() if clean(v.get("id"))}
    claim_labels = ["All Claims", "In Field Warranty Claims", "Pre Delivery Warranty Claims"]

    def ticket_matches(t: Dict[str, Any], label: str) -> bool:
        if label == "All Claims":
            return True
        return clean(t.get("claimType")) == label

    def event_matches(e: Dict[str, Any], label: str) -> bool:
        if label == "All Claims":
            return True
        return _event_claim_type(e, ticket_by_id) == label

    def build_view(label: str) -> Dict[str, Any]:
        total_by_employee: Dict[str, int] = {}
        ticket_detail: Dict[str, Dict[str, Any]] = {}
        total_current_critical = 0
        not_assigned_rows: list[Dict[str, Any]] = []

        for t in snap.values():
            # Employee Workbench focuses on current Critical workload only.
            if not bool(t.get("isCritical")) or not ticket_matches(t, label):
                continue

            emp = clean(t.get("employee")) or "Unknown"
            tid = clean(t.get("id"))

            # This KPI must match Team Dashboard Total Critical for the same
            # claim filter. Count the ticket first; then decide whether/how it
            # appears in the employee/queue bar list.
            total_current_critical += 1

            # Admin/test/sample rows are operational noise for this page.
            # Queue Warranty is NOT hidden: it is the C4C Assign To queue bucket
            # for new claims waiting for assignment and must be shown.
            if is_excluded_employee_name(emp):
                continue

            # Only genuinely missing/unknown owners count as Not Assigned.
            if is_not_assigned_employee_name(emp):
                not_assigned_rows.append({
                    "id": tid,
                    "employeeRaw": emp,
                    "status": clean(t.get("statusText")),
                    "statusCode": clean(t.get("statusCode") or t.get("code")),
                    "dealer": clean(t.get("dealer")),
                    "created": t.get("created") or "",
                    "claimType": clean(t.get("claimType")),
                    "customer": clean(t.get("name")),
                })
                continue

            total_by_employee[emp] = total_by_employee.get(emp, 0) + 1
            ticket_detail[tid] = {
                "id": tid,
                "employee": emp,
                "status": clean(t.get("statusText")),
                "statusCode": clean(t.get("statusCode") or t.get("code")),
                "dealer": clean(t.get("dealer")),
                "created": t.get("created") or "",
                "claimType": clean(t.get("claimType")),
                "isApproved": bool(t.get("isApproved")),
                "isUnapproved": not bool(t.get("isApproved")),
                "amount": parse_amount(t.get("amount")),
                "isCritical": True,
                "removedTotal": 0,
                "lastRemovedAt": "",
            }

        removed_by_employee: Dict[str, int] = {}
        removed_daily_by_employee: Dict[str, Dict[str, int]] = {}
        removed_by_ticket: Dict[str, int] = {}
        last_removed_by_ticket: Dict[str, str] = {}
        unmapped_removed_events: list[Dict[str, Any]] = []
        total_removed = 0
        total_removed_daily: Dict[str, int] = {}
        unmapped_label = "Unmapped removed events"

        for e in history_events:
            if event_type(e) != "exited":
                continue
            dkey = event_date_key(e)
            if dkey and dkey < DASHBOARD_MIN_DATE:
                continue
            if not event_matches(e, label):
                continue
            tid = event_ticket_id(e)
            ticket = ticket_by_id.get(tid, {})
            emp = event_employee_from_snapshot(e, ticket_by_id)

            # KPI must match Team Dashboard Exited Critical, so every exited event
            # is counted here. Role 40 / Assign To maps it to a real employee where
            # available. Only genuinely unmapped rows are placed into a separate
            # audit bucket retained in Firebase only; no local Excel export is required.
            total_removed += 1
            if dkey:
                total_removed_daily[dkey] = total_removed_daily.get(dkey, 0) + 1

            if is_real_employee_name(emp):
                bucket = emp
            else:
                bucket = unmapped_label
                reason = "missing role 40 Assign To / InvolvedPartyName"
                if is_excluded_employee_name(emp):
                    reason = "excluded admin/test/demo/sample owner, not a real employee"
                unmapped_removed_events.append(make_unmapped_removed_event_row(e, ticket, reason))

            removed_by_employee[bucket] = removed_by_employee.get(bucket, 0) + 1
            if dkey:
                by_day = removed_daily_by_employee.setdefault(bucket, {})
                by_day[dkey] = by_day.get(dkey, 0) + 1
            if tid and bucket != unmapped_label:
                removed_by_ticket[tid] = removed_by_ticket.get(tid, 0) + 1
                et = event_time(e)
                if et and et > last_removed_by_ticket.get(tid, ""):
                    last_removed_by_ticket[tid] = et

        for tid, cnt in removed_by_ticket.items():
            if tid in ticket_detail:
                ticket_detail[tid]["removedTotal"] = cnt
                ticket_detail[tid]["lastRemovedAt"] = last_removed_by_ticket.get(tid, "")

        employees = sorted(set(total_by_employee) | set(removed_by_employee))
        stats = []
        for name in employees:
            stats.append({
                "name": name,
                "key": safe_key(name),
                # Backward compatible: totalTickets now means current critical tickets.
                "totalTickets": total_by_employee.get(name, 0),
                "criticalTickets": total_by_employee.get(name, 0),
                "removedTotal": removed_by_employee.get(name, 0),
                "removedDaily": removed_daily_by_employee.get(name, {}),
            })
        stats.sort(key=lambda x: (-int(x.get("totalTickets") or 0), -int(x.get("removedTotal") or 0), clean(x.get("name")).lower()))
        for i, row in enumerate(stats, 1):
            row["rank"] = i

        detail_rows = list(ticket_detail.values())
        detail_rows.sort(key=lambda x: (clean(x.get("employee")).lower(), clean(x.get("id"))))

        # Oldest not-assigned critical ticket. Use parsed date when possible,
        # but keep the original source date string for display in the webpage.
        not_assigned_rows.sort(key=lambda x: (date_key(x.get("created")) or "9999-12-31", clean(x.get("id"))))
        oldest_not_assigned = not_assigned_rows[0] if not_assigned_rows else {}
        assigned_critical = len(detail_rows)
        not_assigned_critical = len(not_assigned_rows)
        approved_critical = sum(1 for r in detail_rows if bool(r.get("isApproved"))) + sum(1 for r in not_assigned_rows if bool(r.get("isApproved")))
        unapproved_critical = max(0, total_current_critical - approved_critical)
        assignment_rate = (assigned_critical / total_current_critical * 100.0) if total_current_critical else 0.0

        return {
            "label": label,
            "key": _claim_key(label),
            "generatedAt": generated_at,
            "minDate": DASHBOARD_MIN_DATE,
            "presetRanges": _preset_ranges_for(generated_at, DASHBOARD_MIN_DATE),
            "summary": {
                "employees": len(stats),
                "totalTickets": total_current_critical,
                "criticalTickets": total_current_critical,
                "assignedCriticalTickets": assigned_critical,
                "notAssignedCriticalTickets": not_assigned_critical,
                "assignmentRate": round(assignment_rate, 1),
                "oldestNotAssignedDate": clean(oldest_not_assigned.get("created")),
                "oldestNotAssignedTicket": clean(oldest_not_assigned.get("id")),
                "oldestNotAssignedDealer": clean(oldest_not_assigned.get("dealer")),
                "oldestNotAssignedStatus": clean(oldest_not_assigned.get("status")),
                "criticalRemoved": total_removed,
                "approvedTickets": approved_critical,
                "unapprovedTickets": unapproved_critical,
                "approvalRule": "Approved = Z1Z8TimeConsumed totalMinutes > 0; Unapproved = empty or <= 0.",
                # All exited critical events by day. This is used by Employee KPI
                # so Critical Removed and rates match Team Dashboard Exited Critical
                # for the same claim/date filter, including unmapped/excluded owners.
                "removedDaily": total_removed_daily,
            },
            "stats": stats,
            "detailRows": detail_rows,
            "notAssignedRows": not_assigned_rows[:300],
            "unmappedRemovedEvents": unmapped_removed_events[:2000],
        }

    views = {_claim_key(label): build_view(label) for label in claim_labels}
    all_view = views["all"]
    return {
        "generatedAt": generated_at,
        "minDate": DASHBOARD_MIN_DATE,
        "claimOptions": [{"key": _claim_key(label), "label": label} for label in claim_labels],
        "defaultView": "all",
        "views": views,
        # Backward-compatible fields for older employee-workbench.html.
        "summary": all_view["summary"],
        "stats": all_view["stats"],
        "detailRows": all_view["detailRows"],
        "storageNote": "Pre-calculated by Python. Claim buttons switch pre-built employee views; browser only renders and filters by date.",
    }


def _claim_key(label: str) -> str:
    s = clean(label).lower()
    if not s or s == "all claims":
        return "all"
    if "pre delivery" in s or "predelivery" in s:
        return "preDelivery"
    if "in field" in s:
        return "inField"
    return safe_key(label)


def _event_claim_type(e: Dict[str, Any], ticket_by_id: Dict[str, Dict[str, Any]]) -> str:
    raw = clean(
        e.get("claimType")
        or e.get("ClaimType")
        or e.get("ticketClaimType")
        or e.get("TicketClaimType")
        or e.get("TicketTypeText")
        or e.get("TicketType")
        or e.get("processType")
        or e.get("ProcessType")
    )
    if raw:
        return normalized_claim_type(raw) or raw
    tid = event_ticket_id(e)
    t = ticket_by_id.get(tid, {}) if tid else {}
    return clean(t.get("claimType")) or "In Field Warranty Claims"


def _date_range_points(start_date: str, end_date: str) -> list[str]:
    start = parse_date_any(start_date)
    end = parse_date_any(end_date)
    if not start or not end:
        return []
    out: list[str] = []
    cur = start.date()
    last = end.date()
    while cur <= last:
        out.append(cur.isoformat())
        cur = cur.fromordinal(cur.toordinal() + 1)
    return out



def _range_start_from_end(end_date: str, days: int, min_date: str = DASHBOARD_MIN_DATE) -> str:
    end = parse_date_any(end_date)
    if not end:
        return min_date
    start = end.date().fromordinal(end.date().toordinal() - max(0, days - 1))
    out = start.isoformat()
    return min_date if out < min_date else out


def _preset_ranges_for(generated_day: str, min_date: str = DASHBOARD_MIN_DATE) -> Dict[str, Dict[str, str]]:
    end = date_key(generated_day) or now_iso()[:10]
    return {
        "day": {"from": end, "to": end, "label": "Today Change"},
        "week": {"from": _range_start_from_end(end, 7, min_date), "to": end, "label": "Weekly Change"},
        "month": {"from": _range_start_from_end(end, 30, min_date), "to": end, "label": "Monthly Change"},
    }


def _sum_daily_range(daily: Dict[str, Any], start: str, end: str) -> float:
    total = 0.0
    for k, v in (daily or {}).items():
        dk = clean(k)
        if (not start or dk >= start) and (not end or dk <= end):
            try:
                total += float(v or 0)
            except Exception:
                pass
    return total


def _critical_at_range_end(trend: list[Dict[str, Any]], end: str, fallback: int = 0) -> int:
    pts = [p for p in (trend or []) if clean(p.get("date")) <= end]
    if pts:
        return int(pts[-1].get("critical") or 0)
    return int(fallback or 0)


def _preset_summary_for_range(
    start: str,
    end: str,
    trend: list[Dict[str, Any]],
    entered_daily: Dict[str, int],
    exited_daily: Dict[str, int],
    moved_daily: Dict[str, int],
    exited_value_daily: Optional[Dict[str, float]] = None,
    current_critical_fallback: int = 0,
) -> Dict[str, Any]:
    return {
        "from": start,
        "to": end,
        "criticalNow": _critical_at_range_end(trend, end, current_critical_fallback),
        "enteredCritical": int(_sum_daily_range(entered_daily, start, end)),
        "exitedCritical": int(_sum_daily_range(exited_daily, start, end)),
        "movedCritical": int(_sum_daily_range(moved_daily, start, end)),
        "exitedTicketsTotalValue": round(_sum_daily_range(exited_value_daily or {}, start, end), 2),
        "source": "python pre-calculated preset",
    }

def _build_team_view(
    claim_label: str,
    snap: Dict[str, Dict[str, Any]],
    history_events: list[Dict[str, Any]],
    generated_at: str,
) -> Dict[str, Any]:
    claim_filter = "" if claim_label == "All Claims" else claim_label
    ticket_by_id = {clean(v.get("id")): v for v in snap.values() if clean(v.get("id"))}

    def ticket_matches(t: Dict[str, Any]) -> bool:
        return (not claim_filter) or clean(t.get("claimType")) == claim_filter

    def event_matches(e: Dict[str, Any]) -> bool:
        return (not claim_filter) or _event_claim_type(e, ticket_by_id) == claim_filter

    critical_now = [t for t in snap.values() if t.get("isCritical") and ticket_matches(t)]
    by_dealer: Dict[str, int] = {}
    type_mix: Dict[str, int] = {}
    status_mix: Dict[str, int] = {}

    for t in critical_now:
        dealer = clean(t.get("dealer")) or "Unknown"
        by_dealer[dealer] = by_dealer.get(dealer, 0) + 1
        ct = clean(t.get("claimType")) or "Unknown"
        type_mix[ct] = type_mix.get(ct, 0) + 1
        st = clean(t.get("statusText")) or clean(t.get("statusCode")) or "Unknown"
        status_mix[st] = status_mix.get(st, 0) + 1

    entered_daily: Dict[str, int] = {}
    exited_daily: Dict[str, int] = {}
    moved_daily: Dict[str, int] = {}
    exited_value_daily: Dict[str, float] = {}
    matching_events: list[Dict[str, Any]] = []
    for e in history_events:
        dkey = event_date_key(e)
        if not dkey or dkey < DASHBOARD_MIN_DATE or not event_matches(e):
            continue
        matching_events.append(e)
        typ = event_type(e)
        if typ == "entered":
            entered_daily[dkey] = entered_daily.get(dkey, 0) + 1
        elif typ == "exited":
            exited_daily[dkey] = exited_daily.get(dkey, 0) + 1
            # Exited tickets total value: current project uses ticket.AmountIncludingTax
            # as the repairer invoice value source. Prefer the current ticket snapshot
            # amount, then fall back to the event amount if the ticket is no longer found.
            tid = event_ticket_id(e)
            t = ticket_by_id.get(tid, {}) if tid else {}
            amt = (t.get("amount") if isinstance(t, dict) and t.get("amount") not in (None, "") else e.get("amount"))
            exited_value_daily[dkey] = exited_value_daily.get(dkey, 0.0) + parse_amount(amt)
        elif typ == "moved":
            moved_daily[dkey] = moved_daily.get(dkey, 0) + 1

    generated_day = date_key(generated_at) or now_iso()[:10]
    dates = _date_range_points(DASHBOARD_MIN_DATE, generated_day)
    entered_cum = 0
    exited_cum = 0
    trend: list[Dict[str, Any]] = []
    current_critical = len(critical_now)

    for d in dates:
        entered_cum += int(entered_daily.get(d, 0) or 0)
        exited_cum += int(exited_daily.get(d, 0) or 0)
        # Reconstruct stock for each day from today's current critical stock.
        # This preserves the original is_critical/classify logic and avoids
        # treating critical total as cumulative created tickets.
        entered_after = sum(int(v or 0) for k, v in entered_daily.items() if k > d)
        exited_after = sum(int(v or 0) for k, v in exited_daily.items() if k > d)
        critical_stock = max(0, current_critical - entered_after + exited_after)
        trend.append({
            "date": d,
            "entered": int(entered_cum),
            "exited": int(exited_cum),
            "critical": int(critical_stock),
        })

    entered_total = sum(int(v or 0) for v in entered_daily.values())
    exited_total = sum(int(v or 0) for v in exited_daily.values())
    exit_to_entry_rate = round((exited_total / entered_total) * 100) if entered_total else 0
    exited_total_value = round(sum(float(v or 0) for v in exited_value_daily.values()), 2)
    scoped_tickets = [t for t in snap.values() if ticket_matches(t)]
    approved_tickets = [t for t in scoped_tickets if bool(t.get("isApproved"))]
    unapproved_tickets = [t for t in scoped_tickets if not bool(t.get("isApproved"))]
    approved_value = round(sum(parse_amount(t.get("amount")) for t in approved_tickets), 2)
    unapproved_value = round(sum(parse_amount(t.get("amount")) for t in unapproved_tickets), 2)

    handling_speed = calculate_handling_speed(matching_events, critical_now, ticket_by_id)
    preset_ranges = _preset_ranges_for(generated_day, DASHBOARD_MIN_DATE)
    preset_summaries = {
        key: _preset_summary_for_range(
            val.get("from", ""), val.get("to", ""), trend, entered_daily, exited_daily, moved_daily, exited_value_daily, current_critical
        )
        for key, val in preset_ranges.items()
    }

    return {
        "label": claim_label,
        "key": _claim_key(claim_label),
        "summary": {
            "totalTickets": len(scoped_tickets),
            "approvedTickets": len(approved_tickets),
            "unapprovedTickets": len(unapproved_tickets),
            "approvedValue": approved_value,
            "unapprovedValue": unapproved_value,
            "approvalRule": "Approved = Z1Z8TimeConsumed totalMinutes > 0; Unapproved = empty or <= 0.",
            "criticalNow": current_critical,
            "enteredCritical": entered_total,
            "exitedCritical": exited_total,
            "movedCritical": sum(int(v or 0) for v in moved_daily.values()),
            "exitToEntryRate": exit_to_entry_rate,
            "exitedTicketsTotalValue": exited_total_value,
            "exitedTicketsValueFormula": "Sum of AmountIncludingTax for exited critical tickets; AmountIncludingTax is the current repairer invoice value source.",
            "exitRateFormula": "Deprecated in UI; kept for backward compatibility.",
            # Backward-compatible fields for older HTML.
            "enteredCriticalStored": entered_total,
            "exitedCriticalStored": exited_total,
            "movedCriticalStored": sum(int(v or 0) for v in moved_daily.values()),
        },
        "currentCriticalRows": [{"id": clean(t.get("id")), "dealer": clean(t.get("dealer")) or "Unknown", "status": clean(t.get("statusText")) or clean(t.get("statusCode")) or "Unknown", "amount": t.get("amount", 0), "created": clean(t.get("created")), "isApproved": bool(t.get("isApproved"))} for t in critical_now],
        "logs": [{"id": event_ticket_id(e), "date": event_date_key(e), "detectedAt": event_time(e), "cls": ("enter" if event_type(e)=="entered" else ("exit" if event_type(e)=="exited" else "move")), "dealer": clean((ticket_by_id.get(event_ticket_id(e), {}) or {}).get("dealer") or e.get("dealer") or "Unknown"), "fromStatus": clean(e.get("fromStatus") or e.get("oldStatus")), "toStatus": clean(e.get("toStatus") or e.get("newStatus")), "amount": parse_amount((ticket_by_id.get(event_ticket_id(e), {}) or {}).get("amount") or e.get("amount"))} for e in matching_events],
        "topDealers": [{"dealer": k, "criticalTickets": v} for k, v in sorted(by_dealer.items(), key=lambda kv: (-kv[1], kv[0]))],
        "ticketTypeMix": [{"type": k, "count": v} for k, v in sorted(type_mix.items(), key=lambda kv: (-kv[1], kv[0]))],
        "statusMix": [{"status": k, "count": v} for k, v in sorted(status_mix.items(), key=lambda kv: (-kv[1], kv[0]))],
        "handlingSpeed": handling_speed,
        "presetRanges": preset_ranges,
        "presetSummaries": preset_summaries,
        "daily": {
            "entered": entered_daily,
            "exited": exited_daily,
            "moved": moved_daily,
            "exitedValue": exited_value_daily,
        },
        "trend": trend,
    }


def build_team_analytics(snap: Dict[str, Dict[str, Any]], history_events: list[Dict[str, Any]], generated_at: str) -> Dict[str, Any]:
    claim_labels = ["All Claims", "In Field Warranty Claims", "Pre Delivery Warranty Claims"]
    views = { _claim_key(label): _build_team_view(label, snap, history_events, generated_at) for label in claim_labels }
    all_view = views["all"]
    return {
        "generatedAt": generated_at,
        "minDate": DASHBOARD_MIN_DATE,
        "claimOptions": [{"key": _claim_key(label), "label": label} for label in claim_labels],
        "views": views,
        "defaultView": "all",
        # Backward-compatible top-level fields for older HTML.
        "summary": all_view["summary"],
        "topDealers": all_view["topDealers"],
        "ticketTypeMix": all_view["ticketTypeMix"],
        "statusMix": all_view["statusMix"],
        "daily": all_view["daily"],
        "trend": all_view["trend"],
        "storageNote": "Pre-calculated by Python. Claim buttons and Today/Weekly/Monthly preset ranges use pre-built values; manual From/To changes are recalculated in the browser.",
    }


def write_analytics(source_root: str, monitor_root: str, snap: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
    print("[ANALYTICS] Building pre-calculated dashboard nodes ...")
    if snap is None:
        snap = build_snapshot(source_root)
    ts = now_iso()
    history_events = load_all_history(monitor_root)
    dealer_payload = build_dealer_analytics(source_root, monitor_root, history_events, ts)
    payload = {
        "meta": {
            "generatedAt": ts,
            "sourceRoot": source_root,
            "monitorRoot": monitor_root,
            "version": "python-precalculated-analytics-v4_chunked_dealer_writes",
            "historyEventsRead": len(history_events),
            "ticketSnapshotSize": len(snap),
        },
        "team": build_team_analytics(snap, history_events, ts),
        "employee": build_employee_analytics(snap, history_events, ts),
        "dealer": dealer_payload,
    }

    # Reconcile Employee Workbench KPIs with Team Dashboard.
    # The Team Dashboard Total Critical is the source of truth for current critical stock.
    # Employee analytics can have extra grouping rules for role 40 / Queue Warranty / hidden test owners,
    # but the top KPI must match Team Dashboard for the same claim filter.
    try:
        team_views = (payload.get("team") or {}).get("views", {}) or {}
        emp_views = (payload.get("employee") or {}).get("views", {}) or {}
        for view_key, emp_view in emp_views.items():
            team_view = team_views.get(view_key) or {}
            team_summary = team_view.get("summary", {}) if isinstance(team_view, dict) else {}
            emp_summary = emp_view.setdefault("summary", {}) if isinstance(emp_view, dict) else {}
            critical_now = int(team_summary.get("criticalNow") or 0)
            emp_summary["criticalTickets"] = critical_now
            emp_summary["totalTickets"] = critical_now
            emp_summary["teamCriticalNowSource"] = "analytics/team/views/%s/summary/criticalNow" % view_key
            # Reconcile visible employee/queue rows too, so the blue bar total is auditable.
            visible_total = 0
            for row in emp_view.get("stats", []) or []:
                try:
                    visible_total += int(row.get("criticalTickets") or row.get("totalTickets") or 0)
                except Exception:
                    pass
            delta = critical_now - visible_total
            emp_summary["visibleCriticalTickets"] = visible_total
            emp_summary["unshownCriticalTickets"] = delta if delta > 0 else 0
            if delta > 0:
                stats = emp_view.setdefault("stats", [])
                stats.append({
                    "name": "Unassigned / Missing role 40",
                    "key": "unassigned_missing_role40",
                    "totalTickets": delta,
                    "criticalTickets": delta,
                    "removedTotal": 0,
                    "removedDaily": {},
                    "rank": len(stats) + 1,
                    "note": "Added by analytics reconciliation so Employee critical total matches Team Dashboard."
                })
    except Exception as reconcile_error:
        payload["meta"]["employeeTeamReconcileError"] = str(reconcile_error)
    # Write analytics in smaller Firebase requests.
    # RTDB rejects one very large PATCH with:
    # "Data to write exceeds the maximum size that can be modified with a single request."
    # Dealer analytics can be large because it contains ticket/log/material detail rows,
    # so write the index first and then write each dealer under byDealer/{dealerKey}.
    root_ref = db.reference(monitor_root)
    analytics_ref = root_ref.child("analytics")

    dealer_index = {k: v for k, v in payload["dealer"].items() if k != "byDealer"}
    dealer_by_dealer = payload["dealer"].get("byDealer", {}) or {}

    analytics_ref.child("meta").set(payload["meta"])
    analytics_ref.child("employee").set(payload["employee"])
    analytics_ref.child("team").set(payload["team"])
    analytics_ref.child("dealer").child("index").set(dealer_index)

    by_dealer_ref = analytics_ref.child("dealer").child("byDealer")
    by_dealer_ref.delete()
    for dealer_key, dealer_data in dealer_by_dealer.items():
        by_dealer_ref.child(safe_key(dealer_key)).set(dealer_data)

    # Backward-compatible aliases for older HTML files. Keep them small enough
    # to avoid the same large PATCH problem: dealerAnalytics intentionally does
    # not contain the heavy byDealer detail payload.
    root_ref.child("employeeAnalytics").set(payload["employee"])
    root_ref.child("dealerAnalytics").set(dealer_index)
    print(
        f"[ANALYTICS DONE] employees={payload['employee']['summary']['employees']}, "
        f"employeeCriticalTickets={payload['employee']['summary']['totalTickets']}, "
        f"removed={payload['employee']['summary']['criticalRemoved']}, "
        f"dealers={payload['dealer']['summary']['dealers']}, "
        f"dealerCritical={payload['dealer']['summary']['criticalTickets']}"
    )

def clear_history_only_when_explicitly_requested(monitor_root: str) -> None:
    root_ref = db.reference(monitor_root)
    # Dangerous operation. Only run when the user explicitly passes --clear-history-on-reset.
    root_ref.child("history").delete()
    root_ref.child("statusLog").delete()
    root_ref.child("statusByTicket").delete()


def reset_baseline(source_root: str, monitor_root: str, clear_history: bool = False) -> None:
    print("[RESET] Building clean baseline from current Firebase data ...")
    snap = build_snapshot(source_root)

    if clear_history:
        print("[RESET] --clear-history-on-reset was provided. Deleting old history/statusLog/statusByTicket ...")
        clear_history_only_when_explicitly_requested(monitor_root)
    else:
        print("[RESET] History-safe mode: keeping existing /history and processed/unprocessed records.")

    ts = now_iso()

    # IMPORTANT:
    # Do NOT write {"history": {}} here unless the user explicitly asked to clear history.
    # Updating currentStatus/meta must not remove old logs.
    db.reference(monitor_root).child("currentStatus").set(snap)
    db.reference(monitor_root).child("meta").update({
        "baselineCreatedAt": ts,
        "lastScanAt": ts,
        "previousScanAt": "",
        "baselineSize": len(snap),
        "lastEventsWritten": 0,
        "comparePolicy": "python_history_safe_baseline_no_history_delete",
        "scanIntervalSeconds": 3600,
        "version": "python-history-safe-1h-mandt800-rejection-v19-new-critical-log-fix",
        "storage": f"firebase:/{monitor_root}",
        "historyRetention": "permanent_until_manual_delete",
        "note": "Baseline reset by Python. Existing history was preserved unless --clear-history-on-reset was used. This run creates ZERO change logs.",
    })

    write_analytics(source_root, monitor_root, snap)

    critical_now = sum(1 for x in snap.values() if x.get("isCritical"))
    print(f"[RESET DONE] baseline tickets={len(snap)}, criticalNow={critical_now}, events=0")
    if clear_history:
        print(f"[RESET DONE] Cleared old /{monitor_root}/history by explicit request.")
    else:
        print(f"[RESET DONE] Preserved old /{monitor_root}/history. Only /currentStatus baseline was refreshed.")


def classify(prev: Dict[str, Any], cur: Dict[str, Any]) -> Optional[tuple[str, str]]:
    prev_crit = bool(prev.get("isCritical"))
    cur_crit = bool(cur.get("isCritical"))

    prev_code = clean(prev.get("code") or prev.get("statusCode"))
    cur_code = clean(cur.get("code") or cur.get("statusCode"))
    prev_text = clean(prev.get("statusText"))
    cur_text = clean(cur.get("statusText"))
    prev_sig = clean(prev.get("signature"))
    cur_sig = clean(cur.get("signature"))

    if (not prev_crit) and cur_crit:
        return "Entered critical", "enter"
    if prev_crit and (not cur_crit):
        return "Exited critical", "exit"
    if prev_crit and cur_crit and (prev_code != cur_code or prev_text != cur_text or prev_sig != cur_sig):
        return "Critical status changed", "move"
    return None


def compare_and_write(source_root: str, monitor_root: str) -> None:
    print("[COMPARE] Loading current Firebase tickets ...")
    snap = build_snapshot(source_root)
    prev = db.reference(f"{monitor_root}/currentStatus").get() or {}
    if not isinstance(prev, dict) or not prev:
        print("[COMPARE] No baseline found. Creating baseline only. events=0")
        # History-safe: do not delete existing history just because currentStatus is missing.
        reset_baseline(source_root, monitor_root, clear_history=False)
        return

    meta = db.reference(f"{monitor_root}/meta").get() or {}
    previous_scan_at = clean(meta.get("lastScanAt") if isinstance(meta, dict) else "")
    ts = now_iso()

    history_patch: Dict[str, Any] = {}
    entered = moved = exited = 0

    for tid_key, cur in snap.items():
        old = prev.get(tid_key)

        # Important v19 fix:
        # If a ticket is newly added after the baseline and it is already in a
        # critical status such as Z1 New Claim, it must still appear in the
        # Critical status change log as Entered critical. The older version
        # skipped all new tickets, which caused 29 May New Claim critical
        # tickets to be missing from the Dealer log.
        if not isinstance(old, dict):
            if not bool(cur.get("isCritical")):
                continue
            old = {
                "code": "",
                "statusCode": "",
                "statusText": "",
                "signature": "NEW_TICKET",
                "isCritical": False,
                "name": "",
                "dealer": clean(cur.get("dealer") or "Unknown"),
            }
            kind = ("Entered critical", "enter")
        else:
            kind = classify(old, cur)

        if not kind:
            continue

        typ, cls = kind
        if typ == "Entered critical":
            entered += 1
        elif typ == "Exited critical":
            exited += 1
        else:
            moved += 1

        # Deterministic event key:
        # If history write succeeds but currentStatus update is interrupted, rerunning the same window
        # patches the same event instead of creating duplicates. It also prevents a later hourly run
        # from deleting or overwriting older unprocessed rows.
        event_key = safe_key(
            f"{tid_key}_{old.get('code','')}_{cur.get('code','')}_"
            f"{old.get('signature','')}_{cur.get('signature','')}_{previous_scan_at or 'baseline'}"
        )
        history_patch[event_key] = {
            "id": cur.get("id") or tid_key,
            "type": typ,
            "cls": cls,
            "detectedAt": ts,
            "dataSyncAt": ts,
            "windowStartAt": previous_scan_at,
            "windowEndAt": ts,
            "fromCode": clean(old.get("code") or old.get("statusCode")),
            "fromStatus": clean(old.get("statusText")),
            "toCode": clean(cur.get("code") or cur.get("statusCode")),
            "toStatus": clean(cur.get("statusText")),
            "fromCritical": bool(old.get("isCritical")),
            "toCritical": bool(cur.get("isCritical")),
            "name": clean(cur.get("name") or old.get("name")),
            "dealer": clean(cur.get("dealer") or old.get("dealer") or "Unknown"),
            "amount": cur.get("amount") or 0,
            "created": cur.get("created") or "",
            "chassis": cur.get("chassis") or "",
            "serial": cur.get("serial") or "",
            "employee": clean(cur.get("employee") or old.get("employee") or "Unknown"),
            "role40Employee": clean(cur.get("role40Employee") or old.get("role40Employee") or ""),
            "assignedToRaw": clean(cur.get("assignedToRaw") or old.get("assignedToRaw") or ""),
            "claimType": clean(cur.get("claimType") or old.get("claimType") or ""),
            "source": "python_v33_role40_z1_queue_logic",
        }

    update_payload: Dict[str, Any] = {
        "currentStatus": snap,
        "meta": {
            "lastScanAt": ts,
            "previousScanAt": previous_scan_at,
            "baselineSize": len(snap),
            "lastEventsWritten": len(history_patch),
            "enteredCritical": entered,
            "criticalStatusChanged": moved,
            "exitedCritical": exited,
            "comparePolicy": "python_history_safe_critical_only_append_history_every_1_hour_mandt800_rejection_new_critical_tickets_logged",
            "scanIntervalSeconds": 3600,
            "version": "python-history-safe-1h-mandt800-rejection-v19-new-critical-log-fix",
            "storage": f"firebase:/{monitor_root}",
        }
    }

    if history_patch:
        update_payload["history"] = history_patch

    # Important:
    # - currentStatus is replaced with current snapshot.
    # - history is patched only when real changes exist.
    # - no logs are generated for initialization.
    # Write history first. Only after history succeeds do we advance currentStatus.
    # Never call .set({}) or .delete() on /history in a normal hourly run.
    if history_patch:
        db.reference(monitor_root).child("history").update(history_patch)

    db.reference(monitor_root).child("currentStatus").set(snap)

    try:
        existing_history = db.reference(monitor_root).child("history").get() or {}
        if isinstance(existing_history, dict):
            update_payload["meta"]["totalHistoryStored"] = len(existing_history)
    except Exception:
        pass

    db.reference(monitor_root).child("meta").update(update_payload["meta"])

    write_analytics(source_root, monitor_root, snap)

    critical_now = sum(1 for x in snap.values() if x.get("isCritical"))
    print(
        f"[COMPARE DONE] tickets={len(snap)}, criticalNow={critical_now}, "
        f"entered={entered}, moved={moved}, exited={exited}, events={len(history_patch)}"
    )


def run_company_fetch(company_file: str, db_url: str, sa_path: str, source_root: str, monitor_root: str) -> None:
    p = Path(company_file)
    if not p.exists():
        raise SystemExit(f"Cannot find company fetch file:\n{p}")

    env = os.environ.copy()
    env["FIREBASE_DB_URL"] = db_url
    env["FIREBASE_SA_PATH"] = sa_path
    env["FIREBASE_ROOT"] = source_root
    env["SOURCE_ROOT"] = source_root
    env["MONITOR_ROOT"] = monitor_root
    env["PYTHONUNBUFFERED"] = "1"

    print(f"[FETCH] Running company file:\n{p}")
    result = subprocess.run([sys.executable, str(p)], env=env)
    if result.returncode != 0:
        raise SystemExit(
            f"公司 fetch 失败，exit code={result.returncode}。本次不做 compare，避免错误日志。\n"
            f"最常见原因：缺少 openpyxl。先运行 install_requirements.bat。"
        )
    print("[FETCH DONE] Company fetch finished successfully.")


def default_company_file() -> str:
    env_path = clean(os.getenv("ORIGINAL_SCRIPT_PATH"))
    if env_path:
        return env_path
    cwd_candidate = Path.cwd() / DEFAULT_COMPANY_FILE
    if cwd_candidate.exists():
        return str(cwd_candidate)
    script_candidate = Path(__file__).resolve().parent / DEFAULT_COMPANY_FILE
    return str(script_candidate)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run company fetch once, then compare once.")
    ap.add_argument("--auto", action="store_true", help="Run automatically. Use --interval-hours 1 for hourly monitor.")
    ap.add_argument("--reset-baseline", action="store_true", help="Create/refresh baseline. History is preserved unless --clear-history-on-reset is also provided.")
    ap.add_argument("--clear-history-on-reset", action="store_true", help="DANGEROUS: delete old history/statusLog/statusByTicket during reset-baseline.")
    ap.add_argument("--skip-fetch", action="store_true", help="Only compare current Firebase, do not run company fetch.")
    ap.add_argument("--rebuild-analytics-only", action="store_true", help="Only rebuild /analytics nodes from current Firebase data. No fetch, no compare, no history logs.")
    ap.add_argument("--interval-hours", type=float, default=1.0)
    ap.add_argument("--company-file", default=default_company_file())
    ap.add_argument("--firebase-db-url", default=os.getenv("FIREBASE_DB_URL", DEFAULT_DB_URL))
    ap.add_argument("--firebase-sa-path", default=os.getenv("FIREBASE_SA_PATH", str(Path.cwd() / "firebase-service-account.json")))
    ap.add_argument("--source-root", default=os.getenv("SOURCE_ROOT", DEFAULT_SOURCE_ROOT))
    ap.add_argument("--monitor-root", default=os.getenv("MONITOR_ROOT", DEFAULT_MONITOR_ROOT))
    return ap.parse_args()


def run_once(args: argparse.Namespace) -> None:
    if not args.skip_fetch:
        run_company_fetch(args.company_file, args.firebase_db_url, args.firebase_sa_path, args.source_root, args.monitor_root)
    else:
        print("[FETCH] Skipped company fetch. Comparing existing Firebase only.")

    # First update critical history/currentStatus from the latest tickets.
    compare_and_write(args.source_root, args.monitor_root)

    # Then rebuild all ready-to-display dashboard analytics.
    # This keeps Team / Dealer pages fresh after every scheduled run,
    # so you do not need to run rebuild_analytics_only separately after fetch.
    write_analytics(args.source_root, args.monitor_root)
    db.reference(args.monitor_root).child("automation").update({
        "lastRunFinishedAt": now_iso(),
        "lastRunMode": "once",
        "sourceRoot": args.source_root,
        "monitorRoot": args.monitor_root,
        "status": "success",
    })


def main() -> None:
    args = parse_args()
    init_firebase(args.firebase_db_url, args.firebase_sa_path)

    if args.rebuild_analytics_only:
        write_analytics(args.source_root, args.monitor_root)
        return

    if args.reset_baseline:
        reset_baseline(args.source_root, args.monitor_root, clear_history=args.clear_history_on_reset)
        return

    if args.once:
        run_once(args)
        return

    if args.auto:
        interval = max(300, int(args.interval_hours * 3600))
        print(f"[AUTO] Started. Interval = {args.interval_hours} hours. Press Ctrl+C to stop.")
        while True:
            started = time.time()
            try:
                run_once(args)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"[AUTO ERROR] {e}")
            sleep_s = max(10, interval - int(time.time() - started))
            print(f"[AUTO] Sleeping {sleep_s} seconds ...")
            time.sleep(sleep_s)
        return

    print("Nothing to do. Use --reset-baseline, --once, or --auto.")


if __name__ == "__main__":
    main()
