"""Precompute the initial dashboard once per analytics refresh, for all devices."""
import json
import subprocess
from pathlib import Path


def publish_dashboard_startup(analytics_ref, team, employee, employee_directory):
    from rebuild_model_series_assets import resolve_node_executable
    root = Path(__file__).resolve().parent
    result = subprocess.run(
        [resolve_node_executable(), str(root / "build_dashboard_startup.mjs")],
        input=json.dumps({"team": team, "employee": employee, "employeeDirectory": employee_directory}, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=180, cwd=root,
    )
    snapshot = json.loads(result.stdout)
    if snapshot.get("schema") != "team-startup-v1" or snapshot.get("generatedAt") != team["generatedAt"]:
        raise ValueError("Invalid dashboard startup snapshot")
    save_local_startup(root, snapshot)
    # Atomic replacement: readers see a complete old or complete new page.
    # Do not sanitize its JSON-string cache keys; store the page as a JSON string
    # so Firebase cannot rewrite calculation arguments or turn arrays into maps.
    payload = {**snapshot, "page": json.dumps(snapshot["page"], ensure_ascii=False, separators=(",", ":"))}
    analytics_ref.child("teamStartup").set(payload)
    return snapshot


def save_local_startup(root, snapshot):
    output = root / "outputs" / "team_dashboard_startup.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({**snapshot, "sourceVersion": snapshot["generatedAt"]}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(output)
