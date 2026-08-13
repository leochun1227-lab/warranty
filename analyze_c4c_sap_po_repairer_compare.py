from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_C4C_CSV = Path(r"C:\Users\Leo.Li\Downloads\SAPAnalyticsReport(ZF8C06456D7698BCB54F44D) (6).csv")
DEFAULT_SAP_XLSX = ROOT / "outputs" / "repairers_2026" / "sap_authoritative_repair_payments_with_cancelled.xlsx"
DEFAULT_OUT_JSON = ROOT / "outputs" / "repairers_2026" / "c4c_sap_po_repairer_compare.json"


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


def po_key(value: Any) -> str:
    text = clean(value)
    digits = re.sub(r"\D+", "", text)
    return digits if len(digits) >= 6 else ""


LEGAL_SUFFIXES = {
    "PTY", "LTD", "LIMITED", "PTYLTD", "P/L", "PL", "INC", "CO", "COMPANY", "THE",
    "REPAIR", "REPAIRS",
}


def normalize_repairer_name(value: Any) -> str:
    text = clean(value).upper()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.replace("&", " AND ")
    text = re.sub(r"\bCUSTOMER\s+AS\s+REPAIRER\b", " ", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = re.sub(r"\bPT\s+Y\b", " PTY ", text)
    tokens = [token for token in text.split() if token and token not in LEGAL_SUFFIXES]
    # Keep GREEN RV variants together without over-merging all Snowy branches.
    normalized = " ".join(tokens)
    normalized = normalized.replace("SNOWY RIVER RV", "SNOWY RIVER")
    normalized = normalized.replace("GREEN RV FOREST GLEN", "GREEN RV")
    normalized = normalized.replace("GREEN RV SLACKS CREEK", "GREEN RV")
    normalized = normalized.replace("GREEN RVS", "GREEN RV")
    return re.sub(r"\s+", " ", normalized).strip()


def name_match_status(c4c_names: set[str], sap_names: set[str]) -> tuple[str, str]:
    c4c_norms = {normalize_repairer_name(name) for name in c4c_names if normalize_repairer_name(name)}
    sap_norms = {normalize_repairer_name(name) for name in sap_names if normalize_repairer_name(name)}
    if not c4c_norms and not sap_norms:
        return "Both Missing", ""
    if not c4c_norms:
        return "Missing C4C Repairer", ""
    if not sap_norms:
        return "Missing SAP Repairer", ""
    exact = sorted(c4c_norms & sap_norms)
    if exact:
        return "Matched", "; ".join(exact)
    partial: list[str] = []
    for c4c in c4c_norms:
        for sap in sap_norms:
            if c4c and sap and (c4c in sap or sap in c4c):
                partial.append(f"{c4c} ~ {sap}")
                continue
            c4c_tokens = set(c4c.split())
            sap_tokens = set(sap.split())
            if c4c_tokens and sap_tokens:
                overlap = c4c_tokens & sap_tokens
                min_len = min(len(c4c_tokens), len(sap_tokens))
                if len(overlap) >= 2 and len(overlap) / max(min_len, 1) >= 0.67:
                    partial.append(f"{c4c} ~ {sap}")
    if partial:
        return "Likely Match", "; ".join(sorted(set(partial)))
    return "Mismatch", ""


def unique_join(values: list[Any] | set[Any], limit: int = 50) -> str:
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


def read_c4c_rows(path: Path) -> dict[str, dict[str, Any]]:
    po_map: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            po = po_key(row.get("ERP Purchase Order ID"))
            if not po:
                continue
            rec = po_map.setdefault(po, {
                "PO": po,
                "c4c_rows": 0,
                "c4c_tickets": set(),
                "c4c_ticket_ids": set(),
                "c4c_service_technicians": set(),
                "c4c_statuses": set(),
                "c4c_created_dates": set(),
                "c4c_posting_dates": set(),
                "c4c_claim_amount": 0.0,
            })
            rec["c4c_rows"] += 1
            rec["c4c_tickets"].add(clean(row.get("Ticket")))
            rec["c4c_ticket_ids"].add(clean(row.get("Ticket ID")))
            rec["c4c_service_technicians"].add(clean(row.get("Service Technician")))
            rec["c4c_statuses"].add(clean(row.get("Status")))
            rec["c4c_created_dates"].add(clean(row.get("Created On")))
            rec["c4c_posting_dates"].add(clean(row.get("Posting Date")))
            rec["c4c_claim_amount"] += amount(row.get("ClaimTotalAmount"))
    return po_map


def read_sap_rows(path: Path) -> tuple[dict[str, dict[str, Any]], int, int]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["SAP PO Lines"]
    rows = ws.iter_rows(values_only=True)
    headers = [clean(value) for value in next(rows)]
    sap_map: dict[str, dict[str, Any]] = {}
    total_rows = 0
    cancelled_rows = 0
    for values in rows:
        row = {headers[index]: values[index] if index < len(values) else "" for index in range(len(headers))}
        total_rows += 1
        if clean(row.get("SAP PO Cancelled")).upper() == "YES":
            cancelled_rows += 1
            continue
        po = po_key(row.get("SAP PO"))
        if not po:
            continue
        rec = sap_map.setdefault(po, {
            "PO": po,
            "sap_active_rows": 0,
            "sap_ticket_ids": set(),
            "sap_repairers": set(),
            "sap_vendor_ids": set(),
            "sap_po_dates": set(),
            "sap_invoice_statuses": set(),
            "sap_invoice_docs": set(),
            "sap_po_net_value": 0.0,
            "sap_active_po_net_value": 0.0,
        })
        rec["sap_active_rows"] += 1
        rec["sap_ticket_ids"].add(clean(row.get("SAP Ticket ID")))
        rec["sap_repairers"].add(clean(row.get("SAP Repairer Name")))
        rec["sap_vendor_ids"].add(clean(row.get("SAP Repairer Vendor ID")))
        rec["sap_po_dates"].add(clean(row.get("SAP PO Date")))
        rec["sap_invoice_statuses"].add(clean(row.get("SAP Invoice Status")))
        rec["sap_invoice_docs"].add(clean(row.get("SAP Last Invoice Doc")))
        rec["sap_po_net_value"] += amount(row.get("SAP PO Net Value"))
        rec["sap_active_po_net_value"] += amount(row.get("SAP Active PO Net Value") or row.get("SAP PO Net Value"))
    wb.close()
    return sap_map, total_rows, cancelled_rows


def flatten_c4c(rec: dict[str, Any] | None) -> dict[str, Any]:
    if not rec:
        return {
            "C4C Rows": 0,
            "C4C Tickets": "",
            "C4C Ticket IDs": "",
            "C4C Service Technician": "",
            "C4C Normalized Repairer": "",
            "C4C Status": "",
            "C4C Claim Amount": 0.0,
            "C4C Created On": "",
            "C4C Posting Date": "",
        }
    return {
        "C4C Rows": rec["c4c_rows"],
        "C4C Tickets": unique_join(rec["c4c_tickets"]),
        "C4C Ticket IDs": unique_join(rec["c4c_ticket_ids"]),
        "C4C Service Technician": unique_join(rec["c4c_service_technicians"]),
        "C4C Normalized Repairer": unique_join({normalize_repairer_name(v) for v in rec["c4c_service_technicians"] if normalize_repairer_name(v)}),
        "C4C Status": unique_join(rec["c4c_statuses"]),
        "C4C Claim Amount": round(rec["c4c_claim_amount"], 2),
        "C4C Created On": unique_join(rec["c4c_created_dates"]),
        "C4C Posting Date": unique_join(rec["c4c_posting_dates"]),
    }


def flatten_sap(rec: dict[str, Any] | None) -> dict[str, Any]:
    if not rec:
        return {
            "SAP Active Rows": 0,
            "SAP Ticket IDs": "",
            "SAP Repairer Name": "",
            "SAP Normalized Repairer": "",
            "SAP Vendor IDs": "",
            "SAP PO Date": "",
            "SAP Invoice Status": "",
            "SAP Invoice Docs": "",
            "SAP PO Net Value": 0.0,
            "SAP Active PO Net Value": 0.0,
        }
    return {
        "SAP Active Rows": rec["sap_active_rows"],
        "SAP Ticket IDs": unique_join(rec["sap_ticket_ids"]),
        "SAP Repairer Name": unique_join(rec["sap_repairers"]),
        "SAP Normalized Repairer": unique_join({normalize_repairer_name(v) for v in rec["sap_repairers"] if normalize_repairer_name(v)}),
        "SAP Vendor IDs": unique_join(rec["sap_vendor_ids"]),
        "SAP PO Date": unique_join(rec["sap_po_dates"]),
        "SAP Invoice Status": unique_join(rec["sap_invoice_statuses"]),
        "SAP Invoice Docs": unique_join(rec["sap_invoice_docs"]),
        "SAP PO Net Value": round(rec["sap_po_net_value"], 2),
        "SAP Active PO Net Value": round(rec["sap_active_po_net_value"], 2),
    }


def build_compare(c4c_map: dict[str, dict[str, Any]], sap_map: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    common_pos = sorted(set(c4c_map) & set(sap_map))
    sap_only_pos = sorted(set(sap_map) - set(c4c_map))
    c4c_only_pos = sorted(set(c4c_map) - set(sap_map))

    common_rows: list[dict[str, Any]] = []
    for po in common_pos:
        c4c = c4c_map[po]
        sap = sap_map[po]
        status, evidence = name_match_status(c4c["c4c_service_technicians"], sap["sap_repairers"])
        common_rows.append({
            "PO": po,
            "Repairer Match Status": status,
            "Match Evidence": evidence,
            **flatten_c4c(c4c),
            **flatten_sap(sap),
        })

    sap_only = [{"PO": po, **flatten_sap(sap_map[po])} for po in sap_only_pos]
    c4c_only = [{"PO": po, **flatten_c4c(c4c_map[po])} for po in c4c_only_pos]
    return common_rows, sap_only, c4c_only


def build_summary(c4c_map, sap_map, common_rows, sap_only, c4c_only, sap_total_rows, sap_cancelled_rows):
    status_counts = Counter(row["Repairer Match Status"] for row in common_rows)
    return [
        {"Metric": "C4C unique nonblank PO", "Value": len(c4c_map)},
        {"Metric": "SAP active unique PO", "Value": len(sap_map)},
        {"Metric": "PO in both C4C and SAP", "Value": len(common_rows)},
        {"Metric": "SAP active PO not in C4C", "Value": len(sap_only)},
        {"Metric": "C4C PO not in SAP active", "Value": len(c4c_only)},
        {"Metric": "Common PO repairer matched", "Value": status_counts.get("Matched", 0)},
        {"Metric": "Common PO repairer likely matched", "Value": status_counts.get("Likely Match", 0)},
        {"Metric": "Common PO repairer mismatch", "Value": status_counts.get("Mismatch", 0)},
        {"Metric": "Common PO missing C4C repairer", "Value": status_counts.get("Missing C4C Repairer", 0)},
        {"Metric": "Common PO missing SAP repairer", "Value": status_counts.get("Missing SAP Repairer", 0)},
        {"Metric": "SAP PO lines read", "Value": sap_total_rows},
        {"Metric": "SAP cancelled PO lines excluded", "Value": sap_cancelled_rows},
        {"Metric": "Rule", "Value": "Compare C4C ERP Purchase Order ID + Service Technician against SAP active PO + SAP Repairer Name. SAP cancelled PO lines are excluded."},
    ]


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare C4C ERP Purchase Order ID / Service Technician against SAP active PO / repairer.")
    parser.add_argument("--c4c-csv", default=str(DEFAULT_C4C_CSV))
    parser.add_argument("--sap-xlsx", default=str(DEFAULT_SAP_XLSX))
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    args = parser.parse_args()

    c4c_map = read_c4c_rows(Path(args.c4c_csv))
    sap_map, sap_total_rows, sap_cancelled_rows = read_sap_rows(Path(args.sap_xlsx))
    common_rows, sap_only, c4c_only = build_compare(c4c_map, sap_map)
    mismatches = [row for row in common_rows if row["Repairer Match Status"] not in {"Matched", "Likely Match"}]
    payload = {
        "summary": build_summary(c4c_map, sap_map, common_rows, sap_only, c4c_only, sap_total_rows, sap_cancelled_rows),
        "common_po_compare": common_rows,
        "repairer_mismatch": mismatches,
        "sap_only_active_po": sap_only,
        "c4c_only_po": c4c_only,
        "source": {
            "c4c_csv": str(Path(args.c4c_csv)),
            "sap_xlsx": str(Path(args.sap_xlsx)),
            "sap_cancelled_rule": "Exclude rows where SAP PO Cancelled = Yes",
        },
    }
    write_payload(Path(args.out_json), payload)
    print(json.dumps({
        "out_json": str(Path(args.out_json)),
        "common_po": len(common_rows),
        "repairer_mismatch_or_missing": len(mismatches),
        "sap_only_active_po": len(sap_only),
        "c4c_only_po": len(c4c_only),
        "sap_cancelled_lines_excluded": sap_cancelled_rows,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
