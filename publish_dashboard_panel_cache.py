"""Backfill the two dashboard panel caches from existing Firebase analytics.

Normal daily refreshes publish these through publish_dashboard_startup().
This command updates only teamPanelCache; it never changes source tickets.
"""
import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

from build_dashboard_startup import panel_cache_payload

ROOT = Path(__file__).resolve().parent


def publish_existing_panel_cache(analytics_ref, employee_directory):
    from rebuild_model_series_assets import resolve_node_executable

    team = analytics_ref.child("teamDashboard").get()
    generation = (team or {}).get("generatedAt")
    if not generation or analytics_ref.child("team/generatedAt").get() != generation:
        raise ValueError("Team analytics are still updating; retry after the refresh")
    team.pop("dashboardPanelCache", None)
    jobs = []
    for key, view in team["views"].items():
        dates = sorted(row["date"] for row in (view.get("trend") or []) if row.get("date"))
        latest = dates[-1] if dates else generation[:10]
        months = {date[:7] for date in dates}
        months.update(period[:7] for period in (view.get("periodSnapshots") or {}) if period[7:10] == "-01")
        for month in sorted(months):
            year, number = map(int, month.split("-"))
            end = min(f"{month}-{calendar.monthrange(year, number)[1]:02d}", latest)
            if month > latest[:7] or end == latest:
                continue  # The current statusMix is already in teamDashboard.
            jobs.append((key, end))

    def read_day(job):
        key, end = job
        return key, end, analytics_ref.child(f"team/views/{key}/dailyCriticalRows/{end}").get()

    with ThreadPoolExecutor(max_workers=4) as pool:
        for key, end, rows in pool.map(read_day, jobs):
            view = team["views"][key]
            if rows is not None:
                view.setdefault("dailyCriticalRows", {})[end] = rows
            elif any(row.get("date") == end and row.get("critical", row.get("criticalNow")) == 0 for row in view.get("trend", [])):
                view.setdefault("dailyCriticalRows", {})[end] = []

    result = subprocess.run(
        [resolve_node_executable(), str(ROOT / "build_dashboard_startup.mjs"), "--panels-only"],
        input=json.dumps({"team": team, "employee": {}, "employeeDirectory": employee_directory}, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=180, cwd=ROOT,
    )
    panels = json.loads(result.stdout)
    if panels.get("schema") != 1 or panels.get("generatedAt") != generation:
        raise ValueError("Invalid panel calculation result")
    if any(analytics_ref.child(path).get() != generation for path in ("team/generatedAt", "teamDashboard/generatedAt")):
        raise ValueError("Team analytics changed during calculation; cache was not published")
    analytics_ref.child("teamPanelCache").set(panel_cache_payload(panels))
    return panels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", required=True)
    parser.parse_args()
    from sync_dashboard_assets_to_firebase import DEFAULT_DB_URL, DEFAULT_SA_PATH, DEFAULT_MONITOR_ROOT, init_firebase
    from firebase_admin import db
    init_firebase(DEFAULT_DB_URL, DEFAULT_SA_PATH)
    root = db.reference(DEFAULT_MONITOR_ROOT)
    panels = publish_existing_panel_cache(root.child("analytics"), root.child("employeeDirectory").get() or {})
    output = ROOT / "outputs" / "team_panel_cache.json"
    output.write_text(json.dumps(panels, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"published": "analytics/teamPanelCache", "generatedAt": panels["generatedAt"],
                      "views": {key: {"approvalMonths": len(value["monthlyApprovals"]["columns"]),
                                      "statusMonths": len(value["remainingStatus"])} for key, value in panels["views"].items()}}, indent=2))


if __name__ == "__main__":
    main()
