#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Delivery Flow Aggregator (standalone, append-only)
==================================================

Purpose
-------
Build the *time series* that the Dealer Workbench "Delivery Flow" dashboard needs.

The main fetch script (fetch_all_tickets_fast_with_firebase_*.py) only stores the
CURRENT state of every ticket under  <FIREBASE_ROOT>/tickets/{id}/ticket .
It overwrites on every run, so yesterday's backlog / partially-issued counts are
lost. Trends ("is delivery flow getting faster or slower?", "is the not-issued
backlog growing?") need a per-day snapshot that is NEVER overwritten.

This script:
  1. Reads the current tickets snapshot from Firebase.
  2. Computes a daily aggregate (KPIs + flow + status mix + aging buckets +
     per-dealer breakdown) for "today" (UTC by default).
  3. Appends it under  <FIREBASE_ROOT>/deliveryFlowHistory/daily/{YYYY-MM-DD} .
     Re-running on the same day overwrites only that day's node (idempotent),
     never previous days.

It depends on the new "First Issue Date" field added to the fetch SQL
(LIKP.WADAT_IST). If that field is empty (old data not yet refreshed), the
ticket is still counted in backlog/status mix; only the leadtime average needs
the date and will simply use the rows that have it.

Run
---
    python delivery_flow_aggregator.py --once
    python delivery_flow_aggregator.py --once --as-of 2026-06-23   # backfill one day

Env / args mirror the other scripts:
    FIREBASE_DB_URL, FIREBASE_SA_PATH, FIREBASE_ROOT
"""
from __future__ import annotations

import os
import sys
import argparse
import logging
from collections import defaultdict
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import firebase_admin
from firebase_admin import credentials, db


# =================== Config ===================
FIREBASE_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app",
)
FIREBASE_SA_PATH = os.getenv(
    "FIREBASE_SA_PATH",
    str(Path(__file__).resolve().with_name("firebase-service-account.json")),
)
FIREBASE_ROOT = os.getenv("FIREBASE_ROOT", "c4cTickets_test")
HISTORY_NODE = os.getenv("DELIVERY_FLOW_NODE", "deliveryFlowHistory")
MONITOR_ROOT = os.getenv("MONITOR_ROOT", "ctmTicketStatusMonitorV44")
NISHI_E03_TAG = os.getenv("NISHI_E03_TAG", "E03")
# Nishi creator identifier. Prefer Reservation_Created_By when the fetch side
# has it, then fall back to Purchaser and the older PO tag so older snapshots
# still work.
NISHI_PURCHASER_TAG = os.getenv("NISHI_PURCHASER_TAG", "nishi")

# Aging buckets (days) for not-yet-issued backlog. Edit here if business wants
# different thresholds; the frontend reads bucket keys dynamically.
AGING_BUCKETS = [
    ("0-14", 0, 14),
    ("15-30", 15, 30),
    ("30+", 31, 10**9),
]

APPROVED_CLOSED_STATUS_CODES = {
    "Z9",  # Sales Order Approved
    "Y0",  # Partially Picked
    "Y1",  # Dispatch Parts
    "Y2",  # Repair in Progress
    "Y4",  # Repairer Invoiced Received
    "YB",  # Repairer Invoiced Processed
}

APPROVED_CLOSED_STATUS_TEXTS = {
    "sales order approved",
    "partially picked",
    "dispatch parts",
    "repair in progress",
    "repairer invoiced received",
    "repairer invoiced processed",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("delivery_flow_aggregator")


# =================== Firebase ===================
def firebase_init() -> None:
    if getattr(firebase_admin, "_apps", None) and firebase_admin._apps:
        return
    if not os.path.exists(FIREBASE_SA_PATH):
        raise SystemExit(f"FIREBASE_SA_PATH not found: {FIREBASE_SA_PATH}")
    if not FIREBASE_DB_URL:
        raise SystemExit("FIREBASE_DB_URL is empty")
    cred = credentials.Certificate(FIREBASE_SA_PATH)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


def node_to_dict(node: Any) -> Dict[str, Any]:
    """Firebase Admin SDK may return numeric-key objects as a list."""
    if isinstance(node, dict):
        return node
    if isinstance(node, list):
        return {str(i): v for i, v in enumerate(node) if v is not None}
    return {}


def safe_key(s: str) -> str:
    s = "" if s is None else str(s).strip()
    for ch in [".", "$", "#", "[", "]", "/"]:
        s = s.replace(ch, "_")
    return s


# =================== Helpers ===================
def clean(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def sales_order_key(v: Any) -> str:
    """Normalize a sales-order identifier for counting.

    Keep the displayed value unchanged, but collapse whitespace/case variants so
    one SO only counts once even if it appears in slightly different forms.
    """
    return "".join(clean(v).split()).upper()


def parse_date(s: Any) -> Optional[date]:
    s = clean(s)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # last resort: ISO prefix
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except Exception:
        return None


def to_int(v: Any) -> int:
    s = clean(v).replace(",", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def to_number(v: Any) -> float:
    s = clean(v).replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def bucket_for_age(age_days: int) -> str:
    for label, lo, hi in AGING_BUCKETS:
        if lo <= age_days <= hi:
            return label
    return AGING_BUCKETS[-1][0]


def bucket_for_delivery_count(delivery_count: int) -> str:
    if delivery_count <= 0:
        return "0"
    if delivery_count == 1:
        return "1"
    if delivery_count == 2:
        return "2"
    return "3+"


def earliest_issue_date(ticket: Dict[str, Any], details: List[Dict[str, Any]]) -> Optional[date]:
    """Use ticket-level first issue when present; otherwise fall back to item rows."""
    found: List[date] = []
    top_level = parse_date(ticket.get("First Issue Date"))
    if top_level:
        found.append(top_level)
    for item in details:
        if isinstance(item, dict):
            item_date = parse_date(item.get("First Issue Date"))
            if item_date:
                found.append(item_date)
    return min(found) if found else None


def parse_loose_date(text: Any) -> Optional[date]:
    """Best-effort parse for the SAP analytics export date strings."""
    s = clean(text)
    if not s or s == "#":
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except Exception:
        return None


def is_approved_or_closed_ticket(ticket: Dict[str, Any]) -> bool:
    code = clean(ticket.get("TicketStatus")).upper()
    text = clean(ticket.get("TicketStatusText")).lower()
    return code in APPROVED_CLOSED_STATUS_CODES or text in APPROVED_CLOSED_STATUS_TEXTS


# =================== Core aggregation ===================
def is_issued_material_item(item: Dict[str, Any], as_of: date) -> tuple[bool, Optional[date]]:
    """Return whether a Sales Order Details row counts as an issued material."""
    if clean(item.get("Material")) == "":
        return False, None
    item_rejection_status = clean(item.get("Item Rejection Status")).lower()
    rejected = item_rejection_status != "not rejected" or clean(item.get("Rejection Reason")) != ""
    if rejected:
        return False, None
    issue_date = parse_date(item.get("First Issue Date")) or parse_date(item.get("firstIssueDate"))
    if not issue_date or issue_date > as_of:
        return False, None
    return True, issue_date


def is_open_material_item(item: Dict[str, Any], as_of: date) -> bool:
    """Return whether a Sales Order Details row is still open as of the snapshot date."""
    if clean(item.get("Material")) == "":
        return False
    item_rejection_status = clean(item.get("Item Rejection Status")).lower()
    rejected = item_rejection_status != "not rejected" or clean(item.get("Rejection Reason")) != ""
    if rejected:
        return False
    issue_date = parse_date(item.get("First Issue Date")) or parse_date(item.get("firstIssueDate"))
    return not issue_date or issue_date > as_of


def aggregate(tickets_node: Dict[str, Any], as_of: date) -> Dict[str, Any]:
    """Compute one day's delivery-flow snapshot from the current tickets node."""
    tickets = node_to_dict(tickets_node)

    # SO-level accumulators. Every statistic below is derived from unique SOs,
    # while export detail rows still preserve the underlying source granularity.
    so_state: Dict[str, Dict[str, Any]] = {}

    part_leadtimes: List[int] = []     # days SO Created -> First Issue (per issued material row)
    new_so_items_today = 0             # unique SOs whose SO Created Date == as_of
    items_issued_today = 0             # issued material rows whose First Issue Date == as_of
    oldest_not_issued = 0
    open_notissued_parts = 0
    selected_monthly: Dict[str, int] = defaultdict(int)
    selected_weekly: Dict[str, int] = defaultdict(int)
    issued_monthly: Dict[str, int] = defaultdict(int)
    issued_mtd = 0
    issue_leadtime_sum_by_month: Dict[str, int] = defaultdict(int)
    issue_leadtime_count_by_month: Dict[str, int] = defaultdict(int)
    backlog_total = 0

    # Cost + Nishi E03 accumulators from ticket fields
    total_cost = 0.0
    monthly_cost: Dict[str, float] = defaultdict(float)
    so_costs: Dict[str, float] = {}
    nishi_ytd_cost = 0.0
    nishi_monthly_cost: Dict[str, float] = defaultdict(float)
    nishi_rows: List[Dict[str, Any]] = []
    ytd_start = date(as_of.year, 1, 1)

    aging_counts: Dict[str, int] = {label: 0 for label, _, _ in AGING_BUCKETS}
    delivery_count_counts: Dict[str, Dict[str, Any]] = {
        "0": {"totalItems": 0, "openItems": 0, "issuedItems": 0, "rejectedItems": 0, "_leadtimes": []},
        "1": {"totalItems": 0, "openItems": 0, "issuedItems": 0, "rejectedItems": 0, "_leadtimes": []},
        "2": {"totalItems": 0, "openItems": 0, "issuedItems": 0, "rejectedItems": 0, "_leadtimes": []},
        "3+": {"totalItems": 0, "openItems": 0, "issuedItems": 0, "rejectedItems": 0, "_leadtimes": []},
    }

    # per dealer
    dealer: Dict[str, Dict[str, Any]] = {}

    def dealer_bucket(did: str, dname: str) -> Dict[str, Any]:
        key = did or dname or "UNKNOWN"
        if key not in dealer:
            dealer[key] = {
                "dealerId": did, "dealerName": dname,
                "backlog": 0, "issuedItems": 0, "openItems": 0,
                "rejectedItems": 0, "leadtimes": [],
            }
        return dealer[key]

    for _tid, tnode in tickets.items():
        tnode = tnode if isinstance(tnode, dict) else {}
        ticket = tnode.get("ticket", {}) if isinstance(tnode.get("ticket"), dict) else {}

        sales_order = clean(ticket.get("Sales Order"))
        sales_order_count_key = sales_order_key(sales_order)
        if not sales_order:
            continue  # no SO -> not part of delivery flow

        did = clean(ticket.get("DealerID"))
        dname = clean(ticket.get("DealerName"))

        so_created = parse_date(ticket.get("SO Created Date"))
        details = ticket.get("Sales Order Details")
        details = details if isinstance(details, list) else []
        # Backfill history from the current snapshot by only counting tickets
        # that already existed on the requested as-of date.
        if not so_created or so_created > as_of:
            continue

        state = so_state.get(sales_order_count_key)
        if not state:
            state = {
                "salesOrder": sales_order,
                "soCreatedDate": so_created,
                "dealerId": did,
                "dealerName": dname,
                "isAwaiting": not is_approved_or_closed_ticket(ticket),
                "hasOpen": False,
                "hasIssued": False,
                "hasRejected": False,
                "firstIssueDate": None,
                "maxDeliveryCount": 0,
                "costSeen": False,
            }
            so_state[sales_order_count_key] = state
        else:
            if so_created < state["soCreatedDate"]:
                state["soCreatedDate"] = so_created
            if did and not state["dealerId"]:
                state["dealerId"] = did
            if dname and not state["dealerName"]:
                state["dealerName"] = dname
            state["isAwaiting"] = state["isAwaiting"] or not is_approved_or_closed_ticket(ticket)

        # Ticket-level cost. Keep the temp-logic rule: one SO counted once.
        cost = to_number(ticket.get("AmountIncludingTax"))
        if cost > 0 and not state["costSeen"]:
            state["costSeen"] = True
            state["cost"] = cost
            so_costs[sales_order_count_key] = cost
            total_cost += cost
            monthly_cost[so_created.strftime("%Y-%m")] += cost
            # ---- Nishi detection -------------------------------------------
            reservation_created_by = clean(ticket.get("Reservation_Created_By") or "")
            reservation_cost_center = clean(ticket.get("Reservation_Cost_Center") or "")
            purchaser = clean(ticket.get("Purchaser") or "")
            po = clean(ticket.get("ERPPurchaseOrder") or "")
            is_nishi = (
                (NISHI_PURCHASER_TAG and NISHI_PURCHASER_TAG.lower() in reservation_created_by.lower())
                or (NISHI_PURCHASER_TAG and NISHI_PURCHASER_TAG.lower() in reservation_cost_center.lower())
                or (NISHI_PURCHASER_TAG and NISHI_PURCHASER_TAG.lower() in purchaser.lower())
                or (NISHI_E03_TAG and NISHI_E03_TAG.upper() in reservation_cost_center.upper())
                or (NISHI_E03_TAG and NISHI_E03_TAG.upper() in po.upper())
            )
            if is_nishi and so_created >= ytd_start:
                nishi_ytd_cost += cost
                nishi_monthly_cost[so_created.strftime("%Y-%m")] += cost
                nishi_rows.append({
                    "ticketKey": clean(_tid),
                    "ticketId": clean(ticket.get("TicketID") or ticket.get("TicketId") or _tid),
                    "salesOrder": sales_order,
                    "soCreatedDate": so_created.isoformat(),
                    "amountIncludingTax": round(cost, 2),
                    "purchaser": purchaser,
                    "reservationCreatedBy": reservation_created_by,
                    "reservationCostCenter": reservation_cost_center,
                    "erppurchaseOrder": po,
                })

        for it in details:
            if not isinstance(it, dict):
                continue
            if clean(it.get("Material")) == "":
                continue
            dc = to_int(it.get("Delivery Count"))
            state["maxDeliveryCount"] = max(state["maxDeliveryCount"], dc)
            material_issued, it_issue_date = is_issued_material_item(it, as_of)
            material_open = is_open_material_item(it, as_of)

            if material_issued and it_issue_date:
                state["hasIssued"] = True
                if it_issue_date and (state["firstIssueDate"] is None or it_issue_date < state["firstIssueDate"]):
                    state["firstIssueDate"] = it_issue_date
                issue_month = it_issue_date.strftime("%Y-%m")
                issued_monthly[issue_month] += 1
                if it_issue_date.month == as_of.month and it_issue_date.year == as_of.year:
                    issued_mtd += 1
                if it_issue_date == as_of:
                    items_issued_today += 1
                lt = max(0, (it_issue_date - so_created).days)
                part_leadtimes.append(lt)
                issue_leadtime_sum_by_month[issue_month] += lt
                issue_leadtime_count_by_month[issue_month] += 1
                db_dealer = dealer_bucket(did, dname)
                db_dealer["leadtimes"].append(lt)
            elif material_open:
                state["hasOpen"] = True
                open_notissued_parts += 1
                age = max(0, (as_of - so_created).days)
                aging_counts[bucket_for_age(age)] += 1
                oldest_not_issued = max(oldest_not_issued, age)
            else:
                state["hasRejected"] = True

    awaiting_tickets = 0
    open_so_items = 0
    issued_items = 0
    rejected_items = 0
    partially_tickets = 0
    fully_tickets = 0
    notissued_tickets = 0
    selected_so_total = len(so_state)
    selected_so_today = 0

    for so_key, state in so_state.items():
        so_created = state["soCreatedDate"]
        if not so_created or so_created > as_of:
            continue

        if so_created == as_of:
            new_so_items_today += 1
            selected_so_today += 1

        selected_monthly[so_created.strftime("%Y-%m")] += 1
        week_start = so_created - timedelta(days=so_created.weekday())
        selected_weekly[week_start.isoformat()] += 1

        if state["isAwaiting"]:
            awaiting_tickets += 1

        if state["hasRejected"]:
            rejected_items += 1

        if state["hasIssued"]:
            issued_items += 1

        if state["hasOpen"]:
            open_so_items += 1

        if state["hasOpen"] and state["hasIssued"]:
            partially_tickets += 1
        elif state["hasIssued"] and not state["hasOpen"]:
            fully_tickets += 1
        elif state["hasOpen"] and not state["hasIssued"]:
            notissued_tickets += 1

        db_dealer = dealer_bucket(state["dealerId"], state["dealerName"])
        if state["hasRejected"]:
            db_dealer["rejectedItems"] += 1
        if state["hasIssued"]:
            db_dealer["issuedItems"] += 1
        if state["hasOpen"]:
            db_dealer["openItems"] += 1
        if state["hasOpen"] and not state["hasIssued"]:
            db_dealer["backlog"] += 1

        band_key = bucket_for_delivery_count(state["maxDeliveryCount"])
        band = delivery_count_counts[band_key]
        band["totalItems"] += 1
        if state["hasRejected"]:
            band["rejectedItems"] += 1
        elif state["hasIssued"]:
            band["issuedItems"] += 1
            if state["firstIssueDate"]:
                lt = max(0, (state["firstIssueDate"] - so_created).days)
                band["_leadtimes"].append(lt)
        else:
            band["openItems"] += 1

    backlog_total = open_notissued_parts
    avg_days_first_issue = round(sum(part_leadtimes) / len(part_leadtimes), 1) if part_leadtimes else 0.0
    net_backlog_change = new_so_items_today - items_issued_today

    # finalize dealers
    dealer_out: Dict[str, Any] = {}
    for key, d in dealer.items():
        lts = d.pop("leadtimes", [])
        d["avgDaysToIssue"] = round(sum(lts) / len(lts), 1) if lts else 0.0
        dealer_out[safe_key(key)] = d

    delivery_count_out: Dict[str, Any] = {}
    for bucket, payload in delivery_count_counts.items():
        lts = payload.pop("_leadtimes", [])
        payload["avgDaysToFirstIssue"] = round(sum(lts) / len(lts), 1) if lts else 0.0
        delivery_count_out[bucket] = payload

    selected_last12_weeks: List[Dict[str, Any]] = []
    current_week_start = as_of - timedelta(days=as_of.weekday())
    for i in range(11, -1, -1):
      week_start = current_week_start - timedelta(days=7 * i)
      week_end = week_start + timedelta(days=6)
      iso_week = week_start.isocalendar()
      selected_last12_weeks.append({
          "week": f"{iso_week.year}-W{iso_week.week:02d}",
          "weekStart": week_start.isoformat(),
          "weekEnd": week_end.isoformat(),
          "count": int(selected_weekly.get(week_start.isoformat(), 0)),
      })

    selected_by_month = {k: int(v) for k, v in sorted(selected_monthly.items())}
    issued_by_month = {k: int(v) for k, v in sorted(issued_monthly.items())}
    avg_issuing_by_month: Dict[str, float] = {}
    volume_by_month: Dict[str, int] = {}
    for month_key in sorted(issue_leadtime_count_by_month):
        count = issue_leadtime_count_by_month[month_key]
        volume_by_month[month_key] = count
        avg_issuing_by_month[month_key] = round(issue_leadtime_sum_by_month[month_key] / count, 1) if count else 0.0

    snapshot = {
        "asOf": as_of.isoformat(),
        "generatedAt": iso_utc_now(),
        "awaitingParts": {
            "current": awaiting_tickets,
        },
        "partsSelected": {
            "today": selected_so_today,
            "total": selected_so_total,
            "last12Weeks": selected_last12_weeks,
            "byMonth": selected_by_month,
        },
        "partsIssued": {
            "today": items_issued_today,
            "mtd": issued_mtd,
            "byMonth": issued_by_month,
        },
        "avgIssuingTime": {
            "byMonth": avg_issuing_by_month,
            "volumeByMonth": volume_by_month,
        },
        # ----- KPI tiles -----
        "kpi": {
            "openSoItems": open_so_items,
            "openNotIssuedParts": open_notissued_parts,
            "partiallyIssuedTickets": partially_tickets,
            "fullyIssuedTickets": fully_tickets,
            "notIssuedTickets": notissued_tickets,
            "avgDaysToFirstIssue": avg_days_first_issue,
            "oldestNotIssuedDays": oldest_not_issued,
        },
        # ----- main chart 1: delivery flow -----
        "flow": {
            "newSoItems": new_so_items_today,
            "itemsIssued": items_issued_today,
            "netBacklogChange": net_backlog_change,
            "backlogTotal": backlog_total,
        },
        # ----- main chart 2: status mix -----
        "statusMix": {
            "awaitingItems": awaiting_tickets,
            "openItems": open_so_items,
            "rejectedItems": rejected_items,
        },
        # ----- main chart 3: aging buckets of open backlog -----
        "agingBuckets": aging_counts,
        # ----- helper chart: per-dealer -----
        "byDealer": dealer_out,
        # ----- helper chart: delivery-count lens -----
        "deliveryCountBandOrder": ["0", "1", "2", "3+"],
        "deliveryCountBands": delivery_count_out,
        # ----- cost + Nishi E03 summary -----
        "partsCost": {
            "total": round(total_cost, 2),
            "byMonth": {k: round(v, 2) for k, v in sorted(monthly_cost.items())},
            "avgPerSO": round(total_cost / len(so_costs), 2) if so_costs else 0.0,
            "soCount": len(so_costs),
        },
        "nishiE03": {
            "ytdCost": round(nishi_ytd_cost, 2),
            "ytdYear": as_of.year,
            "count": len(nishi_rows),
            "byMonth": {k: round(v, 2) for k, v in sorted(nishi_monthly_cost.items())},
        },
        "_nishiRows": nishi_rows,
    }
    return snapshot


# =================== Write ===================
def write_snapshot(snapshot: Dict[str, Any]) -> None:
    day_key = safe_key(snapshot["asOf"])
    ref = db.reference(f"{FIREBASE_ROOT}/{HISTORY_NODE}/daily/{day_key}")
    ref.set(snapshot)
    db.reference(f"{FIREBASE_ROOT}/{HISTORY_NODE}/latestSyncAt").set(snapshot["generatedAt"])
    db.reference(f"{FIREBASE_ROOT}/{HISTORY_NODE}/latestDay").set(snapshot["asOf"])
    logger.info("Wrote delivery-flow snapshot for %s", snapshot["asOf"])


def build_nishi_detail_items(nishi_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    items: Dict[str, Any] = {}
    ordered_rows = sorted(
        nishi_rows,
        key=lambda row: (
            clean(row.get("salesOrder")),
            clean(row.get("ticketKey")),
            clean(row.get("ticketId")),
        ),
    )
    for row in ordered_rows:
        ticket_key = clean(row.get("ticketKey")) or clean(row.get("ticketId"))
        if not ticket_key:
            continue
        items[ticket_key] = {
            "roles": {},
                "ticket": {
                    "TicketID": clean(row.get("ticketId")),
                    "Sales Order": clean(row.get("salesOrder")),
                    "SO Created Date": clean(row.get("soCreatedDate")),
                    "AmountIncludingTax": row.get("amountIncludingTax", 0.0),
                    "ERPPurchaseOrder": clean(row.get("erppurchaseOrder")),
                    "Purchaser": clean(row.get("purchaser")),
                    "Reservation_Created_By": clean(row.get("reservationCreatedBy")),
                    "Reservation_Cost_Center": clean(row.get("reservationCostCenter")),
                    "Net Value": row.get("netValue", 0.0),
                    "Currency": clean(row.get("currency")),
                },
            }
    return items


def write_nishi_detail(snapshot: Dict[str, Any], nishi_rows: List[Dict[str, Any]]) -> None:
    day_key = safe_key(snapshot["asOf"])
    payload = {
        "asOf": snapshot["asOf"],
        "generatedAt": snapshot["generatedAt"],
        "ytdYear": snapshot["nishiE03"]["ytdYear"],
        "ytdCost": snapshot["nishiE03"]["ytdCost"],
        "count": snapshot["nishiE03"].get("count", len(nishi_rows)),
        "items": build_nishi_detail_items(nishi_rows),
    }
    ref = db.reference(f"{MONITOR_ROOT}/analytics/deliveryFlow/nishiE03CostYtd/daily/{day_key}")
    ref.set(payload)
    db.reference(f"{MONITOR_ROOT}/analytics/deliveryFlow/nishiE03CostYtd/latest").set(payload)
    logger.info("Wrote Nishi E03 detail payload for %s with %s rows", snapshot["asOf"], len(nishi_rows))


def load_hana_cost_sidecar(as_of: date) -> Optional[Dict[str, Any]]:
    day_key = safe_key(as_of.isoformat())
    base = f"{MONITOR_ROOT}/analytics/deliveryFlow/hanaCostYtd"
    candidates = [
        f"{base}/daily/{day_key}",
        f"{base}/latest",
    ]
    for path in candidates:
        node = db.reference(path).get()
        node = node_to_dict(node)
        if node and (node.get("costReport") or node.get("partsCost") or node.get("nishiE03")):
            logger.info("Loaded HANA cost sidecar from %s", path)
            return node
    return None


# =================== Main ===================
def run_once(as_of: Optional[str]) -> None:
    firebase_init()
    as_of_date = parse_date(as_of) or datetime.now(timezone.utc).date()
    logger.info("Aggregating delivery flow as of %s", as_of_date.isoformat())

    tickets_node = db.reference(f"{FIREBASE_ROOT}/tickets").get() or {}
    snapshot = aggregate(tickets_node, as_of_date)
    nishi_rows = snapshot.pop("_nishiRows", [])
    hana_cost = load_hana_cost_sidecar(as_of_date)
    if hana_cost:
        if isinstance(hana_cost.get("partsCost"), dict):
            snapshot["partsCost"] = hana_cost["partsCost"]
        if isinstance(hana_cost.get("nishiE03"), dict):
            snapshot["nishiE03"] = hana_cost["nishiE03"]
        if isinstance(hana_cost.get("costReport"), dict):
            snapshot["costReport"] = hana_cost["costReport"]
        nishi_rows = hana_cost.get("nishiRows") if isinstance(hana_cost.get("nishiRows"), list) else nishi_rows
    else:
        logger.warning("No HANA cost sidecar found; leaving ticket-derived cost values in place.")

    logger.info(
        "KPI: openSoItems=%s partially=%s fully=%s avgDaysFirstIssue=%s oldestNotIssued=%s",
        snapshot["kpi"]["openSoItems"],
        snapshot["kpi"]["partiallyIssuedTickets"],
        snapshot["kpi"]["fullyIssuedTickets"],
        snapshot["kpi"]["avgDaysToFirstIssue"],
        snapshot["kpi"]["oldestNotIssuedDays"],
    )
    if snapshot.get("costReport"):
        cost_report = snapshot["costReport"]
        logger.info(
            "Cost report: year=%s totalCost=%s purchaserTotalCost=%s totalSo=%s",
            cost_report["ytdYear"],
            cost_report["totalCost"],
            cost_report.get("purchaserTotalCost", cost_report.get("poTotalCost", 0)),
            cost_report["totalSoCount"],
        )
    write_snapshot(snapshot)
    write_nishi_detail(snapshot, nishi_rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Delivery Flow daily aggregator (append-only).")
    p.add_argument("--once", action="store_true", help="Run a single aggregation and exit.")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD; default = today (UTC).")
    p.add_argument("--firebase-db-url", default=None)
    p.add_argument("--firebase-sa-path", default=None)
    p.add_argument("--firebase-root", default=None)
    args = p.parse_args()

    global FIREBASE_DB_URL, FIREBASE_SA_PATH, FIREBASE_ROOT
    if args.firebase_db_url:
        FIREBASE_DB_URL = args.firebase_db_url
    if args.firebase_sa_path:
        FIREBASE_SA_PATH = args.firebase_sa_path
    if args.firebase_root:
        FIREBASE_ROOT = args.firebase_root

    if not args.once:
        logger.info("Nothing to do. Pass --once to run an aggregation.")
        return
    run_once(args.as_of)


if __name__ == "__main__":
    main()
