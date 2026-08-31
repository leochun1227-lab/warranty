from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import build_analysis_parts_failure_summary as parts


ROOT = Path(__file__).resolve().parent
OUTPUT_INDEX = ROOT / "outputs" / "parts_top10_export_payload" / "index.json"
OUTPUT_GROUPS = ROOT / "outputs" / "parts_top10_export_payload" / "groups"
PARTS_TICKET_MAP = ROOT / "outputs" / "analysis_parts_ticket_cost_map.json"
PARTS_REPORT_START_YEAR = 2025


EXPORT_HEADERS = [
    "Component Rank",
    "Sheet",
    "Component",
    "Component Tickets On Page",
    "Component Lines On Page",
    "Component Cost AUD On Page",
    "Part Categories",
    "Descriptions",
    "Ticket ID",
    "Series",
    "Claim Scope",
    "Ticket Status",
    "Ticket Type",
    "Created On",
    "Claim Approved On",
    "Changed On",
    "PGI Date",
    "Good Receive Date",
    "Posting Date",
    "Date of Purchase",
    "Warranty Cost AUD",
    "Chassis Number",
    "Serial ID",
    "Dealer Name",
    "Repair Shop",
    "Line Count In This Component",
    "Materials",
    "Purchase Orders",
    "Sales Orders",
    "Order Qty",
    "Preferred Line Cost AUD",
    "Line Rejection Status",
]


def unique_join(values):
    seen = set()
    out = []
    for value in values:
        text = parts.clean(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return "; ".join(out)


def sheet_name(rank, component):
    return f"{rank:02d} {parts.clean(component) or 'Other'}"


def path_part(value):
    text = parts.clean(value) or "blank"
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)


def scope_keys(scope):
    clean_scope = parts.clean(scope).upper()
    if clean_scope in {"PRE", "FIELD", "OTHER"}:
        return ["ALL", clean_scope]
    return ["ALL"]


def year_keys(month):
    text = parts.clean(month)
    if len(text) >= 4 and text[:4].isdigit():
        return [text[:4]]
    return []


def month_keys(month):
    text = parts.clean(month)
    keys = ["ALL"]
    if len(text) >= 4 and text[:4].isdigit() and int(text[:4]) >= PARTS_REPORT_START_YEAR:
        keys.append(text[:4])
        if len(text) >= 7 and text[4] == "-":
            keys.append(text[:7])
    return keys


def ticket_series(parts_row, attrs, series_by_chassis, series_by_sales_order):
    return parts.extract_series(
        {
            "Sales Order": parts_row.get("Sales Order", ""),
            "Serial ID": parts_row.get("Serial ID", ""),
            "Chassis Number": parts_row.get("Chassis Number", ""),
            "Matched Serial": parts_row.get("Matched Serial", ""),
            "Matched Chassis": parts_row.get("Matched Chassis", ""),
            "Matched Sales Order": parts_row.get("Matched Sales Order", ""),
        },
        series_by_chassis,
        series_by_sales_order,
    )


def export_row(rank, component, stat, ticket_id, lines, attrs):
    first = lines[0] if lines else {}
    line_cost = sum(parts.parse_amount(line.get("Preferred Line Cost (AUD)", "")) for line in lines)
    order_qty = sum(parts.parse_amount(line.get("Order Qty", "")) for line in lines)
    categories = unique_join(line.get("_category") or line.get("Part Category") or "Other" for line in lines)
    descriptions = unique_join(line.get("Description", "") for line in lines)
    return [
        rank,
        sheet_name(rank, component),
        component,
        stat["tickets"],
        stat["lineItems"],
        round(float(stat["cost"] or 0), 2),
        categories,
        descriptions,
        ticket_id,
        first.get("_series", ""),
        attrs.get("scope") or "OTHER",
        first.get("Ticket Status Text") or first.get("Ticket Status") or "",
        "",
        attrs.get("createdDate") or "",
        attrs.get("approvedDate") or "",
        "",
        attrs.get("pgiDate") or "",
        attrs.get("goodReceiveDate") or "",
        first.get("Posting Date", ""),
        "",
        "",
        first.get("Chassis Number") or first.get("Matched Chassis") or "",
        first.get("Serial ID") or first.get("Matched Serial") or "",
        first.get("Dealer Name", ""),
        "",
        len(lines),
        unique_join(line.get("Material", "") for line in lines),
        unique_join(line.get("ERP Purchase Order", "") for line in lines),
        unique_join(line.get("Sales Order", "") for line in lines),
        round(order_qty, 2),
        round(line_cost, 2),
        unique_join(line.get("Item Rejection Status") or line.get("Rejection Reason") for line in lines),
    ]


def main() -> int:
    ticket_month_scope, _, _ = parts.load_ticket_month_scope()
    series_by_chassis, series_by_sales_order, _, _ = parts.load_vehicle_base_maps()
    parts_meta_path, parts_csv_path = parts.resolve_parts_sources()
    ticket_series_map = {}
    ticket_series_counts = defaultdict(parts.Counter)
    if PARTS_TICKET_MAP.exists():
        ticket_map_payload = json.loads(PARTS_TICKET_MAP.read_text(encoding="utf-8"))
        for row in ticket_map_payload.get("rows", []):
            ticket_id = parts.clean(row.get("ticketId"))
            if not ticket_id:
                continue
            series = parts.extract_series({
                "Serial ID": row.get("serialId"),
                "Chassis Number": row.get("chassisNumber"),
                "Sales Order": row.get("salesOrder"),
                "Vehicle Dispatch Serial": row.get("vehicleDispatchSerial"),
                "Vehicle Dispatch Sales Order": row.get("vehicleDispatchSalesOrder"),
                "Ticket ID": row.get("ticketId"),
            }, series_by_chassis, series_by_sales_order)
            if parts.is_excluded_series(series) or not parts.is_tracked_series(series):
                continue
            ticket_series_counts[ticket_id][series] += 1
    for ticket_id, counter in ticket_series_counts.items():
        ticket_series_map[ticket_id] = counter.most_common(1)[0][0]
    headers = None
    index = {}
    buckets = defaultdict(lambda: {
        "tickets": set(),
        "lineItems": 0,
        "cost": 0.0,
        "rows": defaultdict(list),
    })

    total_rows = 0
    included_rows = 0
    for csv_headers, row in parts.read_csv_rows(parts_csv_path):
        if headers is None:
            headers = csv_headers
            index = parts.build_index(headers)
        total_rows += 1
        ticket_id = parts.clean(parts.get_value(row, index, "Ticket ID"))
        if not ticket_id:
            continue
        attrs = ticket_month_scope.get(ticket_id)
        if not attrs or attrs.get("isPdi") or attrs.get("scope") == "PDI":
            continue
        raw = {
            header: parts.clean(row[idx]) if idx < len(row) else ""
            for idx, header in enumerate(headers or [])
            if parts.clean(header)
        }
        series = ticket_series_map.get(ticket_id) or ticket_series(raw, attrs, series_by_chassis, series_by_sales_order)
        if parts.is_excluded_series(series) or not parts.is_tracked_series(series):
            continue
        keyword = parts.clean(raw.get("Matched Keyword"))
        category = parts.clean(raw.get("Part Category")) or "Other"
        description = parts.clean(raw.get("Description"))
        component = keyword or category or "Other"
        component_label, category = parts.normalize_component_classification(component, category, description)
        if component_label.lower() == "other":
            continue
        included_rows += 1
        cost = parts.parse_amount(raw.get("Preferred Line Cost (AUD)"))
        raw["_series"] = series
        raw["_component"] = component_label
        raw["_category"] = category

        for basis, month in {"CREATED": attrs.get("month", "")}.items():
            if not month:
                continue
            for scope in scope_keys(attrs.get("scope")):
                for month_key in month_keys(month):
                    for series_key in ("ALL", series):
                        bucket_key = (basis, scope, month_key, series_key, component_label, category)
                        bucket = buckets[bucket_key]
                        bucket["tickets"].add(ticket_id)
                        bucket["lineItems"] += 1
                        bucket["cost"] += cost
                        bucket["rows"][ticket_id].append(raw)

    group_keys = defaultdict(list)
    for key, bucket in buckets.items():
        basis, scope, month, series, component, category = key
        group_keys[(basis, scope, month, series)].append({
            "component": component,
            "category": category,
            "tickets": len(bucket["tickets"]),
            "lineItems": bucket["lineItems"],
            "cost": round(bucket["cost"], 3),
            "_bucket": bucket,
        })

    if OUTPUT_GROUPS.exists():
        shutil.rmtree(OUTPUT_GROUPS)
    OUTPUT_GROUPS.mkdir(parents=True, exist_ok=True)

    generated_groups = []
    total_group_bytes = 0
    for group_key, stats in group_keys.items():
        basis, scope, month, series = group_key
        stats.sort(key=lambda item: (-item["tickets"], -item["lineItems"], -item["cost"], item["component"]))
        sheets = []
        for rank, stat in enumerate(stats[:10], start=1):
            bucket = stat.pop("_bucket")
            rows = [
                export_row(rank, stat["component"], stat, ticket_id, lines, ticket_month_scope.get(ticket_id, {}))
                for ticket_id, lines in sorted(bucket["rows"].items(), key=lambda item: item[0])
            ]
            sheets.append({
                "name": sheet_name(rank, stat["component"]),
                "component": stat["component"],
                "category": stat["category"],
                "tickets": stat["tickets"],
                "lineItems": stat["lineItems"],
                "cost": stat["cost"],
                "headers": EXPORT_HEADERS,
                "rows": rows,
            })
        group_payload = {
            "basis": basis,
            "scope": scope,
            "month": month,
            "series": series,
            "sheets": sheets,
        }
        rel_path = Path("groups") / path_part(basis) / path_part(scope) / path_part(month) / f"{path_part(series)}.json"
        out_path = OUTPUT_INDEX.parent / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_text = json.dumps(group_payload, ensure_ascii=False, separators=(",", ":"))
        out_path.write_text(out_text, encoding="utf-8")
        size = out_path.stat().st_size
        total_group_bytes += size
        generated_groups.append({
            "key": "|".join(group_key),
            "path": rel_path.as_posix(),
            "bytes": size,
            "sheets": len(sheets),
            "rows": sum(len(sheet.get("rows", [])) for sheet in sheets),
        })

    payload = {
        "meta": {
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "source": "build_parts_top10_export_payload.py",
            "partsSource": parts.relative_path(parts_csv_path),
            "partsMetaSource": parts.relative_path(parts_meta_path) if parts_meta_path else "",
            "totalRows": total_rows,
            "includedRows": included_rows,
            "groupCount": len(generated_groups),
            "groupBytes": total_group_bytes,
        },
        "groups": generated_groups,
    }
    OUTPUT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_INDEX.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUTPUT_INDEX} ({OUTPUT_INDEX.stat().st_size:,} bytes, {len(generated_groups):,} groups, {total_group_bytes:,} group bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
