from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARTS_CSV = ROOT / "outputs" / "parts_classified.csv"
DEFAULT_OUT_JSON = ROOT / "outputs" / "ticket_item_invoice_monthly_20260820" / "ticket_item_invoice_monthly.json"
DEFAULT_DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)
DEFAULT_MANDT = os.getenv("SAP_CLIENT", "800")
DEFAULT_SCHEMA = os.getenv("SAP_SCHEMA", "SAPHANADB")
DEFAULT_DATE_FROM = "20250901"
DEFAULT_DATE_TO = "20260820"

TICKET_NO_PATTERN = re.compile(
    r"\btickets?\s*no\.?\s*[:#\-]?\s*\[?\s*(\d+)\s*\]?\b",
    flags=re.IGNORECASE,
)
TICKET_BRACKET_PATTERN = re.compile(r"\btickets?\s*\[\s*(\d+)\s*\]", flags=re.IGNORECASE)
TICKET_OUTER_BRACKET_PATTERN = re.compile(
    r"\[\s*tickets?\s+(\d+)\s*(?:[\]\}]|$|[,;]|\s)?",
    flags=re.IGNORECASE,
)


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"#", "None", "nan", "NaN"} else text


def normalize_ticket_id(value: Any) -> str:
    text = clean(value)
    digits = re.sub(r"\D+", "", text)
    return str(int(digits)) if digits else ""


def normalize_date(value: Any) -> str:
    digits = re.sub(r"\D+", "", clean(value))
    if len(digits) == 8:
        return digits
    return ""


def iso_date(value: Any) -> str:
    digits = normalize_date(value)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return clean(value)


def parse_date_value(value: Any) -> date | None:
    text = clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            pass
    return None


def month_key(iso: str) -> str:
    return iso[:7] if re.match(r"^\d{4}-\d{2}-\d{2}$", iso) else ""


def amount(value: Any) -> float:
    try:
        return round(float(clean(value).replace(",", "")), 2)
    except Exception:
        return 0.0


def sql_quote(value: Any) -> str:
    return str(value).replace("'", "''")


def quote_ident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def full_table(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def unique_join(values: Any) -> str:
    seen = []
    for value in values:
        text = clean(value)
        if text and text not in seen:
            seen.append(text)
    return ", ".join(seen)


def extract_ticket_number(short_text: Any) -> tuple[str, str]:
    text = clean(short_text)
    if not text:
        return "", "Short Text is blank"
    matches: list[str] = []
    matches.extend(TICKET_NO_PATTERN.findall(text))
    matches.extend(TICKET_BRACKET_PATTERN.findall(text))
    matches.extend(TICKET_OUTER_BRACKET_PATTERN.findall(text))
    unique = []
    seen = set()
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
        return "", "Contains Ticket word but no standard number"
    return "", "No standard Ticket No. number or Ticket [number] pattern"


def fetch_sap_invoice_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    try:
        import pyodbc  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"pyodbc import failed: {exc}") from exc

    query = f"""
        WITH INVOICE_POS AS (
            SELECT DISTINCT
                ekbe."EBELN",
                ekbe."EBELP"
            FROM {full_table(args.schema, "EKBE")} ekbe
            LEFT JOIN {full_table(args.schema, "RBKP")} rbkp
                ON ekbe."BELNR" = rbkp."BELNR"
               AND ekbe."MANDT" = rbkp."MANDT"
            WHERE ekbe."MANDT" = '{sql_quote(args.mandt)}'
              AND ekbe."BEWTP" = 'Q'
              AND ekbe."BUDAT" >= '{sql_quote(args.date_from)}'
              AND ekbe."BUDAT" <= '{sql_quote(args.date_to)}'
              AND COALESCE(rbkp."STBLG", '') = ''
        ),
        LATEST_GR AS (
            SELECT
                ekbe."EBELN",
                ekbe."EBELP",
                ekbe."BELNR",
                ekbe."BUDAT",
                ekbe."BWART",
                ROW_NUMBER() OVER (
                    PARTITION BY ekbe."EBELN", ekbe."EBELP"
                    ORDER BY ekbe."BUDAT" DESC, ekbe."BELNR" DESC
                ) AS "RN"
            FROM {full_table(args.schema, "EKBE")} ekbe
            INNER JOIN INVOICE_POS ip
                ON ip."EBELN" = ekbe."EBELN"
               AND ip."EBELP" = ekbe."EBELP"
            WHERE ekbe."MANDT" = '{sql_quote(args.mandt)}'
              AND ekbe."BEWTP" = 'E'
        )
        SELECT
            ekbe."EBELN" AS "SAP PO",
            ekbe."EBELP" AS "SAP PO Item",
            MIN(CASE WHEN ekbe."BEWTP" = 'Q' THEN ekbe."BUDAT" END) AS "SAP First Invoice Date Raw",
            MAX(CASE WHEN ekbe."BEWTP" = 'Q' THEN ekbe."BUDAT" END) AS "SAP Invoice Date Raw",
            MIN(CASE WHEN ekbe."BEWTP" = 'Q' THEN ekbe."BELNR" END) AS "SAP First Invoice Doc",
            MAX(CASE WHEN ekbe."BEWTP" = 'Q' THEN ekbe."BELNR" END) AS "SAP Invoice Doc",
            SUM(CASE WHEN ekbe."BEWTP" = 'Q' THEN 1 ELSE 0 END) AS "SAP Invoice Rows",
            CASE WHEN lg."BWART" = '101' THEN lg."BELNR" ELSE NULL END AS "SAP Latest Valid GR",
            CASE WHEN lg."BWART" = '101' THEN lg."BUDAT" ELSE NULL END AS "SAP Latest Valid GR Posting Date Raw",
            SUM(CASE
                WHEN ekbe."BEWTP" = 'E' AND ekbe."SHKZG" = 'S' THEN ekbe."DMBTR"
                WHEN ekbe."BEWTP" = 'E' AND ekbe."SHKZG" = 'H' THEN -ekbe."DMBTR"
                ELSE 0
            END) AS "SAP GR Amt",
            SUM(CASE
                WHEN ekbe."BEWTP" = 'Q' AND ekbe."SHKZG" = 'S' THEN ekbe."DMBTR"
                WHEN ekbe."BEWTP" = 'Q' AND ekbe."SHKZG" = 'H' THEN -ekbe."DMBTR"
                ELSE 0
            END) AS "SAP Invoice Amount Local",
            SUM(CASE
                WHEN ekbe."BEWTP" = 'A' AND ekbe."SHKZG" = 'S' THEN ekbe."DMBTR"
                WHEN ekbe."BEWTP" = 'A' AND ekbe."SHKZG" = 'H' THEN -ekbe."DMBTR"
                ELSE 0
            END) AS "SAP Down Payment Amt",
            SUM(CASE
                WHEN ekbe."BEWTP" = '3' AND ekbe."SHKZG" = 'S' THEN ekbe."DMBTR"
                WHEN ekbe."BEWTP" = '3' AND ekbe."SHKZG" = 'H' THEN -ekbe."DMBTR"
                ELSE 0
            END) AS "SAP Down Payment Clearing",
            MAX(ekbe."WAERS") AS "SAP Currency",
            ekpo."TXZ01" AS "SAP Short Text",
            ekpo."MATNR" AS "SAP Material",
            ekpo."MATKL" AS "SAP Material Group",
            ekpo."NETWR" AS "SAP PO Net Value",
            ekpo."MENGE" AS "SAP PO Item Qty",
            ekpo."MEINS" AS "SAP PO Item Unit",
            ekpo."LOEKZ" AS "SAP PO Item Deletion Indicator",
            ekpo."EREKZ" AS "SAP Final Invoice",
            ekko."BEDAT" AS "SAP PO Date Raw",
            ekko."LIFNR" AS "SAP Repairer Vendor ID",
            ekko."LOEKZ" AS "SAP PO Header Deletion Indicator",
            ekko."WAERS" AS "SAP PO Currency",
            ekko."EKORG" AS "SAP Purchasing Org",
            ekko."EKGRP" AS "SAP Purchasing Group",
            lfa1."NAME1" AS "SAP Repairer Name",
            lfa1."ORT01" AS "SAP Repairer City",
            lfa1."REGIO" AS "SAP Repairer Region",
            lfa1."LAND1" AS "SAP Repairer Country"
        FROM {full_table(args.schema, "EKBE")} ekbe
        INNER JOIN INVOICE_POS ip
            ON ip."EBELN" = ekbe."EBELN"
           AND ip."EBELP" = ekbe."EBELP"
        LEFT JOIN {full_table(args.schema, "RBKP")} rbkp
            ON ekbe."BELNR" = rbkp."BELNR"
           AND ekbe."MANDT" = rbkp."MANDT"
        INNER JOIN {full_table(args.schema, "EKPO")} ekpo
            ON ekpo."MANDT" = ekbe."MANDT"
           AND ekpo."EBELN" = ekbe."EBELN"
           AND ekpo."EBELP" = ekbe."EBELP"
        INNER JOIN {full_table(args.schema, "EKKO")} ekko
            ON ekko."MANDT" = ekpo."MANDT"
           AND ekko."EBELN" = ekpo."EBELN"
        LEFT JOIN {full_table(args.schema, "LFA1")} lfa1
            ON lfa1."MANDT" = ekko."MANDT"
           AND lfa1."LIFNR" = ekko."LIFNR"
        LEFT JOIN LATEST_GR lg
            ON lg."EBELN" = ekbe."EBELN"
           AND lg."EBELP" = ekbe."EBELP"
           AND lg."RN" = 1
        WHERE ekbe."MANDT" = '{sql_quote(args.mandt)}'
          AND COALESCE(rbkp."STBLG", '') = ''
          AND ekko."EKORG" = '{sql_quote(args.purchasing_org)}'
          AND ekko."EKGRP" = '{sql_quote(args.purchasing_group)}'
        GROUP BY
            ekbe."EBELN",
            ekbe."EBELP",
            lg."BELNR",
            lg."BUDAT",
            lg."BWART",
            ekpo."TXZ01",
            ekpo."MATNR",
            ekpo."MATKL",
            ekpo."NETWR",
            ekpo."MENGE",
            ekpo."MEINS",
            ekpo."LOEKZ",
            ekpo."EREKZ",
            ekko."BEDAT",
            ekko."LIFNR",
            ekko."LOEKZ",
            ekko."WAERS",
            ekko."EKORG",
            ekko."EKGRP",
            lfa1."NAME1",
            lfa1."ORT01",
            lfa1."REGIO",
            lfa1."LAND1"
        HAVING MAX(CASE WHEN ekbe."BEWTP" = 'Q' THEN ekbe."BUDAT" END) >= '{sql_quote(args.date_from)}'
           AND MAX(CASE WHEN ekbe."BEWTP" = 'Q' THEN ekbe."BUDAT" END) <= '{sql_quote(args.date_to)}'
        ORDER BY "SAP Invoice Date Raw", ekbe."EBELN", ekbe."EBELP"
    """
    with pyodbc.connect(args.dsn, timeout=30) as conn:
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        out = []
        for values in cursor.fetchall():
            row = dict(zip(columns, values))
            ticket_id, note = extract_ticket_number(row.get("SAP Short Text"))
            out.append(
                {
                    **{key: clean(value) for key, value in row.items()},
                    "SAP Ticket ID": ticket_id,
                    "SAP Short Text Parse Note": note,
                    "SAP Invoice Date": iso_date(row.get("SAP Invoice Date Raw")),
                    "SAP First Invoice Date": iso_date(row.get("SAP First Invoice Date Raw")),
                    "SAP Latest Valid GR Posting Date": iso_date(row.get("SAP Latest Valid GR Posting Date Raw")),
                    "SAP PO Date": iso_date(row.get("SAP PO Date Raw")),
                    "SAP Signed Invoice Amount Doc": amount(row.get("SAP Invoice Amount Local")),
                    "SAP Invoice Rows": int(amount(row.get("SAP Invoice Rows"))),
                    "SAP GR Amt": amount(row.get("SAP GR Amt")),
                    "SAP Down Payment Amt": amount(row.get("SAP Down Payment Amt")),
                    "SAP Down Payment Clearing": amount(row.get("SAP Down Payment Clearing")),
                    "SAP PO Net Value": amount(row.get("SAP PO Net Value")),
                    "SAP Invoice Amount Local": amount(row.get("SAP Invoice Amount Local")),
                    "SAP PO Item Qty": amount(row.get("SAP PO Item Qty")),
                }
            )
    return out


def add_index(index: dict[str, dict[str, dict[str, Any]]], key: Any, ticket_id: str, attrs: dict[str, Any], source: str) -> None:
    text = clean(key).upper()
    if not text:
        return
    index.setdefault(text, {})[ticket_id] = {**attrs, "Match Source": source}


def load_c4c_items(path: Path) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, dict[str, Any]]],
    dict[str, dict[str, dict[str, Any]]],
]:
    by_ticket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ticket_attrs: dict[str, dict[str, Any]] = {}
    po_index: dict[str, dict[str, dict[str, Any]]] = {}
    vehicle_index: dict[str, dict[str, dict[str, Any]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ticket_id = normalize_ticket_id(row.get("Ticket ID") or row.get("Ticket Key"))
            if not ticket_id:
                continue
            item = {
                "Ticket ID": ticket_id,
                "Sales Order": clean(row.get("Sales Order")),
                "Sales Order Item": clean(row.get("Sales Order Item")),
                "Material": clean(row.get("Material")),
                "Description": clean(row.get("Description")),
                "Order Qty": amount(row.get("Order Qty")),
                "Sales Unit": clean(row.get("Sales Unit")),
                "Dealer ID": clean(row.get("Dealer ID")),
                "Dealer Name": clean(row.get("Dealer Name")),
                "Ticket Status Text": clean(row.get("Ticket Status Text")),
                "SO Created Date": clean(row.get("SO Created Date")),
                "ERP Purchase Order": clean(row.get("ERP Purchase Order")),
                "Amount Including Tax": amount(row.get("Amount Including Tax")),
                "Preferred Line Cost (AUD)": amount(row.get("Preferred Line Cost (AUD)")),
                "Item Rejection Status": clean(row.get("Item Rejection Status")),
                "Part Category": clean(row.get("Part Category")),
                "Matched Keyword": clean(row.get("Matched Keyword")),
            }
            by_ticket[ticket_id].append(item)
            attrs = ticket_attrs.setdefault(
                ticket_id,
                {
                    "C4C Ticket ID": ticket_id,
                    "C4C Ticket Status": clean(row.get("Ticket Status Text")),
                    "C4C Dealer ID": clean(row.get("Dealer ID")),
                    "C4C Dealer Name": clean(row.get("Dealer Name")),
                    "C4C Sales Order": clean(row.get("Sales Order")),
                    "C4C SO Created Date": clean(row.get("SO Created Date")),
                },
            )
            add_index(po_index, row.get("ERP Purchase Order"), ticket_id, attrs, "parts_classified ERP Purchase Order")
            for field in ("Sales Order", "Material"):
                add_index(vehicle_index, row.get(field), ticket_id, attrs, f"parts_classified {field}")
    return by_ticket, ticket_attrs, po_index, vehicle_index


def load_c4c_ticket_base_indices(path: Path, po_index: dict[str, dict[str, dict[str, Any]]], vehicle_index: dict[str, dict[str, dict[str, Any]]]) -> None:
    if not path.exists():
        return
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ticket_id = normalize_ticket_id(row.get("C4C Ticket ID") or row.get("Ticket ID"))
            if not ticket_id:
                continue
            attrs = {
                "C4C Ticket ID": ticket_id,
                "C4C Ticket Status": clean(row.get("Status")),
                "C4C Dealer ID": clean(row.get("Dealer")),
                "C4C Dealer Name": clean(row.get("Dealer Name")),
                "C4C Sales Order": clean(row.get("ERP Free Order ID")),
                "C4C SO Created Date": clean(row.get("Created On")),
            }
            add_index(po_index, row.get("ERP Purchase Order ID"), ticket_id, attrs, "analysis_ticket_base ERP Purchase Order ID")
            for field in ("Serial ID", "Chassis Number", "Registered Product", "Product"):
                value = clean(row.get(field)).upper()
                if value and value not in {"TBA", "NOT ASSIGNED"}:
                    add_index(vehicle_index, value, ticket_id, attrs, f"analysis_ticket_base {field}")


def recover_ticket_id_from_c4c(row: dict[str, Any], po_index: dict[str, dict[str, dict[str, Any]]], vehicle_index: dict[str, dict[str, dict[str, Any]]]) -> tuple[str, str, str, dict[str, Any]]:
    po = clean(row.get("SAP PO")).upper()
    short_text = clean(row.get("SAP Short Text")).upper()
    candidates = po_index.get(po, {}) if po else {}
    if len(candidates) == 1:
        ticket_id, attrs = next(iter(candidates.items()))
        return ticket_id, "C4C PO exact", ticket_id, attrs
    if len(candidates) > 1:
        return "", "C4C PO matched multiple tickets", ", ".join(sorted(candidates)), {}

    vehicle_candidates = vehicle_index.get(short_text, {}) if short_text else {}
    if len(vehicle_candidates) == 1:
        ticket_id, attrs = next(iter(vehicle_candidates.items()))
        return ticket_id, "C4C serial/chassis exact unique", ticket_id, attrs
    if len(vehicle_candidates) > 1:
        anchor = parse_date_value(row.get("SAP Invoice Date")) or parse_date_value(row.get("SAP PO Date"))
        scored: list[tuple[int, str, dict[str, Any]]] = []
        if anchor:
            for ticket_id, attrs in vehicle_candidates.items():
                ticket_date = parse_date_value(attrs.get("C4C SO Created Date"))
                if ticket_date:
                    scored.append((abs((ticket_date - anchor).days), ticket_id, attrs))
        scored.sort(key=lambda item: (item[0], int(item[1]) if item[1].isdigit() else 999999999))
        if scored:
            best_days, ticket_id, attrs = scored[0]
            second_days = scored[1][0] if len(scored) > 1 else 999999
            if best_days <= 90 and second_days - best_days >= 30:
                return (
                    ticket_id,
                    f"C4C serial/chassis date-disambiguated ({best_days} days from SAP date)",
                    ", ".join(sorted(vehicle_candidates)),
                    attrs,
                )
        return "", "C4C serial/chassis matched multiple tickets", ", ".join(sorted(vehicle_candidates)), {}
    return "", "No C4C PO or unique serial/chassis match", "", {}


def build_outputs(
    sap_rows: list[dict[str, Any]],
    c4c_by_ticket: dict[str, list[dict[str, Any]]],
    c4c_attrs: dict[str, dict[str, Any]],
    po_index: dict[str, dict[str, dict[str, Any]]],
    vehicle_index: dict[str, dict[str, dict[str, Any]]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    sap_by_ticket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unreadable = []
    recovered_rows = []
    for row in sap_rows:
        ticket_id = clean(row.get("SAP Ticket ID"))
        if not ticket_id:
            recovered_ticket, method, candidates, attrs = recover_ticket_id_from_c4c(row, po_index, vehicle_index)
            row["C4C Recovery Method"] = method
            row["C4C Recovery Candidates"] = candidates
            if recovered_ticket:
                row["SAP Ticket ID"] = recovered_ticket
                row["SAP Short Text Parse Note"] = clean(row.get("SAP Short Text Parse Note")) + "; recovered from " + method
                c4c_attrs.setdefault(recovered_ticket, attrs)
                ticket_id = recovered_ticket
                recovered_rows.append(row)
        if ticket_id:
            sap_by_ticket[ticket_id].append(row)
        else:
            unreadable.append(row)

    ticket_summary = []
    item_detail = []
    for ticket_id, rows in sorted(sap_by_ticket.items(), key=lambda item: (max(clean(r.get("SAP Invoice Date")) for r in item[1]), int(item[0]) if item[0].isdigit() else 0)):
        invoice_dates = sorted(clean(row.get("SAP Invoice Date")) for row in rows if clean(row.get("SAP Invoice Date")))
        latest_invoice = invoice_dates[-1] if invoice_dates else ""
        items = c4c_by_ticket.get(ticket_id, [])
        not_rejected = [item for item in items if clean(item.get("Item Rejection Status")).lower() != "rejected"]
        attrs = c4c_attrs.get(ticket_id, {})
        summary_row = {
            "Invoice Month": month_key(latest_invoice),
            "SAP Ticket ID": ticket_id,
            "SAP First Invoice Date": invoice_dates[0] if invoice_dates else "",
            "SAP Last Invoice Date": latest_invoice,
            "SAP Invoice Docs": unique_join(row.get("SAP Invoice Doc") for row in rows),
            "SAP POs": unique_join(row.get("SAP PO") for row in rows),
            "SAP Invoice Row Count": sum(int(amount(row.get("SAP Invoice Rows"))) for row in rows),
            "SAP Signed Invoice Amount": round(sum(amount(row.get("SAP Signed Invoice Amount Doc")) for row in rows), 2),
            "SAP Currency": unique_join(row.get("SAP Currency") for row in rows),
            "SAP Repairer Name": unique_join(row.get("SAP Repairer Name") for row in rows),
            "SAP Repairer Vendor ID": unique_join(row.get("SAP Repairer Vendor ID") for row in rows),
            "C4C Item Rows": len(items),
            "C4C Item Rows Not Rejected": len(not_rejected),
            "C4C Item Qty": round(sum(amount(item.get("Order Qty")) for item in items), 2),
            "C4C Item Preferred Cost AUD": round(sum(amount(item.get("Preferred Line Cost (AUD)")) for item in items), 2),
            **attrs,
        }
        ticket_summary.append(summary_row)
        for item in items:
            item_detail.append(
                {
                    "Invoice Month": summary_row["Invoice Month"],
                    "SAP Last Invoice Date": latest_invoice,
                    "SAP Invoice Docs": summary_row["SAP Invoice Docs"],
                    "SAP POs": summary_row["SAP POs"],
                    **item,
                }
            )

    monthly: dict[str, dict[str, Any]] = {}
    start = datetime.strptime(args.date_from, "%Y%m%d")
    end = datetime.strptime(args.date_to, "%Y%m%d")
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        key = f"{year:04d}-{month:02d}"
        monthly[key] = {
            "Month": key,
            "Tickets": 0,
            "C4C Item Rows": 0,
            "C4C Item Rows Not Rejected": 0,
            "C4C Item Qty": 0.0,
            "SAP Invoice Row Count": 0,
            "SAP Signed Invoice Amount": 0.0,
            "Avg C4C Item Rows per Ticket": 0.0,
        }
        month += 1
        if month == 13:
            year += 1
            month = 1
    for row in ticket_summary:
        key = clean(row.get("Invoice Month"))
        if key not in monthly:
            continue
        monthly[key]["Tickets"] += 1
        monthly[key]["C4C Item Rows"] += int(row.get("C4C Item Rows") or 0)
        monthly[key]["C4C Item Rows Not Rejected"] += int(row.get("C4C Item Rows Not Rejected") or 0)
        monthly[key]["C4C Item Qty"] = round(monthly[key]["C4C Item Qty"] + amount(row.get("C4C Item Qty")), 2)
        monthly[key]["SAP Invoice Row Count"] += int(row.get("SAP Invoice Row Count") or 0)
        monthly[key]["SAP Signed Invoice Amount"] = round(monthly[key]["SAP Signed Invoice Amount"] + amount(row.get("SAP Signed Invoice Amount")), 2)
    for row in monthly.values():
        tickets = row["Tickets"]
        row["Avg C4C Item Rows per Ticket"] = round(row["C4C Item Rows"] / tickets, 2) if tickets else 0.0

    return {
        "meta": {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "dateRule": "SAP EKBE PO-history aggregation; invoice amount uses BEWTP='Q' signed DMBTR with RBKP.STBLG reversals excluded; tickets allocated to month of latest BEWTP='Q' BUDAT",
            "dateFrom": iso_date(args.date_from),
            "dateTo": iso_date(args.date_to),
            "sapRows": len(sap_rows),
            "sapRowGrain": "one row per SAP PO item with at least one non-reversed BEWTP='Q' invoice in range",
            "parsedTicketRows": sum(1 for row in sap_rows if clean(row.get("SAP Ticket ID"))),
            "unreadableSapRows": len(unreadable),
            "recoveredSapRows": len(recovered_rows),
            "recoveryRule": "If SAP short text has no ticket number, recover by C4C PO exact match first; if no PO match, recover by unique C4C serial/chassis match; if serial/chassis has multiple candidate tickets, choose the candidate whose C4C created date is clearly closest to the SAP invoice/PO date.",
            "ticketCount": len(ticket_summary),
            "itemDetailRows": len(item_detail),
            "c4cPartsSource": str(args.parts_csv),
            "sapSource": "SAP HANA EKBE PO-history aggregation joined to RBKP/EKPO/EKKO/LFA1",
        },
        "monthly": [monthly[key] for key in sorted(monthly)],
        "ticket_summary": ticket_summary,
        "item_detail": item_detail,
        "sap_invoice_rows": sap_rows,
        "sap_recovered_rows": recovered_rows,
        "sap_unreadable_short_text": unreadable,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date-from", default=DEFAULT_DATE_FROM)
    parser.add_argument("--date-to", default=DEFAULT_DATE_TO)
    parser.add_argument("--parts-csv", type=Path, default=DEFAULT_PARTS_CSV)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--mandt", default=DEFAULT_MANDT)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--purchasing-org", default=os.getenv("PURCHASING_ORG", "3111"))
    parser.add_argument("--purchasing-group", default=os.getenv("PURCHASING_GROUP", "E06"))
    args = parser.parse_args()
    args.date_from = normalize_date(args.date_from)
    args.date_to = normalize_date(args.date_to)
    if not args.date_from or not args.date_to:
        raise SystemExit("date-from and date-to must be YYYYMMDD or YYYY-MM-DD")

    c4c_by_ticket, c4c_attrs, po_index, vehicle_index = load_c4c_items(args.parts_csv)
    load_c4c_ticket_base_indices(ROOT / "outputs" / "analysis_ticket_base.csv", po_index, vehicle_index)
    sap_rows = fetch_sap_invoice_rows(args)
    payload = build_outputs(sap_rows, c4c_by_ticket, c4c_attrs, po_index, vehicle_index, args)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
