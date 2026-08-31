from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import build_analysis_parts_failure_summary as parts


ROOT = Path(__file__).resolve().parent
DERIVED = ROOT / "outputs" / "analysis_parts_derived_cache.json"
OUTPUT_INDEX = ROOT / "outputs" / "parts_fast_view_payload" / "index.json"
OUTPUT_GROUPS = ROOT / "outputs" / "parts_fast_view_payload" / "groups"
PARTS_REPORT_START_YEAR = 2025
SCOPES = ("ALL", "PRE", "FIELD")


def clean(value):
    return parts.clean(value)


def path_part(value):
    text = clean(value) or "blank"
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)


def title_case_component(value):
    return parts.title_case(clean(value))


def category_label(value):
    aliases = {
        "lighting / reflectors": "Lighting / Reflectors",
        "windows / hatches / blinds": "Windows / Hatches / Blinds",
        "electrical / power / electronics": "Electrical / Power / Electronics",
        "doors / hatches": "Doors / Hatches",
        "chassis / wheels / towing": "Chassis / Wheels / Towing",
        "hardware / installation": "Hardware / Installation",
        "furniture / interior": "Furniture / Interior",
        "appliances / hvac / gas": "Appliances / HVAC / Gas",
        "body / exterior trim": "Body / Exterior Trim",
        "water / plumbing / kitchen / bath": "Water / Plumbing / Kitchen / Bath",
        "awning / shade": "Awning / Shade",
        "storage / toolbox": "Storage / Toolbox",
        "other": "Other",
    }
    normalized = " ".join(clean(value).lower().replace("/", " / ").split())
    return aliases.get(normalized, parts.title_case(clean(value) or "Other"))


def month_matches(month_key, selection):
    month = clean(month_key)
    selected = clean(selection)
    if not month:
        return False
    if selected == "ALL":
        return True
    if len(selected) == 4 and selected.isdigit():
        return month.startswith(f"{selected}-")
    return month == selected


def month_label(month):
    return parts.month_label(month)


def month_payload(month, bucket, scope):
    if scope == "ALL":
        tickets = int(bucket.get("tickets") or 0)
        line_items = int(bucket.get("lineItems") or 0)
        cost = float(bucket.get("cost") or 0)
    else:
        tickets = int((bucket.get("scope") or {}).get(scope) or 0)
        line_items = tickets
        cost = float((bucket.get("scopeCost") or {}).get(scope) or 0)
    label = month_label(month)
    return {
        "month": month,
        "label": label,
        "shortLabel": label.rsplit(" ", 1)[0] if " " in label else label,
        "year": int(month[:4]) if len(month) >= 4 and month[:4].isdigit() else "",
        "tickets": tickets,
        "lineItems": line_items,
        "cost": round(cost, 3),
    }


def series_bucket(entry, basis, series):
    bases = entry.get("bases") or {}
    basis_bucket = bases.get(basis) or {}
    series_map = basis_bucket.get("series") or {}
    return series_map.get(series)


def component_key(component, category):
    return f"{clean(component).lower()}||{clean(category).lower()}"


def group_payload(entries, basis, scope, selection, series):
    rows = []
    for item in entries:
        bucket = series_bucket(item["entry"], basis, series)
        if not bucket:
            continue
        months = bucket.get("months") or {}
        selected_months = [
            (month, month_bucket)
            for month, month_bucket in months.items()
            if month_matches(month, selection)
        ]
        if not selected_months:
            continue
        trend_rows = [
            month_payload(month, month_bucket, scope)
            for month, month_bucket in sorted(selected_months, key=lambda pair: pair[0])
        ]
        tickets = sum(row["tickets"] for row in trend_rows)
        if tickets <= 0:
            continue
        line_items = sum(row["lineItems"] for row in trend_rows)
        cost = sum(row["cost"] for row in trend_rows)
        rows.append({
            "key": component_key(item["component"], item["category"]),
            "component": item["component"],
            "category": item["category"],
            "tickets": tickets,
            "lineItems": line_items or tickets,
            "cost": round(cost, 3),
            "trendRows": trend_rows[-20:],
        })
    rows.sort(key=lambda row: (-row["tickets"], -row["lineItems"], -row["cost"], row["component"]))
    top = rows[:10]
    total_tickets = sum(row["tickets"] for row in top)
    total_lines = sum(row["lineItems"] for row in top)
    total_cost = sum(row["cost"] for row in top)
    for row in top:
        row["ticketShare"] = round(row["tickets"] / total_tickets, 6) if total_tickets else 0
        row["lineShare"] = round(row["lineItems"] / total_lines, 6) if total_lines else 0
        row["costShare"] = round(row["cost"] / total_cost, 6) if total_cost else 0
    return {
        "basis": basis,
        "scope": scope,
        "month": selection,
        "series": series,
        "totals": {
            "tickets": total_tickets,
            "lineItems": total_lines,
            "cost": round(total_cost, 3),
            "components": len(top),
        },
        "topComponents": top,
    }


def main() -> int:
    payload = json.loads(DERIVED.read_text(encoding="utf-8"))
    by_key = payload.get("byKey") or {}
    entries = []
    months = set()
    series = {"ALL"}
    for key, entry in by_key.items():
        raw_component, _, raw_category = key.partition("||")
        component = title_case_component(raw_component)
        category = category_label(raw_category or "Other")
        if not component or component.lower() == "other":
            continue
        component, category = parts.normalize_component_classification(component, category, "")
        if clean(component).lower() == "other":
            continue
        entries.append({"component": component, "category": category, "entry": entry})
        created = ((entry.get("bases") or {}).get("CREATED") or {}).get("series") or {}
        series.update(created.keys())
        all_bucket = created.get("ALL") or {}
        for month in (all_bucket.get("months") or {}).keys():
            if len(month) >= 7 and month[:4].isdigit() and int(month[:4]) >= PARTS_REPORT_START_YEAR:
                months.add(month[:7])

    month_options = ["ALL"]
    year_options = sorted({month[:4] for month in months}, reverse=True)
    month_options.extend(year_options)
    month_options.extend(sorted(months, reverse=True))
    series_options = ["ALL", *[code for code in parts.SERIES_ORDER if code in series]]

    if OUTPUT_GROUPS.exists():
        shutil.rmtree(OUTPUT_GROUPS)
    OUTPUT_GROUPS.mkdir(parents=True, exist_ok=True)

    groups = []
    total_bytes = 0
    for basis in ("CREATED",):
        for scope in SCOPES:
            for selection in month_options:
                for series_key in series_options:
                    group = group_payload(entries, basis, scope, selection, series_key)
                    if not group["topComponents"]:
                        continue
                    rel_path = Path("groups") / path_part(basis) / path_part(scope) / path_part(selection) / f"{path_part(series_key)}.json"
                    out_path = OUTPUT_INDEX.parent / rel_path
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_text = json.dumps(group, ensure_ascii=False, separators=(",", ":"))
                    out_path.write_text(out_text, encoding="utf-8")
                    size = out_path.stat().st_size
                    total_bytes += size
                    groups.append({
                        "key": "|".join([basis, scope, selection, series_key]),
                        "path": rel_path.as_posix(),
                        "bytes": size,
                        "components": len(group["topComponents"]),
                    })

    index = {
        "meta": {
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "source": "build_parts_fast_view_payload.py",
            "derivedSource": DERIVED.relative_to(ROOT).as_posix(),
            "groupCount": len(groups),
            "groupBytes": total_bytes,
            "currentYear": date.today().year,
        },
        "monthOptions": month_options,
        "seriesOptions": series_options,
        "scopes": list(SCOPES),
        "groups": groups,
    }
    OUTPUT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_INDEX.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUTPUT_INDEX} ({OUTPUT_INDEX.stat().st_size:,} bytes, {len(groups):,} groups, {total_bytes:,} group bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
