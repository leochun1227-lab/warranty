from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from firebase_admin import db

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ctm_v44_history_safe_mandt800_rejection_filter import (
    DEFAULT_DB_URL,
    DEFAULT_SOURCE_ROOT,
    clean,
    get_field,
    init_firebase,
    normalize_po_number,
    normalize_row,
    normalize_ticket_id,
)


PO_FIELDS = [
    "ERPPurchaseOrder",
    "ERP Purchase Order",
    "ERP Purchase Order ID",
    "Purchasing Document",
    "PurchaseOrder",
    "Purchase Order",
    "PurchaseOrderID",
    "Purchase Order ID",
    "PO",
    "PONumber",
    "PO Number",
]

VEHICLE_FIELDS = [
    "SerialID",
    "Serial ID",
    "ChassisNumber",
    "Chassis Number",
    "RegisteredProduct",
    "Registered Product",
    "Product",
    "TicketSubject",
    "Subject",
]


def ticket_entries(source_root: str) -> list[tuple[str, Any]]:
    node = db.reference(f"{source_root}/tickets").get() or {}
    if isinstance(node, list):
        return [(str(i), row) for i, row in enumerate(node) if row]
    if isinstance(node, dict):
        return list(node.items())
    return []


def flatten(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        out = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            out.extend(flatten(child, child_prefix))
        return out
    if isinstance(value, list):
        out = []
        for idx, child in enumerate(value):
            out.extend(flatten(child, f"{prefix}[{idx}]"))
        return out
    text = clean(value)
    return [(prefix, text)] if text else []


def main() -> None:
    init_firebase(
        os.getenv("FIREBASE_DB_URL", DEFAULT_DB_URL),
        os.getenv("FIREBASE_SA_PATH", str(Path.cwd() / "firebase-service-account.json")),
    )
    source_root = os.getenv("SOURCE_ROOT", DEFAULT_SOURCE_ROOT)
    target_po = normalize_po_number(os.getenv("TARGET_PO", "7000013310"))
    target_vehicle = clean(os.getenv("TARGET_VEHICLE", "SRC243285")).upper()

    po_matches = []
    vehicle_matches = []
    raw_contains_matches = []
    field_hits: dict[str, int] = {}
    for fallback_key, raw in ticket_entries(source_root):
        ticket, _roles, tid_raw = normalize_row(raw, fallback_key)
        ticket_id = normalize_ticket_id(tid_raw)
        if not ticket_id:
            continue
        for field in PO_FIELDS:
            value = get_field(ticket, [field])
            if normalize_po_number(value) == target_po:
                field_hits[field] = field_hits.get(field, 0) + 1
                po_matches.append(
                    {
                        "Ticket ID": ticket_id,
                        "Field": field,
                        "Value": clean(value),
                        "Subject": get_field(ticket, ["Subject", "TicketSubject", "Ticket"]),
                        "Status": get_field(ticket, ["Status", "TicketStatusText", "StatusText"]),
                        "Created On": get_field(ticket, ["CreatedOn", "Created On", "createdOn"]),
                        "Dealer": get_field(ticket, ["DealerName", "Dealer Name"]),
                    }
                )
        for path, text in flatten(raw):
            if target_po and target_po in normalize_po_number(text):
                raw_contains_matches.append(
                    {
                        "Ticket ID": ticket_id,
                        "Path": path,
                        "Value": text[:300],
                    }
                )
        for field in VEHICLE_FIELDS:
            value = clean(get_field(ticket, [field])).upper()
            if value == target_vehicle:
                vehicle_matches.append(
                    {
                        "Ticket ID": ticket_id,
                        "Field": field,
                        "Value": clean(get_field(ticket, [field])),
                        "PO": get_field(ticket, PO_FIELDS),
                        "Subject": get_field(ticket, ["Subject", "TicketSubject", "Ticket"]),
                        "Status": get_field(ticket, ["Status", "TicketStatusText", "StatusText"]),
                        "Created On": get_field(ticket, ["CreatedOn", "Created On", "createdOn"]),
                        "Dealer": get_field(ticket, ["DealerName", "Dealer Name"]),
                    }
                )

    out = {
        "targetPO": target_po,
        "targetVehicle": target_vehicle,
        "poMatches": po_matches,
        "rawContainsMatches": raw_contains_matches,
        "vehicleMatches": vehicle_matches,
        "poFieldHitCounts": field_hits,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
