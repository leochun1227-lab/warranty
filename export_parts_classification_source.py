from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import firebase_admin
    from firebase_admin import credentials, db
except ImportError:  # pragma: no cover
    firebase_admin = None
    credentials = None
    db = None


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"
DEFAULT_OUTPUT_CSV = OUTPUT_DIR / "parts_classification_source.csv"

DEFAULT_FIREBASE_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app",
)
DEFAULT_FIREBASE_SA_PATH = os.getenv(
    "FIREBASE_SA_PATH",
    str(ROOT / "firebase-service-account.json"),
)
DEFAULT_FIREBASE_ROOT = os.getenv("FIREBASE_ROOT", "c4cTickets_test")

CNY_TO_AUD_RATE = 5.0
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"]
MATERIAL_PRICE_HEADERS = [
    "3090 SO Net Price (AUD)",
    "3090 SO Original Currency",
    "3090 SO Date",
    "3090 SO Doc Number",
    "3110 PO Net Price (AUD)",
    "3110 PO Original Currency",
    "3110 PO Date",
    "3110 PO Doc Number",
    "3091 PO Net Price (AUD)",
    "3091 PO Original Currency",
    "3091 PO Date",
    "3091 PO Doc Number",
]

CSV_HEADERS = [
    "Month",
    "Ticket Key",
    "Ticket ID",
    "Sales Order",
    "SO Created Date",
    "Dealer ID",
    "Dealer Name",
    "Ticket Status",
    "Ticket Status Text",
    "Sales Order Item",
    "Material",
    "Description",
    "Order Qty",
    "Sales Unit",
    "Purchaser",
    "Currency",
    "Net Value",
    "ERP Purchase Order",
    "Amount Including Tax",
    "Preferred Line Cost (AUD)",
    *MATERIAL_PRICE_HEADERS,
    "Delivery Count",
    "First Issue Date",
    "Rejection Reason",
    "Item Rejection Status",
]


logger = logging.getLogger("export_parts_classification_source")


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_number(value: Any) -> float:
    text = clean(value).replace(",", "")
    if not text or text == "#":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_any_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = clean(value)
    if not text or text == "#":
        return None

    for candidate in (text, text[:10]):
        for fmt in (
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%d.%m.%Y",
        ):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def date_key(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def month_key_from_ticket(ticket: Dict[str, Any]) -> str:
    for candidate in (ticket.get("soCreatedDate"), ticket.get("createdOn"), ticket.get("firstIssueDate")):
        parsed = parse_any_date(candidate)
        if parsed:
            return f"{parsed.year:04d}-{parsed.month:02d}-01"
    return ""


def month_label_from_key(key: str) -> str:
    parsed = parse_any_date(key)
    if not parsed:
        return clean(key)
    return f"{MONTH_LABELS[parsed.month - 1]} {parsed.year}"


def firebase_init(firebase_db_url: str, firebase_sa_path: str) -> None:
    if firebase_admin is None or credentials is None or db is None:
        raise RuntimeError("firebase_admin is not installed.")
    if getattr(firebase_admin, "_apps", None):
        return
    if not Path(firebase_sa_path).exists():
        raise FileNotFoundError(f"Firebase service account file not found: {firebase_sa_path}")
    cred = credentials.Certificate(firebase_sa_path)
    firebase_admin.initialize_app(cred, {"databaseURL": firebase_db_url})


def firebase_node_to_dict(node: Any) -> Dict[str, Any]:
    if isinstance(node, dict):
        return node
    if isinstance(node, list):
        return {str(i): value for i, value in enumerate(node) if value is not None}
    return {}


def price_to_aud(raw_price: Any, raw_currency: Any) -> Any:
    text = clean(raw_price)
    amount = parse_number(raw_price)
    if not text and amount == 0:
        return ""
    currency = clean(raw_currency).upper()
    if currency == "CNY":
        return round(amount / CNY_TO_AUD_RATE, 4)
    return round(amount, 4)


def preferred_parts_unit_price(item: Dict[str, Any]) -> Any:
    au_po = price_to_aud(item.get("auPoPrice"), item.get("auPoCurrency"))
    if au_po != "":
        return au_po
    cn_so = price_to_aud(item.get("cnSoPrice"), item.get("cnSoCurrency"))
    if cn_so != "":
        return cn_so
    return ""


def preferred_parts_line_cost(item: Dict[str, Any]) -> Any:
    unit = preferred_parts_unit_price(item)
    if unit == "":
        return ""
    qty = parse_number(item.get("orderQty"))
    if qty <= 0:
        return unit
    return round(unit * qty, 4)


def material_price_cols(item: Dict[str, Any]) -> List[Any]:
    return [
        price_to_aud(item.get("cnSoPrice"), item.get("cnSoCurrency")),
        clean(item.get("cnSoCurrency")),
        clean(item.get("cnSoDate")),
        clean(item.get("cnSoDoc")),
        price_to_aud(item.get("auPoPrice"), item.get("auPoCurrency")),
        clean(item.get("auPoCurrency")),
        clean(item.get("auPoDate")),
        clean(item.get("auPoDoc")),
        price_to_aud(item.get("cnPoPrice"), item.get("cnPoCurrency")),
        clean(item.get("cnPoCurrency")),
        clean(item.get("cnPoDate")),
        clean(item.get("cnPoDoc")),
    ]


def ticket_values(node: Any) -> Iterable[tuple[str, Any]]:
    data = firebase_node_to_dict(node)
    return data.items()


def parse_sales_order_details(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if isinstance(value, list):
        rows: List[Dict[str, Any]] = []
        for item in value:
            rows.extend(parse_sales_order_details(item))
        return rows
    if not isinstance(value, dict):
        return []

    item_keys = {"Delivery Count", "Description", "Material", "Order Qty", "Sales Order Item"}
    if any(key in value for key in item_keys):
        return [{
            "salesOrderItem": clean(value.get("Sales Order Item")),
            "material": clean(value.get("Material")),
            "description": clean(value.get("Description")),
            "orderQty": clean(value.get("Order Qty")),
            "salesUnit": clean(value.get("Sales Unit")),
            "purchaser": clean(value.get("Purchaser")),
            "currency": clean(value.get("Currency")),
            "netValue": clean(value.get("Net Value")),
            "erpPurchaseOrder": clean(value.get("ERPPurchaseOrder")),
            "amountIncludingTax": clean(value.get("AmountIncludingTax")),
            "deliveryCount": int(parse_number(value.get("Delivery Count")) or 0),
            "firstIssueDate": clean(value.get("First Issue Date")),
            "rejectionReason": clean(value.get("Rejection Reason")),
            "itemRejectionStatus": clean(value.get("Item Rejection Status")),
            "cnSoPrice": clean(value.get("3090 SO Net Price")),
            "cnSoCurrency": clean(value.get("3090 SO Currency")),
            "cnSoDate": clean(value.get("3090 SO Date")),
            "cnSoDoc": clean(value.get("3090 SO Doc")),
            "auPoPrice": clean(value.get("3110 PO Net Price")),
            "auPoCurrency": clean(value.get("3110 PO Currency")),
            "auPoDate": clean(value.get("3110 PO Date")),
            "auPoDoc": clean(value.get("3110 PO Doc")),
            "cnPoPrice": clean(value.get("3091 PO Net Price")),
            "cnPoCurrency": clean(value.get("3091 PO Currency")),
            "cnPoDate": clean(value.get("3091 PO Date")),
            "cnPoDoc": clean(value.get("3091 PO Doc")),
        }]

    rows = []
    for item in value.values():
        rows.extend(parse_sales_order_details(item))
    return rows


def sort_created_on_value(value: Any) -> int:
    parsed = parse_any_date(value)
    if parsed:
        return int(datetime(parsed.year, parsed.month, parsed.day).timestamp())
    return 0


def normalize_ticket_export_row(ticket_key: str, raw_ticket: Any) -> Dict[str, Any]:
    if isinstance(raw_ticket, dict) and isinstance(raw_ticket.get("ticket"), dict):
        ticket = raw_ticket["ticket"]
    elif isinstance(raw_ticket, dict):
        ticket = raw_ticket
    else:
        ticket = {}

    details = parse_sales_order_details(ticket.get("Sales Order Details"))
    issue_dates = sorted(
        (clean(item.get("firstIssueDate")) for item in details if clean(item.get("firstIssueDate"))),
        key=lambda value: (parse_any_date(value) or date.min, value),
    )
    complete_issue_date = (
        clean(ticket.get("Complete Issue Date"))
        or (issue_dates[-1] if issue_dates else "")
        or clean(ticket.get("First Issue Date"))
    )

    return {
        "ticketKey": clean(ticket_key),
        "ticketId": clean(ticket.get("TicketID") or ticket.get("TicketId") or ticket.get("id") or ticket_key),
        "salesOrder": clean(ticket.get("Sales Order") or ticket.get("SalesOrder") or ticket.get("salesOrder") or ticket.get("LookupSalesOrder") or ticket.get("lookupSalesOrder")),
        "soCreatedDate": clean(ticket.get("SO Created Date") or ticket.get("soCreatedDate") or ticket.get("CreatedOn") or ticket.get("createdOn") or ticket.get("created")),
        "dealerId": clean(ticket.get("DealerID")),
        "dealerName": clean(ticket.get("DealerName")),
        "createdOn": clean(ticket.get("CreatedOn") or ticket.get("createdOn") or ticket.get("createdAt") or ticket.get("CreatedAt")),
        "ticketStatus": clean(ticket.get("TicketStatus")),
        "ticketStatusText": clean(ticket.get("TicketStatusText")),
        "erpPurchaseOrder": clean(ticket.get("ERPPurchaseOrder")),
        "amountIncludingTax": parse_number(ticket.get("AmountIncludingTax")),
        "firstIssueDate": complete_issue_date,
        "purchaser": clean(ticket.get("Purchaser")),
        "currency": clean(ticket.get("Currency")),
        "details": details,
    }


def build_csv_rows(tickets_node: Any) -> List[List[Any]]:
    tickets = [
        normalize_ticket_export_row(ticket_key, raw)
        for ticket_key, raw in ticket_values(tickets_node)
    ]
    tickets = [
        ticket for ticket in tickets
        if ticket.get("ticketId") or ticket.get("salesOrder") or ticket.get("details")
    ]
    tickets.sort(
        key=lambda ticket: (
            month_key_from_ticket(ticket),
            clean(ticket.get("ticketId")),
            clean(ticket.get("ticketKey")),
        )
    )

    rows: List[List[Any]] = [CSV_HEADERS]
    for ticket in tickets:
        month_key = month_key_from_ticket(ticket)
        for item in ticket.get("details", []):
            po = clean(item.get("erpPurchaseOrder")) or clean(ticket.get("erpPurchaseOrder"))
            amount_including_tax = item.get("amountIncludingTax")
            if clean(amount_including_tax) == "":
                amount_including_tax = ticket.get("amountIncludingTax")
            rows.append([
                month_label_from_key(month_key),
                ticket.get("ticketKey", ""),
                ticket.get("ticketId", ""),
                ticket.get("salesOrder", ""),
                ticket.get("soCreatedDate", ""),
                ticket.get("dealerId", ""),
                ticket.get("dealerName", ""),
                ticket.get("ticketStatus", ""),
                ticket.get("ticketStatusText", ""),
                item.get("salesOrderItem", ""),
                item.get("material", ""),
                item.get("description", ""),
                item.get("orderQty", ""),
                item.get("salesUnit", ""),
                item.get("purchaser", "") or ticket.get("purchaser", ""),
                item.get("currency", "") or ticket.get("currency", ""),
                item.get("netValue", ""),
                po,
                amount_including_tax,
                preferred_parts_line_cost(item),
                *material_price_cols(item),
                item.get("deliveryCount", 0),
                item.get("firstIssueDate", "") or ticket.get("firstIssueDate", ""),
                item.get("rejectionReason", ""),
                item.get("itemRejectionStatus", ""),
            ])
    return rows


def write_csv(path: Path, rows: List[List[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--firebase-db-url", default=DEFAULT_FIREBASE_DB_URL)
    parser.add_argument("--firebase-sa-path", default=DEFAULT_FIREBASE_SA_PATH)
    parser.add_argument("--firebase-root", default=DEFAULT_FIREBASE_ROOT)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(asctime)s - %(levelname)s - %(message)s")

    firebase_init(args.firebase_db_url, args.firebase_sa_path)
    tickets_node = db.reference(f"{args.firebase_root}/tickets").get()
    rows = build_csv_rows(tickets_node)
    output_path = Path(args.output)
    write_csv(output_path, rows)
    logger.info("Parts classification source rows written: %s", max(0, len(rows) - 1))
    logger.info("Output CSV: %s", output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
