"""Build the three screenshot summaries and publish one Firebase REST resource."""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path


API_NODE = "dashboardSummary"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def rows(value):
    # RTDB omits empty arrays and can return arrays as numeric-key objects.
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value[key] for key in sorted(value, key=lambda key: int(key))]
    raise ValueError("Expected an array")


def number(value):
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Expected a finite, non-negative number")
    return result


def count(value):
    result = number(value)
    if not result.is_integer():
        raise ValueError("Expected an integer count")
    return int(result)


def percentage(value, total):
    return round(value / total * 100, 2) if total else 0.0


def build_summary(team, completion):
    """Use the same precomputed sources as index.html and ticketlifecycle.html.

    Keep source timestamps separate: the two upstream jobs can finish at
    different times. Never substitute screenshot constants or local fallbacks.
    """
    view = team["views"]["all"]
    snapshot_date = max(row["date"] for row in rows(view["trend"]))
    date.fromisoformat(snapshot_date)
    month_start = snapshot_date[:7] + "-01"
    # Respect the Dashboard's earliest partial month.
    period_start = max(month_start, team.get("minDate") or month_start)
    period = view["periodSnapshots"][f"{period_start}|{snapshot_date}"]
    year = int(completion["year"])
    stages = {}
    for key in ("approval", "parts"):
        source = completion["stages"][key]
        buckets = rows(source["buckets"])
        total = sum(count(bucket[3]) for bucket in buckets)
        trend = rows(source["trend"])
        if sum(count(row["completed"]) for row in trend) != total:
            raise ValueError(f"Timeline {key}: monthly and duration totals differ")
        over_match = re.search(r"([\d.]+)%", source["overLabel"])
        if not over_match:
            raise ValueError(f"Timeline {key}: missing over-average percentage")
        stages[key] = {
            "label": source["exportLabel"],
            "completedTickets": total,
            "overAveragePercent": number(over_match[1]),
            "monthlyTrend": [{
                "month": f"{year}-{MONTHS.index(row['label']) + 1:02d}",
                "label": row["label"],
                "averageDays": None if row.get("avg") is None else number(row["avg"]),
                "rolling3MonthAverageDays": None if row.get("roll3") is None else number(row["roll3"]),
                "completedTickets": count(row["completed"]),
            } for row in trend],
            "durationBuckets": [{
                "label": bucket[0], "count": count(bucket[3]),
                # Preserve the one-decimal share displayed by the timeline.
                "percent": number(bucket[1]),
            } for bucket in buckets],
        }

    statuses = sorted([{
        "label": row.get("label") or row.get("status") or "Unknown",
        "count": count(row["count"]),
    } for row in rows(view.get("statusMix")) if count(row["count"]) > 0],
        key=lambda row: (-row["count"], row["label"]))
    status_total = sum(row["count"] for row in statuses)
    if status_total != count(view["summary"]["criticalNow"]):
        raise ValueError("Status mix and current critical total differ")
    for row in statuses:
        row["percent"] = percentage(row["count"], status_total)
    chart_statuses = statuses
    # Match renderLists(): group from the sixth status only when there are >6.
    if len(statuses) > 6:
        other_count = sum(row["count"] for row in statuses[5:])
        chart_statuses = statuses[:5] + [{
            "label": "Others", "count": other_count,
            "percent": percentage(other_count, status_total),
            "details": statuses[5:],
        }]

    costs = [{"key": row["key"], "label": row["label"],
              "count": count(row["count"]), "amount": round(number(row["total"]), 2)}
             for row in rows(period["repairCostDistribution"])]
    cost_total = round(sum(row["amount"] for row in costs), 2)
    cost_count = sum(row["count"] for row in costs)
    for row in costs:
        row["ticketPercent"] = percentage(row["count"], cost_count)
        row["amountPercent"] = percentage(row["amount"], cost_total)

    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scope": "all",
        "ticketTimeline": {
            "year": year, "sourceGeneratedAt": completion["generatedAt"],
            "basis": completion["basis"], "unit": "days", "stages": stages,
        },
        "remainingStatusMix": {
            "snapshotDate": snapshot_date, "sourceGeneratedAt": team["generatedAt"],
            "totalTickets": status_total, "percentageBasis": "ticketCount",
            "items": chart_statuses,
        },
        "approvedRepairCostDistribution": {
            "periodStart": period_start, "periodEnd": snapshot_date,
            "sourceGeneratedAt": team["generatedAt"], "currency": "AUD",
            "totalTickets": cost_count, "totalAmount": cost_total,
            "percentageBasis": "amount", "buckets": costs,
        },
    }


def publish_summary(analytics_ref, *, team=None, completion=None):
    """Validate everything before replacing the single summary node atomically."""
    if team is None:
        team = analytics_ref.child("teamDashboard").get()
    if completion is None:
        completion = analytics_ref.child("pageAssets/timeline/completion2026").get()
    if not team or not completion:
        raise ValueError("Dashboard or completion source is missing; summary was not replaced")
    payload = build_summary(team, completion)
    analytics_ref.child(API_NODE).set(payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-json", type=Path, help="Local dashboard input (requires --completion-json)")
    parser.add_argument("--completion-json", type=Path)
    parser.add_argument("--output", type=Path, default=Path("generated_exports/dashboard_summary.json"))
    parser.add_argument("--publish", action="store_true", help="Publish current Firebase sources")
    args = parser.parse_args()
    if bool(args.team_json) != bool(args.completion_json):
        parser.error("Provide both local source files")
    if args.publish and args.team_json:
        parser.error("Publishing uses current Firebase sources, not local files")
    if args.team_json:
        payload = build_summary(json.loads(args.team_json.read_text(encoding="utf-8")),
                                json.loads(args.completion_json.read_text(encoding="utf-8")))
    else:
        from sync_dashboard_assets_to_firebase import DEFAULT_DB_URL, DEFAULT_SA_PATH, DEFAULT_MONITOR_ROOT, init_firebase
        from firebase_admin import db
        init_firebase(DEFAULT_DB_URL, DEFAULT_SA_PATH)
        ref = db.reference(f"{DEFAULT_MONITOR_ROOT}/analytics")
        if args.publish:
            payload = publish_summary(ref)
            print(f"GET {DEFAULT_DB_URL.rstrip('/')}/{DEFAULT_MONITOR_ROOT}/analytics/{API_NODE}.json")
        else:
            payload = build_summary(ref.child("teamDashboard").get(),
                                    ref.child("pageAssets/timeline/completion2026").get())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
