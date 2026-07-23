import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"
PARTS_META = OUTPUT_DIR / "parts_classified_meta.json"
PARTS_CSV = OUTPUT_DIR / "parts_classified.csv"
PARTS_TICKET_MAP = ROOT / "outputs" / "analysis_parts_ticket_cost_map.json"
VEHICLE_BASE_SUMMARY = OUTPUT_DIR / "analysis_vehicle_base_summary.json"
OUT = ROOT / "outputs" / "analysis_parts_failure_summary.json"
OUT_JS = ROOT / "outputs" / "analysis_parts_failure_summary.js"

SERIES_ORDER = ["SRC", "SRH", "SRT", "SRM", "SRP", "SRL", "SRV", "SRS", "NG"]
TRACKED_SERIES = {code.upper() for code in SERIES_ORDER}
EXCLUDED_SERIES = {"UNKNOWN", "RO", "SR", "SCR", "STR", "RVV", "RR", "SPV", "SRO", "SEV", "RRC", "VRV"}
COMPONENT_ALIASES = {
    "tail light": "Tail Light",
    "tail lights": "Tail Light",
    "taillight": "Tail Light",
    "taillights": "Tail Light",
    "combination taillight": "Tail Light",
    "combination taillights": "Tail Light",
    "marker light": "Marker Light",
    "marker lights": "Marker Light",
    "stop light": "Stop Light",
    "stop lights": "Stop Light",
    "roof hatch": "Roof Hatch",
    "roof hatches": "Roof Hatch",
    "window blind": "Window Blind",
    "window blinds": "Window Blind",
    "access door": "Access Door",
    "access doors": "Access Door",
    "main door": "Main Door",
    "main doors": "Main Door",
    "power inlet": "Power Inlet",
    "power inlets": "Power Inlet",
    "power outlet": "Power Outlet",
    "power outlets": "Power Outlet",
}


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


def title_case(text):
    value = clean(text)
    if not value:
        return "Other"
    parts = re.split(r"(\s+)", value)
    out = []
    for part in parts:
        if not part or part.isspace():
            out.append(part)
            continue
        if part.isupper() or part.isdigit():
            out.append(part)
        else:
            out.append(part[:1].upper() + part[1:].lower())
    return "".join(out)


def normalize_component_label(component, category=""):
    value = clean(component)
    if not value:
        return title_case(category or "Other")

    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    if normalized in COMPONENT_ALIASES:
        return COMPONENT_ALIASES[normalized]
    if normalized.endswith("s") and normalized[:-1] in COMPONENT_ALIASES:
        return COMPONENT_ALIASES[normalized[:-1]]

    if "Lighting / Reflectors" in clean(category):
        if re.search(r"\b(?:combination\s+)?tail\s*lights?\b", normalized) or re.search(r"\btaillights?\b", normalized):
            return "Tail Light"
        if re.search(r"\bmarker\s+lights?\b", normalized):
            return "Marker Light"
        if re.search(r"\bstop\s+lights?\b", normalized):
            return "Stop Light"

    return title_case(value)


def relative_path(path):
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_parts_sources() -> Tuple[Optional[Path], Path]:
    candidates = [PARTS_META, *sorted(OUTPUT_DIR.glob("parts_classification_*/parts_classified_meta.json"), reverse=True)]
    seen_csv = set()

    for meta_path in candidates:
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        csv_path_raw = clean((meta or {}).get("csvPath"))
        if not csv_path_raw:
            continue
        csv_path = Path(csv_path_raw)
        if not csv_path.is_absolute():
            csv_path = (ROOT / csv_path).resolve()
        if csv_path.exists():
            return meta_path, csv_path
        seen_csv.add(csv_path.resolve())

    csv_candidates = [PARTS_CSV, *sorted(OUTPUT_DIR.glob("parts_classification_*/parts_classified.csv"), reverse=True)]
    for csv_path in csv_candidates:
        resolved = csv_path.resolve()
        if resolved in seen_csv:
            continue
        if csv_path.exists():
            return None, csv_path

    raise FileNotFoundError("No parts classified CSV could be resolved.")


def normalize_series_code(code):
    raw = clean(code).upper()
    if not raw:
        return "UNKNOWN"
    if raw.startswith("NG"):
        return "NG"
    if raw == "RV" or raw == "RRV" or raw.startswith("RRV"):
        return "SRL"
    if raw == "LRV" or raw.startswith("LRV"):
        return "SRC"
    if raw.startswith("L"):
        return f"S{raw[1:]}"
    return raw


def is_excluded_series(series):
    return normalize_series_code(series) in EXCLUDED_SERIES


def is_tracked_series(series):
    return normalize_series_code(series) in TRACKED_SERIES


def vehicle_lookup_key(value):
    return re.sub(r"[^A-Za-z0-9]", "", clean(value)).upper()


def lookup_keys(values):
    out = []
    seen = set()
    for value in values:
        raw = clean(value)
        if not raw:
            continue
        canonical = vehicle_lookup_key(raw)
        for key in (raw, canonical):
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out


def vehicle_series_lookup_keys(row):
    return lookup_keys([
        row.get("Matched Chassis"),
        row.get("matchedChassis"),
        row.get("Matched Serial"),
        row.get("matchedSerial"),
        row.get("Chassis Number"),
        row.get("ChassisNumber"),
        row.get("chassisNumber"),
        row.get("Ticket Chassis Number"),
        row.get("ticketChassisNumber"),
        row.get("Ticket Serial ID"),
        row.get("ticketSerialId"),
        row.get("Serial ID"),
        row.get("SerialID"),
        row.get("serialId"),
        row.get("Vehicle Dispatch Serial"),
        row.get("VehicleDispatchSerial"),
        row.get("vehicleDispatchSerial"),
    ])


def sales_order_lookup_keys(row):
    return lookup_keys([
        row.get("Matched Sales Order"),
        row.get("MatchedSalesOrder"),
        row.get("matchedSalesOrder"),
        row.get("Sales Order"),
        row.get("SalesOrder"),
        row.get("salesOrder"),
        row.get("Ticket Sales Order"),
        row.get("ticketSalesOrder"),
        row.get("LookupSalesOrder"),
        row.get("lookupSalesOrder"),
        row.get("Vehicle Dispatch Sales Order"),
        row.get("VehicleDispatchSalesOrder"),
        row.get("vehicleDispatchSalesOrder"),
    ])


def load_vehicle_base_maps():
    if not VEHICLE_BASE_SUMMARY.exists():
        return {}, {}
    try:
        payload = json.loads(VEHICLE_BASE_SUMMARY.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    chassis = payload.get("seriesByChassis") if isinstance(payload, dict) else {}
    sales_order = payload.get("seriesBySalesOrder") if isinstance(payload, dict) else {}
    return (
        chassis if isinstance(chassis, dict) else {},
        sales_order if isinstance(sales_order, dict) else {},
    )


def mapped_series_for_row(row, series_by_chassis=None, series_by_sales_order=None):
    series_by_chassis = series_by_chassis or {}
    series_by_sales_order = series_by_sales_order or {}
    for key in vehicle_series_lookup_keys(row):
        if key in series_by_chassis:
            return normalize_series_code(series_by_chassis[key])
    for key in sales_order_lookup_keys(row):
        if key in series_by_sales_order:
            return normalize_series_code(series_by_sales_order[key])
    return ""


def extract_series(row, series_by_chassis=None, series_by_sales_order=None):
    mapped = mapped_series_for_row(row, series_by_chassis, series_by_sales_order)
    if mapped and not is_excluded_series(mapped):
        return mapped
    parts = [
        row.get("Registered Product"),
        row.get("Product"),
        row.get("Ticket ID"),
        row.get("Ticket"),
        row.get("Serial ID"),
        row.get("Chassis Number"),
    ]
    text = " ".join(clean(v) for v in parts if clean(v) and clean(v) != "#").upper()
    if re.search(r"\bNG[A-Z0-9-]*", text):
        return "NG"
    known = ["SRC", "SRH", "SRT", "SRM", "SRP", "SRL", "SRV", "LRV", "LRT", "LRH", "LRP", "LRL", "LRC", "LTR", "LVR", "LPV", "LEP", "RRV"]
    for code in known:
        if code in text:
            return normalize_series_code(code)
    match = re.search(r"\b([A-Z]{2,4})\d{2,6}[A-Z]?\b", text)
    return normalize_series_code(match.group(1)) if match else "UNKNOWN"


def read_csv_rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)
        for row in reader:
            if not any(clean(cell) for cell in row):
                continue
            yield headers, row


def build_index(headers):
    return {name: idx for idx, name in enumerate(headers)}


def get_value(row, index_map, key):
    idx = index_map.get(key)
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def add_component(bucket, ticket_id, category, cost):
    bucket["lineItems"] += 1
    bucket["tickets"].add(ticket_id)
    bucket["cost"] += cost
    if category:
        bucket["categories"][category] += 1


def finalize_bucket(bucket, total_tickets, total_cost):
    items = []
    for key, stat in bucket.items():
        categories = stat["categories"]
        category = categories.most_common(1)[0][0] if categories else "Other"
        items.append({
            "component": key,
            "category": category,
            "tickets": len(stat["tickets"]),
            "lineItems": stat["lineItems"],
            "cost": round(stat["cost"], 3),
            "ticketShare": round(len(stat["tickets"]) / total_tickets, 6) if total_tickets else 0,
            "costShare": round(stat["cost"] / total_cost, 6) if total_cost else 0,
        })
    items.sort(key=lambda item: (-item["tickets"], -item["cost"], item["component"]))
    return items[:10]


def main():
    parts_meta_path, parts_csv_path = resolve_parts_sources()
    ticket_map_payload = json.loads(PARTS_TICKET_MAP.read_text(encoding="utf-8"))
    series_by_chassis, series_by_sales_order = load_vehicle_base_maps()
    ticket_series = {}
    ticket_series_counts = defaultdict(Counter)
    series_ticket_sets = defaultdict(set)
    for row in ticket_map_payload.get("rows", []):
        ticket_id = clean(row.get("ticketId"))
        if not ticket_id:
            continue
        series = extract_series({
            "Serial ID": row.get("serialId"),
            "Chassis Number": row.get("chassisNumber"),
            "Sales Order": row.get("salesOrder"),
            "Vehicle Dispatch Serial": row.get("vehicleDispatchSerial"),
            "Vehicle Dispatch Sales Order": row.get("vehicleDispatchSalesOrder"),
            "Ticket ID": row.get("ticketId"),
        }, series_by_chassis, series_by_sales_order)
        if is_excluded_series(series) or not is_tracked_series(series):
            continue
        ticket_series_counts[ticket_id][series] += 1
        series_ticket_sets[series].add(ticket_id)

    for ticket_id, counter in ticket_series_counts.items():
        ticket_series[ticket_id] = counter.most_common(1)[0][0]

    parts_stats_all = defaultdict(lambda: {"lineItems": 0, "tickets": set(), "cost": 0.0, "categories": Counter()})
    parts_stats_by_series = defaultdict(lambda: defaultdict(lambda: {"lineItems": 0, "tickets": set(), "cost": 0.0, "categories": Counter()}))
    unmatched_rows = 0
    excluded_rows = 0
    total_rows = 0
    included_rows = 0
    total_cost = 0.0

    parts_headers = None
    parts_index = {}
    for headers, row in read_csv_rows(parts_csv_path):
        if parts_headers is None:
            parts_headers = headers
            parts_index = build_index(headers)
        total_rows += 1
        ticket_id = clean(get_value(row, parts_index, "Ticket ID"))
        series = ticket_series.get(ticket_id, "UNKNOWN")
        if series == "UNKNOWN":
            series = extract_series({
                "Ticket ID": ticket_id,
                "Sales Order": get_value(row, parts_index, "Sales Order"),
                "Serial ID": get_value(row, parts_index, "Serial ID"),
                "Chassis Number": get_value(row, parts_index, "Chassis Number"),
                "Matched Serial": get_value(row, parts_index, "Matched Serial"),
                "Matched Chassis": get_value(row, parts_index, "Matched Chassis"),
                "Matched Sales Order": get_value(row, parts_index, "Matched Sales Order"),
            }, series_by_chassis, series_by_sales_order)
        if series == "UNKNOWN":
            unmatched_rows += 1
        if is_excluded_series(series) or not is_tracked_series(series):
            excluded_rows += 1
            continue
        series_ticket_sets[series].add(ticket_id)
        included_rows += 1

        keyword = clean(get_value(row, parts_index, "Matched Keyword"))
        category = clean(get_value(row, parts_index, "Part Category")) or "Other"
        component = keyword or category or "Other"
        component_label = normalize_component_label(component, category)
        cost = parse_amount(get_value(row, parts_index, "Preferred Line Cost (AUD)"))
        if cost == 0:
            cost = parse_amount(get_value(row, parts_index, "Amount Including Tax"))
        total_cost += cost

        add_component(parts_stats_all[component_label], ticket_id, category, cost)
        add_component(parts_stats_by_series[series][component_label], ticket_id, category, cost)

    total_tickets_all = len({ticket for ticket, series in ticket_series.items() if not is_excluded_series(series) and is_tracked_series(series)})
    overall = {
        "lineItems": included_rows,
        "tickets": total_tickets_all,
        "cost": round(total_cost, 3),
        "topComponents": finalize_bucket(parts_stats_all, total_tickets_all, total_cost),
    }

    series_payload = {}
    all_series_keys = sorted(
        set(parts_stats_by_series.keys()),
        key=lambda s: (s not in SERIES_ORDER, SERIES_ORDER.index(s) if s in SERIES_ORDER else 999, s),
    )
    for series in all_series_keys:
        bucket = parts_stats_by_series[series]
        ticket_total = len(series_ticket_sets.get(series, set()))
        if ticket_total == 0:
            ticket_total = len({ticket for stat in bucket.values() for ticket in stat["tickets"]})
        series_cost = sum(stat["cost"] for stat in bucket.values())
        series_payload[series] = {
            "lineItems": sum(stat["lineItems"] for stat in bucket.values()),
            "tickets": ticket_total,
            "cost": round(series_cost, 3),
            "topComponents": finalize_bucket(bucket, ticket_total, series_cost),
        }

    payload = {
        "meta": {
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "partsRows": total_rows,
            "includedPartsRows": included_rows,
            "mappedTickets": total_tickets_all,
            "seriesCount": len(series_payload),
            "unmatchedRows": unmatched_rows,
            "excludedRows": excluded_rows,
            "partsSource": relative_path(parts_csv_path),
            "ticketMapSource": relative_path(PARTS_TICKET_MAP),
            "vehicleBaseSource": relative_path(VEHICLE_BASE_SUMMARY) if VEHICLE_BASE_SUMMARY.exists() else "",
            "partsMetaSource": relative_path(parts_meta_path) if parts_meta_path else "",
        },
        "all": overall,
        "series": series_payload,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    OUT.write_text(payload_text, encoding="utf-8")
    OUT_JS.write_text(
        "globalThis.ANALYSIS_PARTS_FAILURE_SUMMARY = "
        + payload_text
        + ";\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUT}")
    print(f"Wrote {OUT_JS}")


if __name__ == "__main__":
    main()
