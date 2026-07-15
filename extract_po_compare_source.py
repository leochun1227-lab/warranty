import json
from datetime import date, datetime, time

import openpyxl


INPUT = r"C:\Users\Leo.Li\Downloads\claim_ytd_comparison_tickets_detail_20260709_135559.xlsx"
OUTPUT = "po_compare_source.json"


def clean(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip()
    return value


def number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


wb = openpyxl.load_workbook(INPUT, read_only=True, data_only=True)
tickets = wb["Tickets"]
sheet1 = wb["Sheet1"]

ticket_map = {}
for row in tickets.iter_rows(min_row=5, values_only=True):
    ticket_id = row[0]
    if ticket_id in (None, ""):
        continue
    ticket_map[ticket_id] = {
        "claim_scope": clean(row[1]),
        "status_group": clean(row[2]),
        "status_text": clean(row[3]),
        "claim_approved_on": clean(row[6]),
        "dealer_name": clean(row[9]),
        "ticket_type": clean(row[10]),
        "ticket_type_text": clean(row[11]),
        "created_date": clean(row[12]),
        "approved_date": clean(row[13]),
        "resolved_date": clean(row[14]),
        "amount_including_tax": number(row[8]),
        "amount_value": number(row[15]),
    }

rows = []
for row in sheet1.iter_rows(min_row=2, values_only=True):
    ticket_id = row[0]
    po_price = number(row[4])
    if ticket_id in (None, "") or po_price is None:
        continue
    ticket = ticket_map.get(ticket_id, {})
    rows.append(
        {
            "ticket": ticket_id,
            "po_price": po_price,
            "amount_including_tax": ticket.get("amount_including_tax"),
            "status_group": ticket.get("status_group"),
            "status_text": ticket.get("status_text"),
            "claim_scope": ticket.get("claim_scope"),
            "claim_approved_on": ticket.get("claim_approved_on"),
            "dealer_name": ticket.get("dealer_name"),
            "ticket_type": ticket.get("ticket_type"),
            "ticket_type_text": ticket.get("ticket_type_text"),
            "created_date": ticket.get("created_date"),
            "approved_date": ticket.get("approved_date"),
            "resolved_date": ticket.get("resolved_date"),
            "po_date": clean(row[3]),
            "amount_value": ticket.get("amount_value"),
        }
    )

with open(OUTPUT, "w", encoding="utf-8") as handle:
    json.dump(rows, handle, ensure_ascii=False, indent=2)

print(json.dumps({"rows_with_po": len(rows)}, ensure_ascii=False))
