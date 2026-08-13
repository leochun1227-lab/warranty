from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_REPAIR_DETAILS = ROOT / "outputs" / "repairers_2026" / "repairers_2026_light.json"
DEFAULT_C4C_SOURCE = ROOT / "outputs" / "analysis_ticket_base.csv"
DEFAULT_OUT_JSON = ROOT / "outputs" / "repairers_2026" / "sap_authoritative_repair_payments.json"
DEFAULT_DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)
DEFAULT_MANDT = os.getenv("SAP_CLIENT", "800")
DEFAULT_SCHEMA = os.getenv("SAP_SCHEMA", "SAPHANADB")
DEFAULT_PURCHASING_ORG = os.getenv("PURCHASING_ORG", "3111")
DEFAULT_PURCHASING_GROUP = os.getenv("PURCHASING_GROUP", "E06")
DEFAULT_DATE_FROM = os.getenv("SAP_REPAIR_PO_DATE_FROM", "20260101")
DEFAULT_DATE_TO = os.getenv("SAP_REPAIR_PO_DATE_TO", "")

TICKET_NO_PATTERN = re.compile(
    r"\btickets?\s*no\.?\s*[:#\-]?\s*\[?\s*(\d+)\s*\]?\b",
    flags=re.IGNORECASE,
)
TICKET_BRACKET_PATTERN = re.compile(
    r"\btickets?\s*\[\s*(\d+)\s*\]",
    flags=re.IGNORECASE,
)
TICKET_OUTER_BRACKET_PATTERN = re.compile(
    r"\[\s*tickets?\s+(\d+)\s*(?:[\]\}]|$|[,，;；]|\s)?",
    flags=re.IGNORECASE,
)


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"#", "None", "nan", "NaN"} else text


def sql_quote(value: Any) -> str:
    return str(value).replace("'", "''")


def quote_ident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def full_table(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def normalize_ticket_id(value: Any) -> str:
    text = clean(value)
    if not text:
        return ""
    digits = re.sub(r"\D+", "", text)
    if not digits:
        return ""
    return str(int(digits)) if digits.isdigit() else digits


def normalize_date(value: Any) -> str:
    text = clean(value)
    digits = re.sub(r"\D+", "", text)
    if len(digits) == 8:
        return digits
    return ""


def iso_date(value: Any) -> str:
    digits = normalize_date(value)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return clean(value)


def amount(value: Any) -> float:
    try:
        return round(float(clean(value).replace(",", "")), 2)
    except Exception:
        return 0.0


def extract_ticket_number(short_text: Any) -> tuple[str, str]:
    text = clean(short_text)
    if not text:
        return "", "Short Text is blank"

    matches: list[str] = []
    matches.extend(TICKET_NO_PATTERN.findall(text))
    matches.extend(TICKET_BRACKET_PATTERN.findall(text))
    matches.extend(TICKET_OUTER_BRACKET_PATTERN.findall(text))

    unique: list[str] = []
    seen: set[str] = set()
    for match in matches:
        ticket_id = normalize_ticket_id(match)
        if ticket_id and ticket_id not in seen:
            unique.append(ticket_id)
            seen.add(ticket_id)

    if len(unique) == 1:
        return unique[0], ""
    if len(unique) > 1:
        return "", "Multiple ticket numbers found: " + ", ".join(unique)
    if re.search(r"\btickets?\b", text, flags=re.IGNORECASE):
        return "", "Contains Ticket word but not standard Ticket No. number or Ticket [number] pattern"
    return "", "No standard Ticket No. number or Ticket [number] pattern"


def load_repair_detail_lookup(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    details = payload.get("details") if isinstance(payload.get("details"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for row in details:
        ticket_id = normalize_ticket_id(row.get("C4C Ticket ID") or row.get("C4CTicketID") or row.get("c4c_ticket_id"))
        if ticket_id:
            out[ticket_id] = row
    return out


def normalize_po(value: Any) -> str:
    text = clean(value).upper()
    return re.sub(r"\s+", "", text)


def is_customer_like_repairer(value: Any) -> bool:
    text = clean(value).lower()
    if not text:
        return False
    return "customer" in text and ("repair" in text or "repairer" in text)


def is_unassigned_repairer(value: Any) -> bool:
    text = clean(value).lower()
    if not text:
        return True
    compact = re.sub(r"[\s_\-]+", "", text)
    return compact in {"#", "na", "n/a", "none", "nan", "unknown", "unassigned", "notassigned"} or text in {
        "#",
        "-",
        "not assigned",
        "unassigned",
        "unknown",
    }


def is_c4c_status_excluded(value: Any) -> bool:
    text = clean(value).lower()
    if not text:
        return False
    excluded_tokens = (
        "unapproved",
        "rejected",
        "reject",
        "declined",
        "cancelled",
        "canceled",
        "void",
    )
    return any(token in text for token in excluded_tokens)


def c4c_row_decision(row: dict[str, Any]) -> tuple[bool, str]:
    repairer = clean(row.get("Service Technician"))
    status = clean(row.get("Status"))
    if is_unassigned_repairer(repairer):
        return False, "C4C Service Technician blank/unassigned"
    if is_customer_like_repairer(repairer):
        return False, "C4C customer/self repairer"
    if is_c4c_status_excluded(status):
        return False, f"C4C excluded status: {status}"
    return True, "C4C eligible approved repair scope"


def first_present(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = clean(row.get(name))
        if value:
            return value
    return ""


def summarize_c4c_rows(rows: list[dict[str, Any]], match_source: str) -> dict[str, Any]:
    eligible_rows: list[dict[str, Any]] = []
    reasons: list[str] = []
    for row in rows:
        ok, reason = c4c_row_decision(row)
        if ok:
            eligible_rows.append(row)
        else:
            reasons.append(reason)
    chosen = eligible_rows[0] if eligible_rows else (rows[0] if rows else {})
    return {
        "eligible": bool(eligible_rows),
        "match_source": match_source,
        "reason": "C4C eligible approved repair scope" if eligible_rows else unique_join(reasons) or "C4C row did not meet repairer eligibility",
        "service_technician": first_present(chosen, "Service Technician"),
        "status": first_present(chosen, "Status"),
        "po": first_present(chosen, "ERP Purchase Order ID"),
        "ticket_id": first_present(chosen, "C4C Ticket ID", "C4CTicketID", "C4C_Ticket_ID", "c4c_ticket_id", "Ticket ID"),
        "ticket_title": first_present(chosen, "Ticket"),
        "created_on": first_present(chosen, "Created On"),
        "posting_date": first_present(chosen, "Posting Date"),
        "approved_on": first_present(chosen, "Claim Approved On", "Approved Date"),
        "chassis": first_present(chosen, "Chassis Number", "Serial ID", "Registered Product", "Product"),
        "dealer": first_present(chosen, "Dealer"),
        "dealer_name": first_present(chosen, "Dealer Name"),
        "country_region": first_present(chosen, "Country/Region"),
        "claim_total": amount(first_present(chosen, "ClaimTotalAmount")),
        "repairer_parts_total": amount(first_present(chosen, "Repairer Parts Claim Total Amount")),
    }


def load_c4c_eligibility(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "by_ticket": {},
            "meta": {"path": str(path), "rows_read": 0, "eligible_rows": 0, "missing": True},
        }
    by_ticket_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows_read = 0
    eligible_rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows_read += 1
            ticket_id = normalize_ticket_id(first_present(row, "C4C Ticket ID", "C4CTicketID", "C4C_Ticket_ID", "c4c_ticket_id"))
            ok, _ = c4c_row_decision(row)
            if ok:
                eligible_rows += 1
            if ticket_id:
                by_ticket_rows[ticket_id].append(row)

    return {
        "by_ticket": {key: summarize_c4c_rows(value, "C4C Ticket ID") for key, value in by_ticket_rows.items()},
        "meta": {
            "path": str(path),
            "rows_read": rows_read,
            "eligible_rows": eligible_rows,
            "unique_ticket_ids": len(by_ticket_rows),
            "missing": False,
        },
    }


def lookup_c4c_eligibility(parsed_ticket: str, c4c_lookup: dict[str, Any]) -> dict[str, Any]:
    if parsed_ticket:
        item = c4c_lookup.get("by_ticket", {}).get(parsed_ticket)
        if item:
            return item
    return {
        "eligible": False,
        "match_source": "Not Found",
        "reason": "No matching C4C approved repair row by SAP short-text ticket ID",
        "service_technician": "",
        "status": "",
        "po": "",
        "ticket_id": parsed_ticket,
        "ticket_title": "",
        "created_on": "",
        "posting_date": "",
        "approved_on": "",
        "chassis": "",
        "dealer": "",
        "dealer_name": "",
        "country_region": "",
        "claim_total": 0.0,
        "repairer_parts_total": 0.0,
    }


def fetch_sap_po_lines(
    dsn: str,
    mandt: str,
    schema: str,
    purchasing_org: str,
    purchasing_group: str,
    date_from: str,
    date_to: str,
    exclude_cancelled: bool,
) -> list[dict[str, Any]]:
    import pyodbc

    where_parts = [
        f'p."MANDT" = \'{sql_quote(mandt)}\'',
        f'h."EKORG" = \'{sql_quote(purchasing_org)}\'',
        f'h."EKGRP" = \'{sql_quote(purchasing_group)}\'',
    ]
    if date_from:
        where_parts.append(f'h."BEDAT" >= \'{sql_quote(date_from)}\'')
    if date_to:
        where_parts.append(f'h."BEDAT" <= \'{sql_quote(date_to)}\'')
    if exclude_cancelled:
        where_parts.append("COALESCE(h.\"LOEKZ\", '') = ''")
        where_parts.append("COALESCE(p.\"LOEKZ\", '') = ''")

    sql = f"""
        SELECT
            p."EBELN" AS "PO",
            p."EBELP" AS "PO Item",
            p."TXZ01" AS "Short Text",
            p."NETWR" AS "PO Item Net Value",
            p."BRTWR" AS "PO Item Gross Value",
            p."MENGE" AS "PO Item Qty",
            p."MEINS" AS "PO Item Unit",
            p."MATNR" AS "Material",
            p."MATKL" AS "Material Group",
            p."WERKS" AS "Plant",
            h."LIFNR" AS "SAP Repairer Vendor ID",
            vendor."NAME1" AS "SAP Repairer Name",
            vendor."ORT01" AS "SAP Repairer City",
            vendor."REGIO" AS "SAP Repairer Region",
            vendor."LAND1" AS "SAP Repairer Country",
            h."EKORG" AS "Purchasing Org",
            h."EKGRP" AS "Purchasing Group",
            h."BSART" AS "PO Doc Type",
            h."BEDAT" AS "PO Document Date",
            h."AEDAT" AS "PO Changed Date",
            h."ERNAM" AS "PO Created By",
            h."WAERS" AS "PO Currency",
            h."LOEKZ" AS "PO Header Deletion Indicator",
            p."LOEKZ" AS "PO Item Deletion Indicator",
            p."ELIKZ" AS "Delivery Completed",
            p."EREKZ" AS "Final Invoice",
            inv."Invoice Rows" AS "Invoice Rows",
            inv."First Invoice Doc" AS "First Invoice Doc",
            inv."Last Invoice Doc" AS "Last Invoice Doc",
            inv."Last Invoice Date" AS "Last Invoice Date",
            inv."Invoice Amount Doc" AS "Invoice Amount Doc",
            inv."Signed Invoice Amount Doc" AS "Signed Invoice Amount Doc"
        FROM {full_table(schema, "EKPO")} p
        INNER JOIN {full_table(schema, "EKKO")} h
            ON h."MANDT" = p."MANDT"
           AND h."EBELN" = p."EBELN"
        LEFT JOIN {full_table(schema, "LFA1")} vendor
            ON vendor."MANDT" = h."MANDT"
           AND vendor."LIFNR" = h."LIFNR"
        LEFT JOIN (
            SELECT
                "MANDT",
                "EBELN",
                "EBELP",
                COUNT(*) AS "Invoice Rows",
                MIN("BELNR") AS "First Invoice Doc",
                MAX("BELNR") AS "Last Invoice Doc",
                MAX("BUDAT") AS "Last Invoice Date",
                SUM(COALESCE("WRBTR", 0)) AS "Invoice Amount Doc",
                SUM(CASE WHEN COALESCE("SHKZG", '') = 'H' THEN -COALESCE("WRBTR", 0) ELSE COALESCE("WRBTR", 0) END) AS "Signed Invoice Amount Doc"
            FROM {full_table(schema, "EKBE")}
            WHERE "MANDT" = '{sql_quote(mandt)}'
              AND "VGABE" = '2'
            GROUP BY "MANDT", "EBELN", "EBELP"
        ) inv
            ON inv."MANDT" = p."MANDT"
           AND inv."EBELN" = p."EBELN"
           AND inv."EBELP" = p."EBELP"
        WHERE {" AND ".join(where_parts)}
        ORDER BY h."BEDAT", p."EBELN", p."EBELP"
    """

    with pyodbc.connect(dsn, autocommit=True, timeout=60) as conn:
        cur = conn.execute(sql)
        columns = [column[0] for column in cur.description]
        return [{columns[index]: row[index] for index in range(len(columns))} for row in cur.fetchall()]


def unique_join(values: Iterable[Any], limit: int = 30) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean(value)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
        if len(out) >= limit:
            break
    return ", ".join(out)


def build_outputs(
    lines: list[dict[str, Any]],
    detail_lookup: dict[str, dict[str, Any]],
    c4c_lookup: dict[str, Any],
) -> dict[str, Any]:
    po_lines: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    c4c_ineligible: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for raw in lines:
        parsed_ticket, parse_note = extract_ticket_number(raw.get("Short Text"))
        detail = detail_lookup.get(parsed_ticket) if parsed_ticket else None
        c4c = lookup_c4c_eligibility(parsed_ticket, c4c_lookup) if parsed_ticket else {
            "eligible": False,
            "match_source": "No SAP Ticket ID",
            "reason": "SAP short text ticket ID is unreadable",
            "service_technician": "",
            "status": "",
            "po": "",
            "ticket_id": "",
            "ticket_title": "",
            "created_on": "",
            "posting_date": "",
            "approved_on": "",
            "chassis": "",
            "dealer": "",
            "dealer_name": "",
            "country_region": "",
            "claim_total": 0.0,
            "repairer_parts_total": 0.0,
        }
        po_net_value = amount(raw.get("PO Item Net Value"))
        invoice_rows = int(amount(raw.get("Invoice Rows")))
        header_deleted = clean(raw.get("PO Header Deletion Indicator"))
        item_deleted = clean(raw.get("PO Item Deletion Indicator"))
        is_cancelled = bool(header_deleted or item_deleted)
        line = {
            "SAP Ticket ID": parsed_ticket,
            "Short Text Parse Note": parse_note,
            "SAP PO Date": iso_date(raw.get("PO Document Date")),
            "SAP PO Date Raw": clean(raw.get("PO Document Date")),
            "SAP Last Invoice Date": iso_date(raw.get("Last Invoice Date")),
            "SAP First Invoice Doc": clean(raw.get("First Invoice Doc")),
            "SAP Last Invoice Doc": clean(raw.get("Last Invoice Doc")),
            "SAP Invoice Rows": invoice_rows,
            "SAP Invoice Status": "Invoiced" if invoice_rows > 0 else "PO Only",
            "SAP PO": clean(raw.get("PO")),
            "SAP PO Item": clean(raw.get("PO Item")),
            "SAP Repairer Vendor ID": clean(raw.get("SAP Repairer Vendor ID")),
            "SAP Repairer Name": clean(raw.get("SAP Repairer Name")),
            "SAP Repairer City": clean(raw.get("SAP Repairer City")),
            "SAP Repairer Region": clean(raw.get("SAP Repairer Region")),
            "SAP Repairer Country": clean(raw.get("SAP Repairer Country")),
            "SAP PO Net Value": po_net_value,
            "SAP Active PO Net Value": 0.0 if is_cancelled else po_net_value,
            "SAP Cancelled PO Net Value": po_net_value if is_cancelled else 0.0,
            "SAP PO Cancelled": "Yes" if is_cancelled else "No",
            "SAP PO Header Deletion Indicator": header_deleted,
            "SAP PO Item Deletion Indicator": item_deleted,
            "SAP PO Gross Value": amount(raw.get("PO Item Gross Value")),
            "SAP PO Currency": clean(raw.get("PO Currency")),
            "SAP Invoice Amount Doc": amount(raw.get("Invoice Amount Doc")),
            "SAP Signed Invoice Amount Doc": amount(raw.get("Signed Invoice Amount Doc")),
            "SAP Short Text": clean(raw.get("Short Text")),
            "SAP Material": clean(raw.get("Material")),
            "SAP Material Group": clean(raw.get("Material Group")),
            "SAP Plant": clean(raw.get("Plant")),
            "SAP Purchasing Org": clean(raw.get("Purchasing Org")),
            "SAP Purchasing Group": clean(raw.get("Purchasing Group")),
            "SAP PO Doc Type": clean(raw.get("PO Doc Type")),
            "SAP PO Changed Date": iso_date(raw.get("PO Changed Date")),
            "SAP PO Created By": clean(raw.get("PO Created By")),
            "SAP PO Item Qty": amount(raw.get("PO Item Qty")),
            "SAP PO Item Unit": clean(raw.get("PO Item Unit")),
            "SAP Delivery Completed": clean(raw.get("Delivery Completed")),
            "SAP Final Invoice": clean(raw.get("Final Invoice")),
            "C4C Eligible For Repairer Analysis": "Yes" if c4c.get("eligible") else "No",
            "C4C Eligibility Match Source": clean(c4c.get("match_source")),
            "C4C Eligibility Reason": clean(c4c.get("reason")),
            "C4C Service Technician": clean(c4c.get("service_technician")),
            "C4C Status": clean(c4c.get("status")),
            "C4C PO": clean(c4c.get("po")),
            "C4C Ticket ID": clean(c4c.get("ticket_id")),
            "C4C Ticket": clean(c4c.get("ticket_title")),
            "C4C Created On": clean(c4c.get("created_on")),
            "C4C Posting Date": clean(c4c.get("posting_date")),
            "C4C Claim Approved On": clean(c4c.get("approved_on")),
            "C4C Chassis Number": clean(c4c.get("chassis")),
            "C4C Dealer": clean(c4c.get("dealer")),
            "C4C Dealer Name": clean(c4c.get("dealer_name")),
            "C4C Country/Region": clean(c4c.get("country_region")),
            "C4C Claim Total Amount": amount(c4c.get("claim_total")),
            "C4C Repairer Parts Claim Total Amount": amount(c4c.get("repairer_parts_total")),
            "C4C Repair Detail Exists": "Yes" if detail else "No",
            "C4C Repair Detail Ticket ID": clean(detail.get("Ticket ID")) if detail else "",
            "C4C Repair Detail PO": clean(detail.get("ERP Purchase Order ID")) if detail else "",
            "C4C Repair Detail Repairer": clean(detail.get("repairer_name")) if detail else "",
            "C4C Repair Detail Approved Cost": amount(detail.get("confirmed_cost_aud")) if detail else 0.0,
        }
        po_lines.append(line)
        if parsed_ticket and c4c.get("eligible"):
            grouped[parsed_ticket].append(line)
        elif parsed_ticket:
            c4c_ineligible.append(line)
        else:
            unreadable.append(line)

    summary_rows: list[dict[str, Any]] = []
    compare_rows: list[dict[str, Any]] = []
    for ticket_id, rows in sorted(grouped.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0]):
        currencies = sorted({clean(row.get("SAP PO Currency")) for row in rows if clean(row.get("SAP PO Currency"))})
        detail_exists = any(row.get("C4C Repair Detail Exists") == "Yes" for row in rows)
        c4c_repairers = [row.get("C4C Repair Detail Repairer") for row in rows]
        c4c_pos = [row.get("C4C Repair Detail PO") for row in rows]
        active_rows = [row for row in rows if row.get("SAP PO Cancelled") != "Yes"]
        cancelled_rows = [row for row in rows if row.get("SAP PO Cancelled") == "Yes"]
        main_rows = active_rows or rows
        sap_repairers = [row.get("SAP Repairer Name") for row in main_rows]
        sap_vendor_ids = [row.get("SAP Repairer Vendor ID") for row in main_rows]
        dates = sorted(clean(row.get("SAP PO Date")) for row in main_rows if clean(row.get("SAP PO Date")))
        invoice_dates = sorted(clean(row.get("SAP Last Invoice Date")) for row in main_rows if clean(row.get("SAP Last Invoice Date")))
        po_total = round(sum(amount(row.get("SAP Active PO Net Value")) for row in rows), 2)
        cancelled_total = round(sum(amount(row.get("SAP Cancelled PO Net Value")) for row in rows), 2)
        invoice_signed_total = round(sum(amount(row.get("SAP Signed Invoice Amount Doc")) for row in main_rows), 2)
        invoice_rows = sum(int(amount(row.get("SAP Invoice Rows"))) for row in main_rows)
        if cancelled_rows and active_rows:
            cancelled_status = "Mixed"
        elif cancelled_rows:
            cancelled_status = "Yes"
        else:
            cancelled_status = "No"
        ticket_summary = {
            "SAP Ticket ID": ticket_id,
            "SAP First PO Date": dates[0] if dates else "",
            "SAP Last PO Date": dates[-1] if dates else "",
            "SAP Last Invoice Date": invoice_dates[-1] if invoice_dates else "",
            "SAP Repairer Name": unique_join(sap_repairers),
            "SAP Repairer Vendor ID": unique_join(sap_vendor_ids),
            "SAP Invoice Docs": unique_join(row.get("SAP Last Invoice Doc") for row in main_rows),
            "SAP POs": unique_join(row.get("SAP PO") for row in main_rows),
            "SAP Cancelled POs": unique_join(row.get("SAP PO") for row in cancelled_rows),
            "SAP Line Count": len(rows),
            "SAP Active PO Line Count": len(active_rows),
            "SAP Cancelled PO Line Count": len(cancelled_rows),
            "SAP PO Net Value": po_total,
            "SAP Cancelled PO Net Value": cancelled_total,
            "SAP PO Cancelled": cancelled_status,
            "SAP Signed Invoice Amount": invoice_signed_total,
            "SAP Invoice Row Count": invoice_rows,
            "SAP Invoice Status": "Invoiced" if invoice_rows > 0 else "PO Only",
            "SAP Currency": ", ".join(currencies),
            "C4C Eligible For Repairer Analysis": "Yes",
            "C4C Eligibility Match Source": unique_join(row.get("C4C Eligibility Match Source") for row in rows),
            "C4C Eligibility Reason": unique_join(row.get("C4C Eligibility Reason") for row in rows),
            "C4C Service Technician": unique_join(row.get("C4C Service Technician") for row in rows),
            "C4C Status": unique_join(row.get("C4C Status") for row in rows),
            "C4C Ticket ID": unique_join(row.get("C4C Ticket ID") for row in rows),
            "C4C Ticket": unique_join(row.get("C4C Ticket") for row in rows),
            "C4C PO": unique_join(row.get("C4C PO") for row in rows),
            "C4C Created On": unique_join(row.get("C4C Created On") for row in rows),
            "C4C Posting Date": unique_join(row.get("C4C Posting Date") for row in rows),
            "C4C Claim Approved On": unique_join(row.get("C4C Claim Approved On") for row in rows),
            "C4C Chassis Number": unique_join(row.get("C4C Chassis Number") for row in rows),
            "C4C Dealer": unique_join(row.get("C4C Dealer") for row in rows),
            "C4C Dealer Name": unique_join(row.get("C4C Dealer Name") for row in rows),
            "C4C Country/Region": unique_join(row.get("C4C Country/Region") for row in rows),
            "C4C Claim Total Amount": max(amount(row.get("C4C Claim Total Amount")) for row in rows),
            "C4C Repairer Parts Claim Total Amount": max(amount(row.get("C4C Repairer Parts Claim Total Amount")) for row in rows),
            "C4C Repair Detail Exists": "Yes" if detail_exists else "No",
            "C4C Repair Detail Repairer": unique_join(c4c_repairers),
            "C4C Repair Detail PO": unique_join(c4c_pos),
            "C4C Repair Detail Approved Cost": max(amount(row.get("C4C Repair Detail Approved Cost")) for row in rows),
        }
        summary_rows.append(ticket_summary)
        compare_rows.append({
            **ticket_summary,
            "Repairer Match": "Yes" if clean(ticket_summary["SAP Repairer Name"]).lower() == clean(ticket_summary["C4C Repair Detail Repairer"]).lower() and detail_exists else "No",
            "Any SAP PO Equals C4C PO": "Yes" if any(clean(row.get("SAP PO")) == clean(row.get("C4C Repair Detail PO")) and clean(row.get("C4C Repair Detail PO")) for row in rows) else "No",
            "SAP Minus C4C Approved Cost": round(po_total - amount(ticket_summary["C4C Repair Detail Approved Cost"]), 2),
        })

    return {
        "summary": summary_rows,
        "po_lines": po_lines,
        "invoice_lines": po_lines,
        "short_text_unreadable": unreadable,
        "c4c_ineligible": c4c_ineligible,
        "c4c_compare": compare_rows,
    }


def build_meta(
    lines: list[dict[str, Any]],
    outputs: dict[str, Any],
    args: argparse.Namespace,
    c4c_lookup: dict[str, Any],
) -> list[dict[str, Any]]:
    parsed = [row for row in outputs["po_lines"] if clean(row.get("SAP Ticket ID"))]
    invoiced = [row for row in outputs["po_lines"] if amount(row.get("SAP Invoice Rows")) > 0]
    cancelled = [row for row in outputs["po_lines"] if row.get("SAP PO Cancelled") == "Yes"]
    eligible_lines = [row for row in outputs["po_lines"] if row.get("C4C Eligible For Repairer Analysis") == "Yes"]
    ineligible = outputs.get("c4c_ineligible") or []
    c4c_meta = c4c_lookup.get("meta") if isinstance(c4c_lookup.get("meta"), dict) else {}
    return [
        {"Metric": "Source of truth", "Value": "C4C-approved eligibility + SAP EKPO/EKKO PO rows + EKPO short text ticket ID"},
        {"Metric": "Date rule", "Value": "SAP PO document date = EKKO.BEDAT"},
        {"Metric": "Ticket ID rule", "Value": "Parse C4C ticket ID from EKPO.TXZ01 short text"},
        {"Metric": "Price rule", "Value": "SAP PO item net value = EKPO.NETWR"},
        {"Metric": "Repairer rule", "Value": "PO vendor / repairer = EKKO.LIFNR joined to LFA1.NAME1"},
        {"Metric": "Eligibility rule", "Value": "A SAP parsed ticket is included only when C4C analysis_ticket_base has a matching C4C Ticket ID, with a valid non-customer Service Technician and no excluded status. No PO fallback is used."},
        {"Metric": "Customer/self repair rule", "Value": "Exclude C4C Service Technician values that are blank/unassigned or contain customer plus repair/repairer."},
        {"Metric": "C4C eligibility source", "Value": clean(c4c_meta.get("path"))},
        {"Metric": "C4C rows read", "Value": c4c_meta.get("rows_read", 0)},
        {"Metric": "C4C eligible rows", "Value": c4c_meta.get("eligible_rows", 0)},
        {"Metric": "C4C unique ticket IDs", "Value": c4c_meta.get("unique_ticket_ids", 0)},
        {"Metric": "SAP client", "Value": args.mandt},
        {"Metric": "SAP schema", "Value": args.schema},
        {"Metric": "Purchasing org", "Value": args.purchasing_org},
        {"Metric": "Purchasing group", "Value": args.purchasing_group},
        {"Metric": "PO date from", "Value": iso_date(args.date_from)},
        {"Metric": "PO date to", "Value": iso_date(args.date_to) if args.date_to else ""},
        {"Metric": "SAP PO lines fetched", "Value": len(lines)},
        {"Metric": "Lines with parsed ticket ID", "Value": len(parsed)},
        {"Metric": "C4C eligible SAP PO lines", "Value": len(eligible_lines)},
        {"Metric": "C4C ineligible parsed SAP PO lines", "Value": len(ineligible)},
        {"Metric": "Short text unreadable lines", "Value": len(outputs["short_text_unreadable"])},
        {"Metric": "Cancelled/deleted PO lines", "Value": len(cancelled)},
        {"Metric": "PO lines already invoiced", "Value": len(invoiced)},
        {"Metric": "PO-only lines not invoiced yet", "Value": len(lines) - len(invoiced)},
        {"Metric": "Unique eligible SAP ticket IDs", "Value": len(outputs["summary"])},
    ]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build SAP-authoritative repair PO rows by short-text ticket ID.")
    parser.add_argument("--repair-details", default=str(DEFAULT_REPAIR_DETAILS))
    parser.add_argument("--c4c-source", default=str(DEFAULT_C4C_SOURCE))
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--mandt", default=DEFAULT_MANDT)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--purchasing-org", default=DEFAULT_PURCHASING_ORG)
    parser.add_argument("--purchasing-group", default=DEFAULT_PURCHASING_GROUP)
    parser.add_argument("--date-from", default=DEFAULT_DATE_FROM)
    parser.add_argument("--date-to", default=DEFAULT_DATE_TO)
    parser.add_argument("--exclude-cancelled-po", action="store_true", help="Exclude SAP PO rows where EKKO.LOEKZ or EKPO.LOEKZ is not blank.")
    args = parser.parse_args()
    args.date_from = normalize_date(args.date_from)
    args.date_to = normalize_date(args.date_to)

    detail_lookup = load_repair_detail_lookup(Path(args.repair_details))
    c4c_lookup = load_c4c_eligibility(Path(args.c4c_source))
    lines = fetch_sap_po_lines(
        args.dsn,
        args.mandt,
        args.schema,
        args.purchasing_org,
        args.purchasing_group,
        args.date_from,
        args.date_to,
        exclude_cancelled=args.exclude_cancelled_po,
    )
    outputs = build_outputs(lines, detail_lookup, c4c_lookup)
    payload = {
        "meta": build_meta(lines, outputs, args, c4c_lookup),
        **outputs,
    }
    write_json(Path(args.out_json), payload)
    print(json.dumps({
        "out_json": str(Path(args.out_json)),
        "po_lines": len(outputs["po_lines"]),
        "short_text_unreadable": len(outputs["short_text_unreadable"]),
        "c4c_ineligible": len(outputs["c4c_ineligible"]),
        "unique_eligible_sap_ticket_ids": len(outputs["summary"]),
        "c4c_compare_rows": len(outputs["c4c_compare"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
