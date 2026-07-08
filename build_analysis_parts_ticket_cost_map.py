import json
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "analysis_parts_ticket_cost_map.json"
DB_URL = "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app"
FB_TICKETS = "c4cTickets_test/tickets"
FB_TICKETS_URL = f"{DB_URL}/{FB_TICKETS}.json"
CNY_TO_AUD_RATE = 5.0


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def parse_amount(value):
    text = clean(value).replace(",", "")
    if not text or text == "#":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def price_to_aud(raw_price, raw_currency):
    num = parse_amount(raw_price)
    if not num and clean(raw_price) not in {"0", "0.0"}:
        return ""
    cur = clean(raw_currency).upper()
    if cur == "CNY":
        return round((num / CNY_TO_AUD_RATE) * 10000) / 10000
    return num


def parse_sales_order_details(value):
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(parse_sales_order_details(item))
        return out
    if not isinstance(value, dict):
        return []
    if any(k in value for k in ["Delivery Count", "Description", "Material", "Order Qty", "Sales Order Item"]):
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
            "deliveryCount": parse_amount(value.get("Delivery Count")),
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
    out = []
    for item in value.values():
        out.extend(parse_sales_order_details(item))
    return out


def preferred_parts_unit_price(item):
    au_po = price_to_aud(item.get("auPoPrice"), item.get("auPoCurrency"))
    if au_po != "":
        return au_po
    cn_so = price_to_aud(item.get("cnSoPrice"), item.get("cnSoCurrency"))
    if cn_so != "":
        return cn_so
    return ""


def preferred_parts_line_cost(item):
    unit = preferred_parts_unit_price(item)
    if unit == "":
        return ""
    qty = parse_amount(item.get("orderQty"))
    if qty <= 0:
        return unit
    return round(unit * qty * 10000) / 10000


def preferred_parts_cost(ticket):
    details = parse_sales_order_details(ticket.get("Sales Order Details"))
    total = 0.0
    hit = False
    for item in details:
        line = preferred_parts_line_cost(item)
        if line == "":
            continue
        total += float(line)
        hit = True
    if hit:
        return round(total * 10000) / 10000
    amount = parse_amount(ticket.get("AmountIncludingTax"))
    return round(amount * 10000) / 10000 if amount else ""


def ticket_values(node):
    if not node or not isinstance(node, (dict, list)):
        return []
    if isinstance(node, list):
        return [(str(index), value) for index, value in enumerate(node) if value is not None]
    return list(node.items())


def fetch_json(url):
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    data = fetch_json(FB_TICKETS_URL)
    rows = []
    for idx, entry in ticket_values(data):
        if not isinstance(entry, dict):
            continue
        ticket = entry.get("ticket")
        if not isinstance(ticket, dict):
            continue
        cost = preferred_parts_cost(ticket)
        rows.append({
            "ticketId": clean(ticket.get("TicketID") or ticket.get("TicketId") or ticket.get("id") or idx),
            "salesOrder": clean(ticket.get("Sales Order") or ticket.get("SalesOrder") or ticket.get("salesOrder") or ticket.get("LookupSalesOrder") or ticket.get("lookupSalesOrder")),
            "serialId": clean(ticket.get("SerialID") or ticket.get("Serial Id") or ticket.get("Serial ID")),
            "chassisNumber": clean(ticket.get("ChassisNumber") or ticket.get("Chassis Number")),
            "vehicleDispatchDate": clean(ticket.get("Vehicle Dispatch Date") or ticket.get("vehicleDispatchDate")),
            "vehicleDispatchSource": clean(ticket.get("Vehicle Dispatch Source") or ticket.get("vehicleDispatchSource")),
            "vehicleDispatchSerial": clean(ticket.get("Vehicle Dispatch Serial") or ticket.get("vehicleDispatchSerial")),
            "vehicleDispatchSalesOrder": clean(ticket.get("Vehicle Dispatch Sales Order") or ticket.get("vehicleDispatchSalesOrder")),
            "amountIncludingTax": parse_amount(ticket.get("AmountIncludingTax")),
            "preferredCost": cost if cost != "" else None,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "source": FB_TICKETS,
            "rowCount": len(data) if hasattr(data, "__len__") else None,
            "ticketRows": len(rows),
        },
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
