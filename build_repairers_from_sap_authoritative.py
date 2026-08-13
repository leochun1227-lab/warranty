from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "outputs" / "repairers_2026" / "sap_authoritative_repair_payments.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "repairers_2026"


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"#", "None", "nan", "NaN"} else text


def amount(value: Any) -> float:
    try:
        return round(float(clean(value).replace(",", "")), 2)
    except Exception:
        return 0.0


def unique_join(values: list[Any]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean(value)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return ", ".join(out)


def normalize_name(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", clean(value).upper())).strip()


def is_snowy_repair_source(*values: Any) -> bool:
    text = " ".join(normalize_name(value) for value in values if clean(value))
    compact = text.replace(" ", "")
    return "SNOWYRIVER" in compact or "SNOWY RIVER" in text


def state_from_sap(row: dict[str, Any]) -> str:
    region = clean(row.get("SAP Repairer Region")).upper()
    country = clean(row.get("SAP Repairer Country")).upper()
    if region in {"QLD", "NSW", "VIC", "WA", "SA", "TAS", "ACT", "NT"}:
        return region
    if country in {"NZ", "NEW ZEALAND"}:
        return "NZ"
    if country in {"AU", "AUS", "AUSTRALIA"} and region:
        return region
    return region or country or "Unknown"


def week_start_key(value: Any) -> str:
    text = clean(value)
    try:
        dt = datetime.fromisoformat(text[:10])
    except Exception:
        return ""
    start = dt - timedelta(days=dt.weekday())
    return start.date().isoformat()


def week_end_key(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value[:10])
    except Exception:
        return ""
    return (dt + timedelta(days=6)).date().isoformat()


def week_label(value: str) -> str:
    end = week_end_key(value)
    return f"{value} to {end}" if value and end else value


def make_detail_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    summary_rows = payload.get("summary") if isinstance(payload.get("summary"), list) else []
    line_rows = payload.get("po_lines") if isinstance(payload.get("po_lines"), list) else payload.get("invoice_lines") if isinstance(payload.get("invoice_lines"), list) else []
    lines_by_ticket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in line_rows:
        ticket_id = clean(line.get("SAP Ticket ID"))
        if ticket_id:
            lines_by_ticket[ticket_id].append(line)

    details: list[dict[str, Any]] = []
    for row in summary_rows:
        ticket_id = clean(row.get("SAP Ticket ID"))
        if not ticket_id:
            continue
        lines = lines_by_ticket.get(ticket_id, [])
        active_lines = [line for line in lines if clean(line.get("SAP PO Cancelled")) != "Yes"]
        cancelled_lines = [line for line in lines if clean(line.get("SAP PO Cancelled")) == "Yes"]
        main_lines = active_lines or lines
        first_line = main_lines[0] if main_lines else (lines[0] if lines else {})
        state = state_from_sap(first_line)
        repairer_base = unique_join([line.get("SAP Repairer Name") for line in main_lines]) or clean(row.get("SAP Repairer Name")) or "Unknown SAP Repairer"
        vendor_id = unique_join([line.get("SAP Repairer Vendor ID") for line in main_lines]) or clean(row.get("SAP Repairer Vendor ID"))
        repairer_name = f"{repairer_base} ({state})" if state and state != "Unknown" else repairer_base
        split_key = f"{normalize_name(repairer_base) or 'UNKNOWN'}|{state}"
        invoice_docs = unique_join([line.get("SAP Last Invoice Doc") for line in main_lines]) or clean(row.get("SAP Invoice Docs"))
        pos = unique_join([line.get("SAP PO") for line in main_lines]) or clean(row.get("SAP POs"))
        cancelled_pos = unique_join([line.get("SAP PO") for line in cancelled_lines])
        po_dates = sorted(clean(line.get("SAP PO Date")) for line in main_lines if clean(line.get("SAP PO Date")))
        invoice_dates = sorted(clean(line.get("SAP Last Invoice Date")) for line in main_lines if clean(line.get("SAP Last Invoice Date")))
        last_date = (po_dates[-1] if po_dates else clean(row.get("SAP Last PO Date") or row.get("SAP Last Invoice Date")))
        first_date = (po_dates[0] if po_dates else clean(row.get("SAP First PO Date") or row.get("SAP First Invoice Date"))) or last_date
        invoice_date = invoice_dates[-1] if invoice_dates else clean(row.get("SAP Last Invoice Date"))
        cost = round(sum(amount(line.get("SAP Active PO Net Value")) for line in lines), 2) or amount(row.get("SAP PO Net Value") or row.get("SAP Signed Invoice Amount"))
        invoice_amount = round(sum(amount(line.get("SAP Signed Invoice Amount Doc")) for line in main_lines), 2) or amount(row.get("SAP Signed Invoice Amount"))
        if cancelled_lines and active_lines:
            cancelled_status = "Mixed"
        elif cancelled_lines:
            cancelled_status = "Yes"
        else:
            cancelled_status = "No"
        c4c_cost = amount(row.get("C4C Repairer Parts Claim Total Amount") or row.get("C4C Claim Total Amount") or row.get("C4C Repair Detail Approved Cost"))
        c4c_ticket_title = clean(row.get("C4C Ticket ID") or row.get("C4C Repair Detail Ticket ID"))
        c4c_po = clean(row.get("C4C PO") or row.get("C4C Repair Detail PO"))
        c4c_repairer = clean(row.get("C4C Service Technician") or row.get("C4C Repair Detail Repairer"))
        c4c_status = clean(row.get("C4C Status"))
        c4c_ticket = clean(row.get("C4C Ticket"))
        c4c_approved_on = clean(row.get("C4C Claim Approved On"))
        short_text = clean(first_line.get("SAP Short Text"))
        chassis = clean(first_line.get("C4C Chassis Number") or first_line.get("Chassis Number"))

        details.append({
            "Ticket": c4c_ticket or short_text or f"Ticket No: {ticket_id}",
            "Ticket ID": c4c_ticket_title or ticket_id,
            "TicketID": c4c_ticket_title or ticket_id,
            "C4C Ticket ID": ticket_id,
            "Created On": clean(row.get("C4C Created On")) or first_date,
            "Posting Date": last_date,
            "Changed On": clean(row.get("C4C Posting Date")) or last_date,
            "Approved Date": last_date,
            "approved_date": last_date,
            "C4C Claim Approved On": c4c_approved_on,
            "c4c_claim_approved_on": c4c_approved_on,
            "Ticket Type": "SAP Repair Payment",
            "Status": c4c_status or clean(row.get("SAP Invoice Status")) or "SAP PO Created",
            "Service Technician": repairer_base,
            "raw_repairer_name": repairer_base,
            "canonical_repairer_name": repairer_base,
            "repairer_name": repairer_name,
            "repairer_base_name": repairer_base,
            "normalized_key": normalize_name(repairer_base),
            "repairer_split_key": split_key,
            "repairshop_id": vendor_id,
            "RepairerBusinessNameID": vendor_id,
            "state": state,
            "state_source": "sap_vendor_region",
            "Country/Region": clean(row.get("C4C Country/Region")) or clean(first_line.get("SAP Repairer Country")),
            "Dealer": vendor_id,
            "Dealer Name": repairer_base,
            "Service Requester Postal Code": "",
            "Serial ID": chassis,
            "Chassis Number": chassis,
            "Registered Product": chassis,
            "Product": chassis,
            "ERP Purchase Order ID": pos,
            "Sales Order": "",
            "invoice_status": "invoiced" if clean(row.get("SAP Last Invoice Date")) else "po_created",
            "invoice_number": invoice_docs,
            "invoice_date": invoice_date,
            "current_claim_amount_aud": c4c_cost,
            "sap_po_cost_native": cost,
            "sap_po_cost_currency": clean(row.get("SAP Currency")),
            "sap_po_cost_aud": cost,
            "confirmed_cost_source": "sap_ekpo_netwr_short_text_ticket",
            "confirmed_cost_native": cost,
            "confirmed_cost_currency": clean(row.get("SAP Currency")),
            "confirmed_cost_aud": cost,
            "pending_amount_aud": 0,
            "ClaimTotalAmount": c4c_cost or cost,
            "Factory Parts Claim Total Amount": 0,
            "LabourHoursTotalAmount": 0,
            "Repairer Parts Claim Total Amount": c4c_cost or cost,
            "is_snowy_river": is_snowy_repair_source(c4c_repairer, repairer_base),
            "sap_authoritative": True,
            "sap_invoice_line_count": int(amount(row.get("SAP Line Count"))),
            "sap_invoice_row_count": int(amount(row.get("SAP Invoice Row Count"))),
            "sap_invoice_docs": invoice_docs,
            "sap_last_invoice_date": invoice_date,
            "sap_pos": pos,
            "sap_cancelled_pos": cancelled_pos,
            "sap_repairer_vendor_id": vendor_id,
            "sap_repairer_name": repairer_base,
            "sap_po_net_value": cost,
            "sap_cancelled_po_net_value": amount(row.get("SAP Cancelled PO Net Value")),
            "sap_po_cancelled": cancelled_status,
            "c4c_eligible_for_repairer_analysis": clean(row.get("C4C Eligible For Repairer Analysis")) or "Yes",
            "c4c_eligibility_match_source": clean(row.get("C4C Eligibility Match Source")),
            "c4c_eligibility_reason": clean(row.get("C4C Eligibility Reason")),
            "c4c_status": c4c_status,
            "c4c_service_technician": c4c_repairer,
            "sap_raw_invoice_amount": invoice_amount,
            "sap_signed_invoice_amount": invoice_amount,
            "sap_currency": clean(row.get("SAP Currency")),
            "c4c_compare_repairer": c4c_repairer,
            "c4c_compare_po": c4c_po,
            "c4c_compare_approved_cost": c4c_cost,
            "week_start": week_start_key(last_date),
        })
    return details


def build_payload(source_payload: dict[str, Any], details: list[dict[str, Any]]) -> dict[str, Any]:
    repairer_groups: dict[str, dict[str, Any]] = {}
    state_groups: dict[str, dict[str, Any]] = {}
    weekly_groups: dict[str, dict[str, Any]] = {}

    for row in details:
        cost = amount(row.get("confirmed_cost_aud"))
        if cost <= 0:
            continue
        split_key = clean(row.get("repairer_split_key")) or "Unknown|Unknown"
        state = clean(row.get("state")) or "Unknown"
        repairer = repairer_groups.setdefault(split_key, {
            "split_key": split_key,
            "repairer_name": clean(row.get("repairer_name")),
            "repairer_base_name": clean(row.get("repairer_base_name")),
            "normalized_key": clean(row.get("normalized_key")),
            "repairer_business_name_id": clean(row.get("RepairerBusinessNameID")),
            "state": state,
            "ticket_count": 0,
            "invoiced_tickets": 0,
            "open_tickets": 0,
            "confirmed_cost": 0.0,
            "pending_amount": 0.0,
            "unique_address_groups": 1,
            "raw_name_variants": 1,
            "raw_name_variants_text": clean(row.get("raw_repairer_name")),
            "top_address_group": clean(row.get("Dealer Name")),
            "top_dealer_name": clean(row.get("Dealer Name")),
            "first_created_on": clean(row.get("approved_date")),
            "last_created_on": clean(row.get("approved_date")),
        })
        repairer["ticket_count"] += 1
        repairer["invoiced_tickets"] += 1
        repairer["confirmed_cost"] += cost
        date = clean(row.get("approved_date"))
        if date:
            repairer["first_created_on"] = min(clean(repairer["first_created_on"]) or date, date)
            repairer["last_created_on"] = max(clean(repairer["last_created_on"]) or date, date)

        state_group = state_groups.setdefault(state, {
            "state": state,
            "ticket_count": 0,
            "invoiced_tickets": 0,
            "open_tickets": 0,
            "confirmed_cost": 0.0,
            "pending_amount": 0.0,
            "repairers": set(),
            "snowy_ticket_count": 0,
            "snowy_confirmed_cost": 0.0,
            "snowy_repairers": set(),
            "top_dealer_name": "",
        })
        state_group["ticket_count"] += 1
        state_group["invoiced_tickets"] += 1
        state_group["confirmed_cost"] += cost
        state_group["repairers"].add(split_key)
        state_group["top_dealer_name"] = state_group["top_dealer_name"] or clean(row.get("Dealer Name"))
        if str(row.get("is_snowy_river")).lower() == "true":
            state_group["snowy_ticket_count"] += 1
            state_group["snowy_confirmed_cost"] += cost
            state_group["snowy_repairers"].add(split_key)

        week = clean(row.get("week_start"))
        if week:
            weekly = weekly_groups.setdefault(week, {
                "week_start": week,
                "week_end": week_end_key(week),
                "label": week_label(week),
                "ticket_count": 0,
                "invoiced_tickets": 0,
                "open_tickets": 0,
                "confirmed_cost": 0.0,
                "pending_amount": 0.0,
                "_states": defaultdict(lambda: {"ticket_count": 0, "confirmed_cost": 0.0, "repairers": set()}),
                "_repairers": defaultdict(lambda: {"ticket_count": 0, "confirmed_cost": 0.0}),
            })
            weekly["ticket_count"] += 1
            weekly["invoiced_tickets"] += 1
            weekly["confirmed_cost"] += cost
            weekly["_states"][state]["ticket_count"] += 1
            weekly["_states"][state]["confirmed_cost"] += cost
            weekly["_states"][state]["repairers"].add(split_key)
            weekly["_repairers"][split_key]["ticket_count"] += 1
            weekly["_repairers"][split_key]["confirmed_cost"] += cost

    repairer_rows = []
    for row in repairer_groups.values():
        row["confirmed_cost"] = round(row["confirmed_cost"], 2)
        row["avg_warranty_cost"] = round(row["confirmed_cost"] / row["ticket_count"], 2) if row["ticket_count"] else 0
        row["avg_confirmed_cost"] = row["avg_warranty_cost"]
        repairer_rows.append(row)
    repairer_rows.sort(key=lambda r: (-r["confirmed_cost"], -r["ticket_count"], r["repairer_name"]))

    state_rows = []
    for row in state_groups.values():
        repairers = row.pop("repairers")
        snowy_repairers = row.pop("snowy_repairers")
        row["confirmed_cost"] = round(row["confirmed_cost"], 2)
        row["avg_warranty_cost"] = round(row["confirmed_cost"] / row["ticket_count"], 2) if row["ticket_count"] else 0
        row["avg_confirmed_cost"] = row["avg_warranty_cost"]
        row["unique_repairers"] = len(repairers)
        row["snowy_confirmed_cost"] = round(row["snowy_confirmed_cost"], 2)
        row["snowy_unique_repairers"] = len(snowy_repairers)
        row["snowy_avg_confirmed_cost"] = round(row["snowy_confirmed_cost"] / row["snowy_ticket_count"], 2) if row["snowy_ticket_count"] else 0
        state_rows.append(row)
    state_rows.sort(key=lambda r: (-r["confirmed_cost"], -r["ticket_count"], r["state"]))

    repairer_name_by_key = {row["split_key"]: row["repairer_name"] for row in repairer_rows}
    weekly_rows = []
    for week, row in sorted(weekly_groups.items()):
        states = []
        for state, item in row.pop("_states").items():
            states.append({
                "state": state,
                "ticket_count": item["ticket_count"],
                "confirmed_cost": round(item["confirmed_cost"], 2),
                "pending_amount": 0.0,
                "avg_confirmed_cost": round(item["confirmed_cost"] / item["ticket_count"], 2) if item["ticket_count"] else 0,
                "unique_repairers": len(item["repairers"]),
            })
        top_repairers = []
        for split_key, item in row.pop("_repairers").items():
            top_repairers.append({
                "split_key": split_key,
                "repairer_name": repairer_name_by_key.get(split_key, split_key),
                "ticket_count": item["ticket_count"],
                "confirmed_cost": round(item["confirmed_cost"], 2),
                "avg_confirmed_cost": round(item["confirmed_cost"] / item["ticket_count"], 2) if item["ticket_count"] else 0,
            })
        row["confirmed_cost"] = round(row["confirmed_cost"], 2)
        row["states"] = sorted(states, key=lambda s: -s["confirmed_cost"])
        row["top_repairers"] = sorted(top_repairers, key=lambda r: -r["confirmed_cost"])[:20]
        weekly_rows.append(row)

    positive_details = [row for row in details if amount(row.get("confirmed_cost_aud")) > 0]
    total_cost = round(sum(amount(row.get("confirmed_cost_aud")) for row in positive_details), 2)
    meta_source = source_payload.get("meta") if isinstance(source_payload.get("meta"), list) else []
    meta = {
        "source": "sap_authoritative_repair_payments.json",
        "source_of_truth": "C4C-approved eligibility + SAP EKPO/EKKO PO rows + EKPO short text ticket ID",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sap_meta": meta_source,
        "total_sap_ticket_ids": len(details),
        "positive_cost_ticket_ids": len(positive_details),
        "short_text_unreadable_lines": len(source_payload.get("short_text_unreadable") or []),
    }
    summary = {
        "total_tickets": len(positive_details),
        "invoiced_tickets": len(positive_details),
        "open_tickets": 0,
        "confirmed_cost": total_cost,
        "total_warranty_cost": total_cost,
        "pending_amount": 0.0,
        "avg_confirmed_cost": round(total_cost / len(positive_details), 2) if positive_details else 0,
        "avg_warranty_cost": round(total_cost / len(positive_details), 2) if positive_details else 0,
        "unique_repairers": len(repairer_rows),
        "unique_repairers_raw": len(repairer_rows),
        "unique_repairers_normalized": len(repairer_rows),
        "unique_states": len(state_rows),
        "unique_addresses": len(repairer_rows),
        "unique_weeks": len(weekly_rows),
        "po_cost_sanity_overrides": 0,
        "top_repairers": repairer_rows[:20],
    }
    variants = [{
        "raw_repairer_name": clean(row.get("raw_repairer_name")),
        "normalized_key": clean(row.get("normalized_key")),
        "state": clean(row.get("state")),
        "state_source": clean(row.get("state_source")),
        "address_group": clean(row.get("Dealer Name")),
        "dealer_name": clean(row.get("Dealer Name")),
        "dealer_code": clean(row.get("Dealer")),
        "country_region": clean(row.get("Country/Region")),
        "postal_code": "",
        "ticket_id": clean(row.get("Ticket ID")),
        "c4c_ticket_id": clean(row.get("C4C Ticket ID")),
        "created_on": clean(row.get("Created On")),
        "status": clean(row.get("Status")),
        "claim_total_amount": amount(row.get("ClaimTotalAmount")),
    } for row in details]
    addresses = [{
        "address_group": row["repairer_name"],
        "ticket_count": row["ticket_count"],
        "total_warranty_cost": row["confirmed_cost"],
        "avg_warranty_cost": row["avg_warranty_cost"],
        "unique_repairers": 1,
        "top_state": row["state"],
        "top_repairer": row["repairer_name"],
        "top_dealer_name": row["repairer_base_name"],
    } for row in repairer_rows]

    return {
        "meta": meta,
        "summary": summary,
        "repairers": repairer_rows,
        "addresses": addresses,
        "states": state_rows,
        "weekly": weekly_rows,
        "variants": variants,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert SAP authoritative repair payments into repairer dashboard cache input.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    source = Path(args.source)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_payload = json.loads(source.read_text(encoding="utf-8"))
    details = make_detail_rows(source_payload)
    payload = build_payload(source_payload, details)
    data_path = output_dir / "repairers_2026_data.json"
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    js_path = output_dir / "repairers_2026_data.js"
    js_path.write_text("globalThis.REPAIRERS_2026_ANALYSIS = " + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(json.dumps({
        "data": str(data_path),
        "details": len(payload["details"]),
        "positive_cost_tickets": payload["summary"]["total_tickets"],
        "confirmed_cost": payload["summary"]["confirmed_cost"],
        "repairers": len(payload["repairers"]),
        "states": len(payload["states"]),
        "short_text_unreadable_lines": payload["meta"]["short_text_unreadable_lines"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
