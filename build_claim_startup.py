"""Build claim charts once per source refresh; publish only a complete generation."""
import json
import subprocess
from pathlib import Path


def publish_claim_startup(analytics_ref, source_ref, tickets, core_version, so_version, *, publish=True):
    from rebuild_model_series_assets import resolve_node_executable
    root = Path(__file__).resolve().parent
    team_version = analytics_ref.child("team/generatedAt").get()
    amounts = analytics_ref.child("teamDashboard/views/all/approvedAmountMonthly").get()
    if isinstance(amounts, dict):
        amounts = list(amounts.values())
    payload = {"tickets": tickets, "amounts": amounts, "teamVersion": team_version,
               "coreVersion": core_version, "soVersion": so_version}
    result = subprocess.run([resolve_node_executable(), str(root / "build_claim_startup.mjs")],
                            input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                            text=True, encoding="utf-8", check=True, timeout=180, cwd=root)
    snapshot = json.loads(result.stdout)
    expected = [team_version, core_version, so_version]
    if (snapshot.get("schema") != "claim-startup-v1" or not snapshot.get("monthly")
            or snapshot.get("generatedAt") != team_version
            or json.loads(snapshot.get("sourceVersion", "null")) != expected):
        raise ValueError("Invalid claim trend snapshot")
    actual = [analytics_ref.child("team/generatedAt").get(), source_ref.child("ticketCoreSyncAt").get(),
              source_ref.child("ticketSoSyncAt").get()]
    if actual != expected:
        raise ValueError("Claim sources changed during calculation; previous snapshot retained")
    if not publish:
        return snapshot
    metadata = {key: snapshot[key] for key in ("schema", "generatedAt", "sourceVersion")}
    analytics_ref.child("claimTrendStartup").set({**metadata, "data": json.dumps(
        {"monthly": snapshot["monthly"], "closedIndex": snapshot["closedIndex"]}, separators=(",", ":"))})
    save_claim_startup(root, snapshot)
    return snapshot


def save_claim_startup(root, snapshot):
    output = root / "outputs/claim_trend_startup.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(output)
