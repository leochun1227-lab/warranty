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
DEFAULT_OUT_JSON = ROOT / "outputs" / "repairers_2026" / "ekbe_invoice_ticket_audit_data.json"
DEFAULT_DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)
DEFAULT_MANDT = os.getenv("SAP_CLIENT", "800")
DEFAULT_SCHEMA = os.getenv("SAP_SCHEMA", "SAPHANADB")

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


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def amount(value: Any) -> float:
    try:
        return round(float(clean(value).replace(",", "")), 2)
    except Exception:
        return 0.0


def load_repair_details(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("details") if isinstance(payload.get("details"), list) else []
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return rows, meta


def detail_lookup(rows: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]], list[str]]:
    by_po: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_c4c: dict[str, dict[str, Any]] = {}
    po_numbers: set[str] = set()
    for row in rows:
        po = clean(row.get("ERP Purchase Order ID") or row.get("ERPPurchaseOrder") or row.get("ERPPO"))
        c4c = normalize_ticket_id(row.get("C4C Ticket ID") or row.get("C4CTicketID") or row.get("c4c_ticket_id"))
        if po:
            po_numbers.add(po)
            by_po[po].append(row)
        if c4c:
            by_c4c[c4c] = row
    return by_po, by_c4c, sorted(po_numbers)


def fetch_ekbe_invoice_rows(po_numbers: list[str], dsn: str, mandt: str, schema: str) -> list[dict[str, Any]]:
    import pyodbc

    out: list[dict[str, Any]] = []
    with pyodbc.connect(dsn, autocommit=True, timeout=60) as conn:
        for batch in chunks(po_numbers, 450):
            in_list = ", ".join(f"'{sql_quote(po)}'" for po in batch)
            sql = f"""
                SELECT
                    ekbe."EBELN" AS "PO",
                    ekbe."EBELP" AS "PO Item",
                    ekbe."VGABE" AS "History Type",
                    ekbe."BELNR" AS "Invoice Doc",
                    ekbe."GJAHR" AS "Fiscal Year",
                    ekbe."BUZEI" AS "History Item",
                    ekbe."BUDAT" AS "Posting Date",
                    ekbe."MENGE" AS "Qty",
                    ekbe."DMBTR" AS "Amount Local",
                    ekbe."WRBTR" AS "Amount Doc",
                    ekbe."WAERS" AS "Currency",
                    ekbe."BEWTP" AS "History Category",
                    ekpo."TXZ01" AS "Short Text",
                    ekpo."NETWR" AS "PO Item Net Value",
                    ekpo."LOEKZ" AS "PO Item Deleted"
                FROM {full_table(schema, "EKBE")} ekbe
                LEFT JOIN {full_table(schema, "EKPO")} ekpo
                    ON ekpo."MANDT" = ekbe."MANDT"
                   AND ekpo."EBELN" = ekbe."EBELN"
                   AND ekpo."EBELP" = ekbe."EBELP"
                WHERE ekbe."MANDT" = '{sql_quote(mandt)}'
                  AND ekbe."VGABE" = '2'
                  AND ekbe."EBELN" IN ({in_list})
                ORDER BY ekbe."EBELN", ekbe."EBELP", ekbe."BUDAT", ekbe."BELNR"
            """
            cur = conn.execute(sql)
            columns = [column[0] for column in cur.description]
            for raw in cur.fetchall():
                out.append({columns[index]: raw[index] for index in range(len(columns))})
    return out


def build_audit_rows(
    ekbe_rows: list[dict[str, Any]],
    by_po: dict[str, list[dict[str, Any]]],
    by_c4c: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    unified: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    parsed_not_in_repair_detail: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []

    for row in ekbe_rows:
        po = clean(row.get("PO"))
        short_text = clean(row.get("Short Text"))
        extracted_ticket, note = extract_ticket_number(short_text)
        po_candidates = by_po.get(po, [])
        po_candidate_ids = sorted({
            normalize_ticket_id(candidate.get("C4C Ticket ID") or candidate.get("C4CTicketID") or candidate.get("c4c_ticket_id"))
            for candidate in po_candidates
            if normalize_ticket_id(candidate.get("C4C Ticket ID") or candidate.get("C4CTicketID") or candidate.get("c4c_ticket_id"))
        })
        ticket_row = by_c4c.get(extracted_ticket) if extracted_ticket else None
        po_match = bool(ticket_row and clean(ticket_row.get("ERP Purchase Order ID")) == po)
        candidate_match = extracted_ticket in po_candidate_ids if extracted_ticket else False
        if extracted_ticket and ticket_row:
            match_status = "Unified by short text ticket ID"
        elif extracted_ticket:
            match_status = "Ticket parsed from short text, but not found in repair detail"
        else:
            match_status = note or "No ticket parsed"

        base = {
            "PO": po,
            "PO Item": clean(row.get("PO Item")),
            "Invoice Doc": clean(row.get("Invoice Doc")),
            "Fiscal Year": clean(row.get("Fiscal Year")),
            "History Item": clean(row.get("History Item")),
            "Posting Date": clean(row.get("Posting Date")),
            "Amount Local": amount(row.get("Amount Local")),
            "Amount Doc": amount(row.get("Amount Doc")),
            "Currency": clean(row.get("Currency")),
            "Short Text": short_text,
            "Parsed Ticket ID": extracted_ticket,
            "Parse Note": note,
            "Match Status": match_status,
            "Short Text Ticket Found In Repair Detail": "Yes" if ticket_row else "No",
            "Repair Detail PO Equals SAP PO": "Yes" if po_match else "No",
            "Parsed Ticket Is One Of PO Candidate Tickets": "Yes" if candidate_match else "No",
            "PO Candidate Ticket IDs": ", ".join(po_candidate_ids),
            "PO Candidate Count": len(po_candidate_ids),
            "Repair Detail Ticket ID": clean(ticket_row.get("Ticket ID")) if ticket_row else "",
            "Repair Detail C4C Ticket ID": clean(ticket_row.get("C4C Ticket ID")) if ticket_row else "",
            "Repair Shop": clean(ticket_row.get("repairer_name")) if ticket_row else "",
            "Repair State": clean(ticket_row.get("state")) if ticket_row else "",
            "Repair Detail PO": clean(ticket_row.get("ERP Purchase Order ID")) if ticket_row else "",
            "Repair Detail Invoice Number": clean(ticket_row.get("invoice_number")) if ticket_row else "",
            "Repair Detail Approved Cost": amount(ticket_row.get("confirmed_cost_aud")) if ticket_row else 0.0,
        }
        raw_rows.append({**base, **{f"Raw {key}": clean(value) for key, value in row.items()}})
        if not extracted_ticket:
            unreadable.append(base)
        elif ticket_row:
            unified.append(base)
        else:
            parsed_not_in_repair_detail.append(base)
    return unified, unreadable, parsed_not_in_repair_detail, raw_rows


def summary_rows(
    detail_rows: list[dict[str, Any]],
    po_numbers: list[str],
    ekbe_rows: list[dict[str, Any]],
    unified: list[dict[str, Any]],
    unreadable: list[dict[str, Any]],
    parsed_not_in_repair_detail: list[dict[str, Any]],
    source_path: Path,
    dsn: str,
    mandt: str,
) -> list[dict[str, Any]]:
    parsed = [row for row in unified + parsed_not_in_repair_detail if clean(row.get("Parsed Ticket ID"))]
    po_diff = [row for row in unified if row.get("Repair Detail PO Equals SAP PO") != "Yes"]
    return [
        {"Metric": "Repair detail source", "Value": str(source_path)},
        {"Metric": "SAP client", "Value": mandt},
        {"Metric": "SAP DSN", "Value": re.sub(r"PWD=[^;]+", "PWD=***", dsn, flags=re.IGNORECASE)},
        {"Metric": "Repair detail rows", "Value": len(detail_rows)},
        {"Metric": "Unique repair POs checked", "Value": len(po_numbers)},
        {"Metric": "EKBE invoice rows fetched", "Value": len(ekbe_rows)},
        {"Metric": "Rows with parsed ticket ID", "Value": len(parsed)},
        {"Metric": "Unified by short text ticket ID", "Value": len(unified)},
        {"Metric": "Short text unreadable rows", "Value": len(unreadable)},
        {"Metric": "Parsed ticket not found in repair detail", "Value": len(parsed_not_in_repair_detail)},
        {"Metric": "Unified rows where repair detail PO differs from SAP PO", "Value": len(po_diff)},
        {"Metric": "Rule", "Value": "SAP short text parsed ticket ID is treated as the source of truth. PO mismatch is reported as a warning, not used to reject the ticket match."},
    ]


def write_json_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit EKBE invoice rows against ticket IDs parsed from SAP PO short text.")
    parser.add_argument("--repair-details", default=str(DEFAULT_REPAIR_DETAILS))
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--mandt", default=DEFAULT_MANDT)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = parser.parse_args()

    source_path = Path(args.repair_details)
    detail_rows, _meta = load_repair_details(source_path)
    by_po, by_c4c, po_numbers = detail_lookup(detail_rows)
    ekbe_rows = fetch_ekbe_invoice_rows(po_numbers, args.dsn, args.mandt, args.schema)
    unified, unreadable, parsed_not_in_repair_detail, raw_rows = build_audit_rows(ekbe_rows, by_po, by_c4c)
    payload = {
        "summary": summary_rows(
            detail_rows,
            po_numbers,
            ekbe_rows,
            unified,
            unreadable,
            parsed_not_in_repair_detail,
            source_path,
            args.dsn,
            args.mandt,
        ),
        "unified_by_short_text": unified,
        "short_text_unreadable": unreadable,
        "parsed_ticket_not_in_repair_detail": parsed_not_in_repair_detail,
        "raw": raw_rows,
    }
    write_json_payload(Path(args.out_json), payload)
    print(json.dumps({
        "out_json": str(Path(args.out_json)),
        "repair_detail_rows": len(detail_rows),
        "unique_pos": len(po_numbers),
        "ekbe_invoice_rows": len(ekbe_rows),
        "unified_by_short_text": len(unified),
        "short_text_unreadable": len(unreadable),
        "parsed_ticket_not_in_repair_detail": len(parsed_not_in_repair_detail),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
