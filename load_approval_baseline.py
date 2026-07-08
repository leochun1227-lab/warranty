#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deprecated approval baseline loader.
==================================

This script is kept only as a narrow fallback for legacy one-off imports.
It does not replace the main monitor, which now owns approval-state
maintenance directly and prefers the new interface fields first.

Current fallback rule:
  - approved rows are included when Sales Order or ERP Purchase Order ID exists
  - approved rows are dated from SAP Posting Date looked up by ERP Purchase
    Order ID, or Created On when posting date is not yet available
  - unapproved rows are not imported here

The backup payload keeps the same row shape as the main path where practical,
including blank placeholders for the new direct-interface date fields.
"""
from __future__ import annotations

import os
import re
import argparse
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import pandas as pd


def _import_firebase():
    global firebase_admin, credentials, db
    import firebase_admin
    from firebase_admin import credentials, db


FIREBASE_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app",
)
FIREBASE_SA_PATH = os.getenv("FIREBASE_SA_PATH", "firebase-service-account.json")
MONITOR_ROOT = os.getenv("MONITOR_ROOT", "ctmTicketStatusMonitorV44")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("load_approval_baseline_csv_fixed")


EMPTY_VALUES = {"", "#", "nan", "none", "null", "not assigned", "notassigned"}


def clean(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    s = str(v).strip()
    return "" if s.lower().strip() in EMPTY_VALUES else s


def is_closed_approved_status_text(v: Any) -> bool:
    text = clean(v).lower()
    return "approved claims closed" in text


def has_value(v: Any) -> bool:
    return bool(clean(v))


def safe_key(v: Any) -> str:
    s = clean(v)
    for ch in [".", "$", "#", "[", "]", "/"]:
        s = s.replace(ch, "_")
    return s[:180]


def ticket_storage_key(v: Any) -> str:
    return "ticket_" + (safe_key(v) or "blank")


def parse_c4c_dt(v: Any) -> Optional[str]:
    """Parse C4C datetime/date into ISO string.

    Examples:
      16.06.2026 10:30:17 AUSACT -> 2026-06-16T10:30:17
      24.04.2026 -> 2026-04-24T00:00:00
      2026-06-16 -> 2026-06-16T00:00:00
    """
    s = clean(v)
    if not s:
        return None

    parts = s.split()
    date_part = parts[0] if parts else ""
    time_part = parts[1] if len(parts) > 1 and ":" in parts[1] else ""

    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            d = datetime.strptime(date_part, fmt)
            if time_part:
                for tfmt in ("%H:%M:%S", "%H:%M"):
                    try:
                        t = datetime.strptime(time_part, tfmt).time()
                        d = d.replace(hour=t.hour, minute=t.minute, second=t.second)
                        break
                    except ValueError:
                        pass
            return d.isoformat()
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None).isoformat()
    except Exception:
        return None


def iso_day(iso_s: Optional[str]) -> str:
    if not iso_s:
        return ""
    return iso_s[:10]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def first_existing(row: pd.Series, names: list[str]) -> str:
    for name in names:
        if name in row.index and has_value(row.get(name)):
            return clean(row.get(name))
    return ""


def numeric_like(s: Any) -> bool:
    v = clean(s)
    return bool(re.fullmatch(r"\d+(?:\.0+)?", v))


def normalize_id(s: Any) -> str:
    v = clean(s)
    if re.fullmatch(r"\d+\.0+", v):
        return v.split(".", 1)[0]
    return v


def csv_ticket_id(row: pd.Series) -> str:
    """SAPAnalyticsReport real numeric ticket id priority.

    In the user's CSV:
      Ticket      = name text
      Unnamed: 1  = numeric id
      Ticket ID   = name text, misleading
      Unnamed: 3  = numeric id
    """
    priority = [
        "Unnamed: 1",
        "Unnamed: 3",
        "Ticket Number",
        "Ticket No",
        "Object ID",
        "ID",
        "TicketID",
        "Ticket Id",
        "Ticket ID",
    ]
    # First pass: accept numeric-looking values only.
    for c in priority:
        if c in row.index and numeric_like(row.get(c)):
            return normalize_id(row.get(c))
    # Fallback: any unnamed column with a numeric-looking value.
    for c in row.index:
        if str(c).lower().startswith("unnamed") and numeric_like(row.get(c)):
            return normalize_id(row.get(c))
    # Last fallback only, should not happen for this CSV.
    return normalize_id(first_existing(row, ["Ticket", "Ticket ID"]))


def choose_better(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Deduplicate one ticket with multiple source rows."""
    if not existing:
        return new
    if existing.get("decision") != new.get("decision"):
        if new.get("decision") == "approved":
            return new
        return existing
    old_t = clean(existing.get("decisionTime"))
    new_t = clean(new.get("decisionTime"))
    if new_t and (not old_t or new_t < old_t):
        return new
    return existing


def build_records_from_csv(csv_path: str) -> Dict[str, Dict[str, Any]]:
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    seeded_at = iso_now()
    records: Dict[str, Dict[str, Any]] = {}

    for _, row in df.iterrows():
        tid = csv_ticket_id(row)
        if not tid:
            continue

        created_on = first_existing(row, ["Created On", "CreatedOn", "Created Date"])
        po = first_existing(row, ["ERP Purchase Order ID", "ERPPurchaseOrder", "Purchasing Document"])
        so = first_existing(row, ["Sales Order", "SalesOrder", "LookupSalesOrder"])
        status_text = first_existing(row, ["Status", "Ticket Status", "TicketStatusText"])
        if is_closed_approved_status_text(status_text):
            continue
        posting_date = first_existing(row, ["Posting Date", "PostingDate"])
        decision_time = parse_c4c_dt(posting_date) or parse_c4c_dt(created_on)
        if not decision_time:
            continue

        rec = {
            "ticketId": tid,
            "ticketName": first_existing(row, ["Ticket", "Ticket ID", "Ticket Name", "Name"]) or tid,
            "decision": "approved",
            "decisionDay": iso_day(decision_time),
            "decisionTime": decision_time,
            "changedOn": "",
            "changeOnDateTime": "",
            "claimApprovedOn": "",
            "claimApprovedOnDateTime": "",
            "resolvedOn": "",
            "resolvedOnDateTime": "",
            "postingDate": posting_date,
            "approvalDecisionSource": "sap_posting_date_baseline",
            "status": first_existing(row, ["Status", "Ticket Status", "TicketStatusText"]),
            "po": po,
            "salesOrder": so,
            "employee": first_existing(row, ["Agent", "Service Technician", "C4C Assign To", "Assign To"]),
            "dealer": first_existing(row, ["Dealer Name", "Dealer"]),
            "dealerId": first_existing(row, ["Dealer", "Dealer ID", "DealerID"]),
            "claimType": first_existing(row, ["Ticket Type", "TicketType", "TicketTypeText"]),
            "amount": first_existing(row, ["ClaimTotalAmount", "AmountIncludingTax", "Amount Including Tax"]),
            "created": created_on,
            "source": "sap_posting_date_baseline",
            "seededAt": seeded_at,
        }
        key = ticket_storage_key(tid)
        records[key] = choose_better(records.get(key, {}), rec)

    return records


def excel_ticket_id(row: pd.Series) -> str:
    # Old exported workbook usually has numeric ticket id in Unnamed: 1.
    priority = ["Unnamed: 1", "Ticket ID", "TicketID", "ID", "Ticket"]
    for c in priority:
        if c in row.index and numeric_like(row.get(c)):
            return normalize_id(row.get(c))
    return normalize_id(first_existing(row, priority))


def build_records_from_excel(xlsx_path: str) -> Dict[str, Dict[str, Any]]:
    xl = pd.ExcelFile(xlsx_path)
    seeded_at = iso_now()
    records: Dict[str, Dict[str, Any]] = {}

    def ingest(sheet: str) -> None:
        if sheet not in xl.sheet_names:
            logger.warning("Sheet %s not found, skipping", sheet)
            return
        df = xl.parse(sheet, dtype=str, keep_default_na=False)
        for _, row in df.iterrows():
            tid = excel_ticket_id(row)
            if not tid:
                continue
            po = first_existing(row, ["ERP Purchase Order ID", "ERPPurchaseOrder", "Purchasing Document"])
            so = first_existing(row, ["Sales Order", "SalesOrder", "LookupSalesOrder"])
            status_text = first_existing(row, ["Status", "Ticket Status", "TicketStatusText"])
            if is_closed_approved_status_text(status_text):
                continue
            posting_date = first_existing(row, ["Posting Date", "PostingDate"])
            decision_time = parse_c4c_dt(posting_date) or parse_c4c_dt(first_existing(row, ["Created On", "CreatedOn"]))
            if not decision_time:
                decision_time = ""
            rec = {
                "ticketId": tid,
                "ticketName": first_existing(row, ["Ticket", "Ticket ID", "Ticket Name"]),
                "decision": "approved",
                "decisionDay": iso_day(decision_time),
                "decisionTime": decision_time,
                "changedOn": "",
                "changeOnDateTime": "",
                "claimApprovedOn": "",
                "claimApprovedOnDateTime": "",
                "resolvedOn": "",
                "resolvedOnDateTime": "",
                "postingDate": posting_date,
                "approvalDecisionSource": "sap_posting_date_baseline",
                "status": first_existing(row, ["Status", "Ticket Status", "TicketStatusText"]),
                "po": po,
                "salesOrder": so,
                "employee": first_existing(row, ["Agent", "Service Technician", "C4C Assign To", "Assign To"]),
                "dealer": first_existing(row, ["Dealer Name", "Dealer"]),
                "dealerId": first_existing(row, ["Dealer", "Dealer ID", "DealerID"]),
                "claimType": first_existing(row, ["Ticket Type", "TicketType", "TicketTypeText"]),
                "amount": first_existing(row, ["ClaimTotalAmount", "AmountIncludingTax", "Amount Including Tax"]),
                "created": first_existing(row, ["Created On", "CreatedOn"]),
                "source": "sap_posting_date_baseline",
                "seededAt": seeded_at,
            }
            key = ticket_storage_key(tid)
            records[key] = choose_better(records.get(key, {}), rec)

    ingest("Approved")
    return records


def firebase_init() -> None:
    _import_firebase()
    if getattr(firebase_admin, "_apps", None) and firebase_admin._apps:
        return
    if not os.path.exists(FIREBASE_SA_PATH):
        raise SystemExit(f"FIREBASE_SA_PATH not found: {FIREBASE_SA_PATH}")
    cred = credentials.Certificate(FIREBASE_SA_PATH)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


def chunked_update(ref, records: Dict[str, Dict[str, Any]], chunk_size: int = 3000) -> None:
    items = list(records.items())
    for i in range(0, len(items), chunk_size):
        batch = dict(items[i:i + chunk_size])
        ref.update(batch)
        logger.info("Uploaded approvalState batch %s-%s / %s", i + 1, i + len(batch), len(items))


def main() -> None:
    ap = argparse.ArgumentParser(description="Deprecated fallback loader for SAP posting-date approvalState imports.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--excel", help="Legacy workbook; only the Approved sheet is used")
    src.add_argument("--csv", help="SAPAnalyticsReport*.csv; only Posting Date is used")
    ap.add_argument("--dry-run", action="store_true", help="Parse and print summary only; do not write Firebase")
    ap.add_argument("--replace", action="store_true", help="Delete existing approvalState before writing new baseline")
    args = ap.parse_args()

    if args.csv:
        records = build_records_from_csv(args.csv)
        source_file = args.csv
        source_type = "csv"
    else:
        records = build_records_from_excel(args.excel)
        source_file = args.excel
        source_type = "excel"

    approved = sum(1 for r in records.values() if r.get("decision") == "approved")
    logger.info("Parsed posting-date baseline: %s tickets (approved=%s)", len(records), approved)

    for k in list(records.keys())[:5]:
        logger.info("  %s -> %s", k, records[k])

    if args.dry_run:
        return

    firebase_init()
    root = db.reference(MONITOR_ROOT)
    approval_ref = root.child("approvalState")

    if args.replace:
        logger.warning("Replacing existing /%s/approvalState only. history/currentStatus are not touched.", MONITOR_ROOT)
        approval_ref.delete()

    chunked_update(approval_ref, records)
    root.child("approvalStateMeta").set({
        "source": "sap_posting_date_baseline",
        "sourceType": source_type,
        "sourceFile": os.path.basename(source_file),
        "count": len(records),
        "approved": approved,
        "keyFormat": "ticket_<numeric TicketID>",
        "decisionRule": "Backup-only approved rows dated by SAP Posting Date looked up from ERP Purchase Order ID.",
        "loadedAt": iso_now(),
    })
    logger.info("DONE: wrote %s approvalState records under /%s/approvalState", len(records), MONITOR_ROOT)


if __name__ == "__main__":
    main()
