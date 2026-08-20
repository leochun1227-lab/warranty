import csv
import json
import calendar
import os
import re
import time
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
TICKET_FAILURE_TIMING = OUTPUT_DIR / "analysis_ticket_failure_timing.csv"
TICKET_BASE_CSV = OUTPUT_DIR / "analysis_ticket_base.csv"
OUT = ROOT / "outputs" / "analysis_parts_failure_summary.json"
OUT_JS = ROOT / "outputs" / "analysis_parts_failure_summary.js"
OUT_LIGHT = ROOT / "outputs" / "analysis_parts_failure_light.json"
OUT_LIGHT_JS = ROOT / "outputs" / "analysis_parts_failure_light.js"
OUT_DERIVED = ROOT / "outputs" / "analysis_parts_derived_cache.json"
OUT_DERIVED_JS = ROOT / "outputs" / "analysis_parts_derived_cache.js"

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


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    last_error = None
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as err:
            last_error = err
            time.sleep(0.25 * (attempt + 1))
    raise last_error


def parse_date(value):
    text = clean(value)
    if not text or text == "#":
        return None
    text = text.replace("Z", "+00:00")
    if re.match(r"^\d{8}$", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def month_key_for_date(date_value):
    return f"{date_value.year:04d}-{date_value.month:02d}" if date_value else ""


def month_label(month_key):
    match = re.match(r"^(\d{4})-(\d{2})$", clean(month_key))
    if not match:
        return clean(month_key)
    year = int(match.group(1))
    month = int(match.group(2))
    if month < 1 or month > 12:
        return clean(month_key)
    return f"{calendar.month_abbr[month]} {year}"


def normalize_parts_date_basis(value):
    return "APPROVED" if clean(value).upper() == "APPROVED" else "CREATED"


def ticket_claim_scope(row):
    text = " ".join(clean(row.get(key)) for key in (
        "Claim Scope",
        "ClaimScope",
        "claimScope",
        "claimType",
        "ClaimType",
        "Claim Type",
        "Ticket Type",
        "TicketType",
        "TicketTypeText",
        "Ticket Type Text",
        "WarrantyClaimType",
        "Warranty Claim Type",
    )).lower()
    if "pre delivery" in text or "pre-delivery" in text or "predelivery" in text:
        return "PRE"
    if "in field" in text or "in-field" in text or "infield" in text or "field warranty" in text:
        return "FIELD"
    return "OTHER"


def ticket_is_pdi(row):
    code = clean(row.get("TicketType") or row.get("Ticket Type")).upper()
    text = " ".join(clean(row.get(key)) for key in (
        "Claim Scope",
        "ClaimScope",
        "claimScope",
        "claimType",
        "ClaimType",
        "Claim Type",
        "Ticket Type",
        "TicketType",
        "TicketTypeText",
        "Ticket Type Text",
        "WarrantyClaimType",
        "Warranty Claim Type",
    )).lower()
    return code == "Z010" or "pdi" in text


def ticket_id_from_row(row):
    return clean(
        row.get("Ticket ID")
        or row.get("TicketID")
        or row.get("TicketId")
        or row.get("ticketId")
    )


def repaired_ticket_row(headers, row):
    out = {}
    numeric_ticket_id = ""
    for idx, header in enumerate(headers):
        key = clean(header)
        value = clean(row[idx]) if idx < len(row) else ""
        if key and key not in out:
            out[key] = value
        prev_key = clean(headers[idx - 1]) if idx > 0 else ""
        next_key = clean(headers[idx + 1]) if idx + 1 < len(headers) else ""
        if not numeric_ticket_id and value.isdigit() and (prev_key in {"Ticket", "Ticket ID"} or next_key == "Ticket ID"):
            numeric_ticket_id = value
    if numeric_ticket_id:
        out["Ticket ID"] = numeric_ticket_id
        out["TicketID"] = numeric_ticket_id
        out["TicketId"] = numeric_ticket_id
    return out


def lookup_first_date(row, maps, key_fn):
    for key in key_fn(row):
        value = maps.get(key)
        parsed = parse_date(value)
        if parsed:
            return parsed
    return None


def load_ticket_month_scope():
    attrs = {}
    source_paths = []
    _, _, pgi_by_chassis, pgi_by_sales_order = load_vehicle_base_maps()
    for path in (TICKET_BASE_CSV, TICKET_FAILURE_TIMING):
        if not path.exists():
            continue
        source_paths.append(path)
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                headers = next(reader)
            except StopIteration:
                continue
            for values in reader:
                if not any(clean(cell) for cell in values):
                    continue
                row = repaired_ticket_row(headers, values)
                ticket_id = ticket_id_from_row(row)
                if not ticket_id:
                    continue
                created = (
                    parse_date(row.get("Created On ISO"))
                    or parse_date(row.get("Created On"))
                    or parse_date(row.get("CreatedOn"))
                    or parse_date(row.get("Posting Date"))
                )
                approved = (
                    parse_date(row.get("Claim Approved On"))
                    or parse_date(row.get("Claim Approved Date"))
                    or parse_date(row.get("Approved On"))
                    or parse_date(row.get("Approved Date"))
                )
                scope = ticket_claim_scope(row)
                is_pdi = ticket_is_pdi(row)
                existing = attrs.setdefault(ticket_id, {
                    "month": "",
                    "approvedMonth": "",
                    "createdDate": "",
                    "approvedDate": "",
                    "scope": "OTHER",
                    "isPdi": False,
                    "hasPgi": False,
                    "pgiDate": "",
                    "goodReceiveDate": "",
                })
                if is_pdi:
                    existing["isPdi"] = True
                    existing["scope"] = "PDI"
                created_month = month_key_for_date(created)
                approved_month = month_key_for_date(approved)
                pgi_date = (
                    parse_date(row.get("PGI Date"))
                    or parse_date(row.get("Delivery Date"))
                    or parse_date(row.get("Dispatch Date"))
                    or lookup_first_date(row, pgi_by_chassis, vehicle_series_lookup_keys)
                    or lookup_first_date(row, pgi_by_sales_order, sales_order_lookup_keys)
                )
                good_receive_date = (
                    parse_date(row.get("Good Receive Date"))
                    or parse_date(row.get("Goods Receipt Date"))
                    or parse_date(row.get("GR Date"))
                )
                if created_month and not existing.get("month"):
                    existing["month"] = created_month
                if created and not existing.get("createdDate"):
                    existing["createdDate"] = created.isoformat()
                if approved_month and not existing.get("approvedMonth"):
                    existing["approvedMonth"] = approved_month
                if approved and not existing.get("approvedDate"):
                    existing["approvedDate"] = approved.isoformat()
                if not existing.get("isPdi") and existing.get("scope") == "OTHER" and scope != "OTHER":
                    existing["scope"] = scope
                if pgi_date:
                    existing["hasPgi"] = True
                    if not existing.get("pgiDate"):
                        existing["pgiDate"] = pgi_date.isoformat()
                if good_receive_date and not existing.get("goodReceiveDate"):
                    existing["goodReceiveDate"] = good_receive_date.isoformat()
    return attrs, source_paths[0] if source_paths else None, source_paths


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
        return {}, {}, {}, {}
    try:
        payload = json.loads(VEHICLE_BASE_SUMMARY.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}, {}, {}
    chassis = payload.get("seriesByChassis") if isinstance(payload, dict) else {}
    sales_order = payload.get("seriesBySalesOrder") if isinstance(payload, dict) else {}
    pgi_chassis = payload.get("pgiByChassis") if isinstance(payload, dict) else {}
    pgi_sales_order = payload.get("pgiBySalesOrder") if isinstance(payload, dict) else {}
    return (
        chassis if isinstance(chassis, dict) else {},
        sales_order if isinstance(sales_order, dict) else {},
        pgi_chassis if isinstance(pgi_chassis, dict) else {},
        pgi_sales_order if isinstance(pgi_sales_order, dict) else {},
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


def make_failure_row(attrs):
    created_date = parse_date((attrs or {}).get("createdDate"))
    pgi_date = parse_date((attrs or {}).get("pgiDate"))
    if not (created_date and pgi_date):
        return None
    return {
        "ageDays": (created_date - pgi_date).days,
        "pgiDate": pgi_date.isoformat(),
    }


def add_failure_ticket(bucket, ticket_id, claim_scope, failure_row):
    if not (ticket_id and failure_row):
        return
    scope_for_failure = claim_scope if claim_scope in {"PRE", "FIELD", "OTHER"} else "OTHER"
    bucket.setdefault("_failureTickets", {"ALL": {}, "PRE": {}, "FIELD": {}, "OTHER": {}})
    bucket["_failureTickets"].setdefault("ALL", {})[ticket_id] = failure_row
    bucket["_failureTickets"].setdefault(scope_for_failure, {})[ticket_id] = failure_row


def failure_age_payload_from_rows(rows):
    rows = list(rows or [])
    ages = [float(row.get("ageDays")) for row in rows if row.get("ageDays") is not None]
    pgi_counts = Counter(clean(row.get("pgiDate")) for row in rows if clean(row.get("pgiDate")))
    pgi_month_counts = Counter(date_key[:7] for date_key in pgi_counts.elements() if len(date_key) >= 7)
    matched = sum(pgi_counts.values())
    pgi_months = [
        {
            "month": month_key,
            "tickets": count,
            "share": round(count / matched, 6) if matched else 0,
        }
        for month_key, count in sorted(pgi_month_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "tickets": len(rows),
        "pgiMatchedTickets": matched,
        "avgFailureDays": round(sum(ages) / len(ages), 1) if ages else None,
        "minFailureDays": round(min(ages), 1) if ages else None,
        "maxFailureDays": round(max(ages), 1) if ages else None,
        "topPgiMonths": pgi_months[:5],
        "pgiMonthCounts": {
            month_key: count
            for month_key, count in sorted(pgi_month_counts.items())
        },
    }


def finalize_failure_tickets(failure_tickets):
    failure_tickets = failure_tickets or {}
    return {
        scope: failure_age_payload_from_rows((failure_tickets.get(scope) or {}).values())
        for scope in ("ALL", "PRE", "FIELD", "OTHER")
    }


def add_component(bucket, ticket_id, category, cost, series="", claim_scope="OTHER", failure_row=None):
    bucket["lineItems"] += 1
    bucket["tickets"].add(ticket_id)
    bucket["cost"] += cost
    add_failure_ticket(bucket, ticket_id, claim_scope, failure_row)
    if category:
        bucket["categories"][category] += 1
    series_key = normalize_series_code(series or "")
    if series_key:
        bucket["seriesTickets"][series_key].add(ticket_id)
        bucket["seriesLineItems"][series_key] += 1
        bucket["seriesCost"][series_key] += cost


def component_bucket():
    return {
        "lineItems": 0,
        "tickets": set(),
        "cost": 0.0,
        "categories": Counter(),
        "seriesTickets": defaultdict(set),
        "seriesLineItems": Counter(),
        "seriesCost": Counter(),
        "_failureTickets": {"ALL": {}, "PRE": {}, "FIELD": {}, "OTHER": {}},
    }


def aggregate_bucket():
    return {"lineItems": 0, "tickets": set(), "cost": 0.0, "components": defaultdict(component_bucket)}


def add_to_aggregate(bucket, component_label, ticket_id, category, cost, series="", claim_scope="OTHER", failure_row=None):
    bucket["lineItems"] += 1
    bucket["tickets"].add(ticket_id)
    bucket["cost"] += cost
    add_component(bucket["components"][component_label], ticket_id, category, cost, series, claim_scope, failure_row)


def finalize_bucket(bucket, total_tickets, total_cost, total_line_items=None):
    if total_line_items is None:
        total_line_items = sum(stat["lineItems"] for stat in bucket.values())
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
            "failureAge": finalize_failure_tickets(stat.get("_failureTickets")),
            "ticketShare": round(len(stat["tickets"]) / total_tickets, 6) if total_tickets else 0,
            "lineShare": round(stat["lineItems"] / total_line_items, 6) if total_line_items else 0,
            "costShare": round(stat["cost"] / total_cost, 6) if total_cost else 0,
            "series": {
                series: {
                    "tickets": len(tickets),
                    "lineItems": int(stat.get("seriesLineItems", {}).get(series) or 0),
                    "cost": round(float(stat.get("seriesCost", {}).get(series) or 0.0), 3),
                }
                for series, tickets in sorted((stat.get("seriesTickets") or {}).items(), key=lambda item: series_sort_key(item[0]))
            },
        })
    items.sort(key=lambda item: (-item["tickets"], -item["lineItems"], -item["cost"], item["component"]))
    return items[:10]


def top_component_coverage(components, top_items):
    ticket_ids = set()
    line_items = 0
    cost = 0.0
    series_ticket_ids = defaultdict(set)
    series_line_items = Counter()
    series_cost = Counter()
    for item in top_items:
        stat = components.get(item.get("component")) if components else None
        if not stat:
            continue
        ticket_ids.update(stat.get("tickets") or set())
        line_items += int(stat.get("lineItems") or 0)
        cost += float(stat.get("cost") or 0.0)
        for series, tickets in (stat.get("seriesTickets") or {}).items():
            series_ticket_ids[series].update(tickets or set())
        for series, count in (stat.get("seriesLineItems") or {}).items():
            series_line_items[series] += int(count or 0)
        for series, value in (stat.get("seriesCost") or {}).items():
            series_cost[series] += float(value or 0.0)
    return {
        "components": len(top_items),
        "tickets": len(ticket_ids),
        "lineItems": line_items,
        "cost": round(cost, 3),
        "series": {
            series: {
                "tickets": len(tickets),
                "lineItems": int(series_line_items.get(series) or 0),
                "cost": round(float(series_cost.get(series) or 0.0), 3),
            }
            for series, tickets in sorted(series_ticket_ids.items(), key=lambda item: series_sort_key(item[0]))
        },
    }


def finalize_aggregate(bucket):
    if not bucket:
        bucket = {"lineItems": 0, "tickets": set(), "cost": 0.0, "components": {}}
    total_tickets = len(bucket["tickets"])
    total_line_items = bucket["lineItems"]
    total_cost = bucket["cost"]
    components = bucket["components"]
    top_by_tickets = finalize_bucket(components, total_tickets, total_cost, total_line_items)
    return {
        "lineItems": total_line_items,
        "tickets": total_tickets,
        "cost": round(total_cost, 3),
        "topComponents": top_by_tickets,
        "topComponentsByTickets": top_by_tickets,
        "topComponentCoverage": {
            "tickets": top_component_coverage(components, top_by_tickets),
        },
    }


def make_derived_component_entry():
    return {
        "materialQty": 0.0,
        "bases": {
            "APPROVED": {"series": {}},
            "CREATED": {"series": {}},
        },
    }


def make_derived_month_bucket():
    return {
        "lineItems": 0,
        "cost": 0.0,
        "tickets": 0,
        "materialQty": 0.0,
        "scope": {"PRE": 0, "FIELD": 0, "OTHER": 0},
        "scopeCost": {"PRE": 0.0, "FIELD": 0.0, "OTHER": 0.0},
        "_ticketIds": set(),
        "_scopeIds": {"PRE": set(), "FIELD": set(), "OTHER": set()},
        "_failureTickets": {"ALL": {}, "PRE": {}, "FIELD": {}, "OTHER": {}},
    }


def ensure_derived_series_bucket(entry, basis, series):
    basis_key = normalize_parts_date_basis(basis)
    basis_bucket = entry["bases"].setdefault(basis_key, {"series": {}})
    series_key = normalize_series_code(series or "ALL")
    if series_key not in basis_bucket["series"]:
        basis_bucket["series"][series_key] = {"months": {}}
    return basis_bucket["series"][series_key]


def ensure_derived_month_bucket(series_bucket, month_key):
    months = series_bucket["months"]
    if month_key not in months:
        months[month_key] = make_derived_month_bucket()
    return months[month_key]


def finalize_derived_month_bucket(bucket):
    bucket["tickets"] = len(bucket.pop("_ticketIds", set()))
    scope_ids = bucket.pop("_scopeIds", {})
    bucket["scope"] = {
        "PRE": len(scope_ids.get("PRE", set())),
        "FIELD": len(scope_ids.get("FIELD", set())),
        "OTHER": len(scope_ids.get("OTHER", set())),
    }
    scope_cost = bucket.get("scopeCost") or {}
    bucket["scopeCost"] = {
        "PRE": round(float(scope_cost.get("PRE") or 0.0), 3),
        "FIELD": round(float(scope_cost.get("FIELD") or 0.0), 3),
        "OTHER": round(float(scope_cost.get("OTHER") or 0.0), 3),
    }
    failure_tickets = bucket.pop("_failureTickets", {}) or {}
    failure_payload = {}
    for scope in ("ALL", "PRE", "FIELD", "OTHER"):
        rows = list((failure_tickets.get(scope) or {}).values())
        ages = [float(row.get("ageDays")) for row in rows if row.get("ageDays") is not None]
        pgi_counts = Counter(clean(row.get("pgiDate")) for row in rows if clean(row.get("pgiDate")))
        pgi_month_counts = Counter(date_key[:7] for date_key in pgi_counts.elements() if len(date_key) >= 7)
        matched = sum(pgi_counts.values())
        pgi_months = [
            {
                "month": month_key,
                "tickets": count,
                "share": round(count / matched, 6) if matched else 0,
            }
            for month_key, count in sorted(pgi_month_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        failure_payload[scope] = {
            "tickets": len(rows),
            "pgiMatchedTickets": matched,
            "avgFailureDays": round(sum(ages) / len(ages), 1) if ages else None,
            "minFailureDays": round(min(ages), 1) if ages else None,
            "maxFailureDays": round(max(ages), 1) if ages else None,
            "topPgiMonths": pgi_months[:5],
            "pgiMonthCounts": {
                month_key: count
                for month_key, count in sorted(pgi_month_counts.items())
            },
        }
    bucket["failureAge"] = failure_payload
    return bucket


def finalize_derived_cache(derived_by_key):
    out = {}
    for comp_key, entry in derived_by_key.items():
        entry["materialQty"] = round(float(entry.get("materialQty") or 0.0), 3)
        for basis_bucket in entry.get("bases", {}).values():
            for series_bucket in basis_bucket.get("series", {}).values():
                months = series_bucket.get("months", {})
                for month_key in list(months.keys()):
                    finalize_derived_month_bucket(months[month_key])
                    months[month_key]["cost"] = round(float(months[month_key].get("cost") or 0.0), 3)
                    months[month_key]["materialQty"] = round(float(months[month_key].get("materialQty") or 0.0), 3)
        out[comp_key] = entry
    return {
        "meta": {
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "byKey": out,
    }


def build_component_trends(month_keys, bucket_for_month):
    components = set()
    monthly_buckets = {}
    for month in month_keys:
        bucket = bucket_for_month(month) or {}
        monthly_buckets[month] = bucket
        components.update((bucket.get("components") or {}).keys())

    trends = {}
    for component in sorted(components):
        points = []
        for month in month_keys:
            stat = (monthly_buckets.get(month, {}).get("components") or {}).get(component)
            points.append([
                month,
                len(stat["tickets"]) if stat else 0,
                stat["lineItems"] if stat else 0,
            ])
        trends[component] = points
    return trends


def series_sort_key(series):
    return (series not in SERIES_ORDER, SERIES_ORDER.index(series) if series in SERIES_ORDER else 999, series)


def build_scope_payload(scope, scope_totals, scope_series_totals, month_totals, month_series_totals, year_totals, year_series_totals):
    month_keys_asc = sorted([month for scope_key, month in month_totals.keys() if scope_key == scope])
    payload = finalize_aggregate(scope_totals.get(scope))
    payload["componentTrends"] = build_component_trends(
        month_keys_asc,
        lambda month: month_totals.get((scope, month)),
    )
    series_payload = {}
    for series in sorted(scope_series_totals.get(scope, {}).keys(), key=series_sort_key):
        series_payload[series] = finalize_aggregate(scope_series_totals[scope][series])
        series_payload[series]["componentTrends"] = build_component_trends(
            month_keys_asc,
            lambda month, series=series: month_series_totals.get((scope, month), {}).get(series),
        )
    payload["series"] = series_payload

    months = []
    month_keys = sorted(month_keys_asc, reverse=True)
    for month in month_keys:
        month_payload = finalize_aggregate(month_totals.get((scope, month)))
        month_payload["month"] = month
        month_payload["label"] = month_label(month)
        month_payload["series"] = {}
        for series in sorted(month_series_totals.get((scope, month), {}).keys(), key=series_sort_key):
            month_payload["series"][series] = finalize_aggregate(month_series_totals[(scope, month)][series])
        months.append(month_payload)
    payload["months"] = months

    years = []
    year_keys = sorted([year for scope_key, year in year_totals.keys() if scope_key == scope], reverse=True)
    for year in year_keys:
        year_payload = finalize_aggregate(year_totals.get((scope, year)))
        year_payload["year"] = year
        year_payload["label"] = year
        year_payload["series"] = {}
        for series in sorted(year_series_totals.get((scope, year), {}).keys(), key=series_sort_key):
            year_payload["series"][series] = finalize_aggregate(year_series_totals[(scope, year)][series])
        years.append(year_payload)
    payload["years"] = years
    return payload


def compact_failure_age_summary(value):
    """Keep enough timing data for first paint without large PGI month maps."""
    if not isinstance(value, dict):
        return value
    top_months = []
    for item in value.get("topPgiMonths") or []:
        if not isinstance(item, dict):
            continue
        top_months.append({
            "month": item.get("month") or "",
            "tickets": int(item.get("tickets") or 0),
            "share": round(float(item.get("share") or 0), 6),
        })
    return {
        "tickets": int(value.get("tickets") or 0),
        "pgiMatchedTickets": int(value.get("pgiMatchedTickets") or 0),
        "avgFailureDays": value.get("avgFailureDays"),
        "topPgiMonths": top_months[:5],
    }


def compact_for_initial_render(value, path=()):
    """Keep the component leaderboard payload small enough for first paint."""
    if isinstance(value, dict):
        if "avgFailureDays" in value and "pgiMatchedTickets" in value:
            return compact_failure_age_summary(value)
        is_component_row = (
            "component" in value
            and "category" in value
            and ("tickets" in value or "lineItems" in value)
        )
        return {
            key: compact_for_initial_render(item, path + (str(key),))
            for key, item in value.items()
            if key not in {"componentTrends", "pgiMonthCounts", "minFailureDays", "maxFailureDays"}
            and not (is_component_row and key == "series")
            and not (key == "series" and ("months" in path or "years" in path))
        }
    if isinstance(value, list):
        return [compact_for_initial_render(item, path) for item in value]
    return value


def validate_summary_bucket(bucket, label):
    if not isinstance(bucket, dict):
        return
    tickets = int(bucket.get("tickets") or 0)
    line_items = int(bucket.get("lineItems") or 0)
    if tickets < 0 or line_items < 0:
        raise ValueError(f"{label} has negative tickets or lineItems")
    if tickets > line_items:
        raise ValueError(f"{label} has tickets > lineItems ({tickets} > {line_items})")
    for component in bucket.get("topComponents") or []:
        comp_label = f"{label} / {component.get('component', 'component')}"
        comp_tickets = int(component.get("tickets") or 0)
        comp_lines = int(component.get("lineItems") or 0)
        if comp_tickets > comp_lines:
            raise ValueError(f"{comp_label} has tickets > lineItems ({comp_tickets} > {comp_lines})")


def validate_summary_tree(value, label="summary"):
    if isinstance(value, dict):
        if "tickets" in value and "lineItems" in value:
            validate_summary_bucket(value, label)
        for key, item in value.items():
            if key == "componentTrends":
                continue
            validate_summary_tree(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_summary_tree(item, f"{label}[{index}]")


def validate_derived_cache(derived_payload):
    by_key = derived_payload.get("byKey") if isinstance(derived_payload, dict) else None
    if not isinstance(by_key, dict) or not by_key:
        raise ValueError("derived cache has no component buckets")
    for comp_key, entry in by_key.items():
        bases = entry.get("bases") if isinstance(entry, dict) else {}
        for basis_key, basis_bucket in (bases or {}).items():
            series_map = (basis_bucket or {}).get("series") or {}
            for series_key, series_bucket in series_map.items():
                months = (series_bucket or {}).get("months") or {}
                for month_key, month_bucket in months.items():
                    tickets = int((month_bucket or {}).get("tickets") or 0)
                    line_items = int((month_bucket or {}).get("lineItems") or 0)
                    if tickets > line_items:
                        raise ValueError(
                            f"derived {comp_key} {basis_key}/{series_key}/{month_key} "
                            f"has tickets > lineItems ({tickets} > {line_items})"
                        )


def validate_outputs(payload, derived_payload):
    meta = payload.get("meta") or {}
    included_rows = int(meta.get("includedPartsRows") or 0)
    mapped_tickets = int(meta.get("mappedTickets") or 0)
    if included_rows <= 0:
        raise ValueError("parts failure summary has no included parts rows")
    if mapped_tickets <= 0:
        raise ValueError("parts failure summary has no mapped tickets")
    validate_summary_tree(payload)
    validate_derived_cache(derived_payload)
    derived_meta = derived_payload.get("meta") or {}
    for key in ("includedPartsRows", "mappedTickets"):
        if int(derived_meta.get(key) or 0) != int(meta.get(key) or 0):
            raise ValueError(f"summary and derived cache meta mismatch for {key}")


def main():
    parts_meta_path, parts_csv_path = resolve_parts_sources()
    ticket_map_payload = json.loads(PARTS_TICKET_MAP.read_text(encoding="utf-8"))
    ticket_month_scope, ticket_month_scope_source, ticket_month_scope_sources = load_ticket_month_scope()
    series_by_chassis, series_by_sales_order, pgi_by_chassis, pgi_by_sales_order = load_vehicle_base_maps()
    ticket_series = {}
    ticket_series_counts = defaultdict(Counter)
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

    for ticket_id, counter in ticket_series_counts.items():
        ticket_series[ticket_id] = counter.most_common(1)[0][0]

    parts_stats_all = defaultdict(component_bucket)
    parts_stats_by_series = defaultdict(lambda: defaultdict(component_bucket))
    derived_by_key = {}
    date_basis_aggs = {
        basis: {
            "scope_totals": defaultdict(aggregate_bucket),
            "scope_series_totals": defaultdict(lambda: defaultdict(aggregate_bucket)),
            "month_totals": defaultdict(aggregate_bucket),
            "month_series_totals": defaultdict(lambda: defaultdict(aggregate_bucket)),
            "year_totals": defaultdict(aggregate_bucket),
            "year_series_totals": defaultdict(lambda: defaultdict(aggregate_bucket)),
        }
        for basis in ("CREATED", "APPROVED")
    }
    unmatched_rows = 0
    excluded_rows = 0
    total_rows = 0
    included_rows = 0
    total_cost = 0.0
    monthly_rows = 0
    approved_monthly_rows = 0
    monthly_missing_ticket_attrs = 0
    missing_pgi_rows = 0

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
        attrs = ticket_month_scope.get(ticket_id)
        if not attrs:
            excluded_rows += 1
            continue
        if attrs.get("isPdi") or attrs.get("scope") == "PDI":
            excluded_rows += 1
            continue
        if not attrs.get("hasPgi"):
            missing_pgi_rows += 1
        included_rows += 1

        keyword = clean(get_value(row, parts_index, "Matched Keyword"))
        category = clean(get_value(row, parts_index, "Part Category")) or "Other"
        component = keyword or category or "Other"
        component_label = normalize_component_label(component, category)
        cost = parse_amount(get_value(row, parts_index, "Preferred Line Cost (AUD)"))
        material_qty = parse_amount(get_value(row, parts_index, "Order Qty"))
        total_cost += cost

        month_by_basis = {
            "CREATED": attrs.get("month") if attrs else "",
            "APPROVED": attrs.get("approvedMonth") if attrs else "",
        }
        claim_scope = attrs.get("scope") if attrs else "OTHER"
        failure_row = make_failure_row(attrs)

        add_component(parts_stats_all[component_label], ticket_id, category, cost, series, claim_scope, failure_row)
        add_component(parts_stats_by_series[series][component_label], ticket_id, category, cost, series, claim_scope, failure_row)

        comp_key = f"{component_label.lower()}||{category.lower()}"
        if comp_key not in derived_by_key:
            derived_by_key[comp_key] = make_derived_component_entry()
        derived_entry = derived_by_key[comp_key]
        derived_entry["materialQty"] = float(derived_entry.get("materialQty") or 0.0) + material_qty
        for basis, month in month_by_basis.items():
            if not month:
                continue
            basis_key = normalize_parts_date_basis(basis)
            for series_key in (series, "ALL"):
                series_bucket = ensure_derived_series_bucket(derived_entry, basis_key, series_key)
                month_bucket = ensure_derived_month_bucket(series_bucket, month)
                month_bucket["lineItems"] += 1
                month_bucket["cost"] += cost
                month_bucket["materialQty"] += material_qty
                scope_cost_key = claim_scope if claim_scope in {"PRE", "FIELD", "OTHER"} else "OTHER"
                month_bucket.setdefault("scopeCost", {"PRE": 0.0, "FIELD": 0.0, "OTHER": 0.0})
                month_bucket["scopeCost"][scope_cost_key] = float(month_bucket["scopeCost"].get(scope_cost_key) or 0.0) + cost
                if ticket_id:
                    month_bucket["_ticketIds"].add(ticket_id)
                    month_bucket["_scopeIds"].setdefault(claim_scope, set()).add(ticket_id)
                    if failure_row:
                        scope_for_failure = claim_scope if claim_scope in {"PRE", "FIELD", "OTHER"} else "OTHER"
                        month_bucket["_failureTickets"].setdefault("ALL", {})[ticket_id] = failure_row
                        month_bucket["_failureTickets"].setdefault(scope_for_failure, {})[ticket_id] = failure_row
        for basis, month in month_by_basis.items():
            if not month:
                continue
            if basis == "CREATED":
                monthly_rows += 1
            elif basis == "APPROVED":
                approved_monthly_rows += 1
            year = month[:4]
            aggs = date_basis_aggs[basis]
            scope_keys = ["ALL", claim_scope if claim_scope in {"PRE", "FIELD", "OTHER"} else "OTHER"]
            for scope_key in dict.fromkeys(scope_keys):
                add_to_aggregate(aggs["scope_totals"][scope_key], component_label, ticket_id, category, cost, series, claim_scope, failure_row)
                add_to_aggregate(aggs["scope_series_totals"][scope_key][series], component_label, ticket_id, category, cost, series, claim_scope, failure_row)
                add_to_aggregate(aggs["month_totals"][(scope_key, month)], component_label, ticket_id, category, cost, series, claim_scope, failure_row)
                add_to_aggregate(aggs["month_series_totals"][(scope_key, month)][series], component_label, ticket_id, category, cost, series, claim_scope, failure_row)
                add_to_aggregate(aggs["year_totals"][(scope_key, year)], component_label, ticket_id, category, cost, series, claim_scope, failure_row)
                add_to_aggregate(aggs["year_series_totals"][(scope_key, year)][series], component_label, ticket_id, category, cost, series, claim_scope, failure_row)

    total_tickets_all = len({ticket for stat in parts_stats_all.values() for ticket in stat["tickets"]})
    overall_top_by_tickets = finalize_bucket(parts_stats_all, total_tickets_all, total_cost, included_rows)
    overall = {
        "lineItems": included_rows,
        "tickets": total_tickets_all,
        "cost": round(total_cost, 3),
        "topComponents": overall_top_by_tickets,
        "topComponentsByTickets": overall_top_by_tickets,
        "topComponentCoverage": {
            "tickets": top_component_coverage(parts_stats_all, overall_top_by_tickets),
        },
    }

    series_payload = {}
    all_series_keys = sorted(
        set(parts_stats_by_series.keys()),
        key=lambda s: (s not in SERIES_ORDER, SERIES_ORDER.index(s) if s in SERIES_ORDER else 999, s),
    )
    for series in all_series_keys:
        bucket = parts_stats_by_series[series]
        ticket_total = len({ticket for stat in bucket.values() for ticket in stat["tickets"]})
        series_cost = sum(stat["cost"] for stat in bucket.values())
        series_line_items = sum(stat["lineItems"] for stat in bucket.values())
        series_top_by_tickets = finalize_bucket(bucket, ticket_total, series_cost, series_line_items)
        series_payload[series] = {
            "lineItems": series_line_items,
            "tickets": ticket_total,
            "cost": round(series_cost, 3),
            "topComponents": series_top_by_tickets,
            "topComponentsByTickets": series_top_by_tickets,
            "topComponentCoverage": {
                "tickets": top_component_coverage(bucket, series_top_by_tickets),
            },
        }

    date_basis_scopes = {
        basis: {
            scope: build_scope_payload(
                scope,
                aggs["scope_totals"],
                aggs["scope_series_totals"],
                aggs["month_totals"],
                aggs["month_series_totals"],
                aggs["year_totals"],
                aggs["year_series_totals"],
            )
            for scope in ("ALL", "PRE", "FIELD", "OTHER")
        }
        for basis, aggs in date_basis_aggs.items()
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
            "missingPgiRows": missing_pgi_rows,
            "partsSource": relative_path(parts_csv_path),
            "ticketMapSource": relative_path(PARTS_TICKET_MAP),
            "vehicleBaseSource": relative_path(VEHICLE_BASE_SUMMARY) if VEHICLE_BASE_SUMMARY.exists() else "",
            "vehicleBasePgiChassisKeys": len(pgi_by_chassis),
            "vehicleBasePgiSalesOrderKeys": len(pgi_by_sales_order),
            "partsMetaSource": relative_path(parts_meta_path) if parts_meta_path else "",
        },
        "all": overall,
        "series": series_payload,
        "monthScopes": date_basis_scopes["CREATED"],
        "dateBasisScopes": date_basis_scopes,
    }
    payload["meta"]["monthlyRows"] = monthly_rows
    payload["meta"]["approvedMonthlyRows"] = approved_monthly_rows
    payload["meta"]["monthlyMissingTicketRows"] = monthly_missing_ticket_attrs
    payload["meta"]["monthCount"] = len(payload["monthScopes"]["ALL"].get("months", []))
    payload["meta"]["monthlyTicketSource"] = relative_path(ticket_month_scope_source) if ticket_month_scope_source else ""
    payload["meta"]["monthlyTicketSources"] = [relative_path(path) for path in ticket_month_scope_sources]

    derived_payload = finalize_derived_cache(derived_by_key)
    derived_payload["meta"].update({
        "partsRows": total_rows,
        "includedPartsRows": included_rows,
        "mappedTickets": total_tickets_all,
        "seriesCount": len(series_payload),
        "missingPgiRows": missing_pgi_rows,
        "partsSource": relative_path(parts_csv_path),
        "ticketMapSource": relative_path(PARTS_TICKET_MAP),
        "vehicleBaseSource": relative_path(VEHICLE_BASE_SUMMARY) if VEHICLE_BASE_SUMMARY.exists() else "",
        "vehicleBasePgiChassisKeys": len(pgi_by_chassis),
        "vehicleBasePgiSalesOrderKeys": len(pgi_by_sales_order),
        "partsMetaSource": relative_path(parts_meta_path) if parts_meta_path else "",
        "monthlyRows": monthly_rows,
        "approvedMonthlyRows": approved_monthly_rows,
        "monthlyMissingTicketRows": monthly_missing_ticket_attrs,
        "monthlyTicketSource": relative_path(ticket_month_scope_source) if ticket_month_scope_source else "",
        "monthlyTicketSources": [relative_path(path) for path in ticket_month_scope_sources],
    })
    validate_outputs(payload, derived_payload)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    light_payload = compact_for_initial_render(payload)
    light_payload.setdefault("meta", {})["compactForInitialRender"] = True
    light_text = json.dumps(light_payload, ensure_ascii=False, separators=(",", ":"))
    derived_text = json.dumps(derived_payload, ensure_ascii=False, separators=(",", ":"))
    write_text_atomic(OUT, payload_text)
    write_text_atomic(
        OUT_JS,
        "globalThis.ANALYSIS_PARTS_FAILURE_SUMMARY = "
        + payload_text
        + ";\n"
    )
    write_text_atomic(OUT_LIGHT, light_text)
    write_text_atomic(
        OUT_LIGHT_JS,
        "globalThis.ANALYSIS_PARTS_FAILURE_LIGHT = "
        + light_text
        + ";\n"
    )
    write_text_atomic(OUT_DERIVED, derived_text)
    write_text_atomic(
        OUT_DERIVED_JS,
        "globalThis.ANALYSIS_PARTS_DERIVED_CACHE = "
        + derived_text
        + ";\n"
    )
    print(f"Wrote {OUT}")
    print(f"Wrote {OUT_JS}")
    print(f"Wrote {OUT_LIGHT}")
    print(f"Wrote {OUT_LIGHT_JS}")
    print(f"Wrote {OUT_DERIVED}")
    print(f"Wrote {OUT_DERIVED_JS}")


if __name__ == "__main__":
    main()
