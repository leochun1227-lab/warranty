"""Publish a complete, versioned delivery page after each successful aggregation."""
import json
import subprocess
from pathlib import Path


def publish_delivery_startup(history_ref, tickets, history_version, ticket_version, read_ticket_version,
                             *, publish=True, use_local_summary=True):
    from rebuild_model_series_assets import resolve_node_executable
    root = Path(__file__).resolve().parent
    summary_path = root / "outputs/delivery_flow_current_summary.json"
    payload = {
        "history": history_ref.child("daily").get(), "tickets": tickets,
        "historyVersion": history_version, "ticketVersion": ticket_version,
        "summary": json.loads(summary_path.read_text(encoding="utf-8")) if use_local_summary and summary_path.exists() else None,
    }
    result = subprocess.run(
        [resolve_node_executable(), str(root / "build_delivery_startup.mjs")],
        input=json.dumps(payload, ensure_ascii=False), capture_output=True,
        text=True, encoding="utf-8", check=True, timeout=180, cwd=root,
    )
    snapshot = json.loads(result.stdout)
    expected_version = f"{history_version}|{ticket_version}"
    if (snapshot.get("schema") != "delivery-startup-v1"
            or snapshot.get("sourceVersion") != expected_version
            or snapshot.get("generatedAt") != history_version
            or not snapshot.get("page", {}).get("history")):
        raise ValueError("Invalid delivery startup snapshot")
    if history_ref.child("latestSyncAt").get() != history_version or read_ticket_version() != ticket_version:
        raise ValueError("Delivery sources changed during calculation; snapshot was not replaced")
    if not publish:
        return snapshot
    # One atomic write. The JSON string preserves array shapes and numeric keys.
    history_ref.child("startup").set({**snapshot, "page": json.dumps(snapshot["page"], ensure_ascii=False, separators=(",", ":"))})
    save_delivery_startup(root, snapshot)
    return snapshot


def save_delivery_startup(root, snapshot):
    output = root / "outputs/delivery_flow_startup.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(output)
