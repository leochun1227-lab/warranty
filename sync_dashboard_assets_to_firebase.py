from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, db


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app",
)
DEFAULT_SA_PATH = os.getenv("FIREBASE_SA_PATH", str(ROOT / "firebase-service-account.json"))
DEFAULT_MONITOR_ROOT = os.getenv("MONITOR_ROOT", "ctmTicketStatusMonitorV44")


ASSETS = {
    "timeline/summaryLatest": ROOT / "generated_exports" / "ticket_timeline_summary.json",
    "timeline/summary2026": ROOT / "generated_exports" / "ticket_timeline_summary_2026.json",
    "timeline/completion2026": ROOT / "generated_exports" / "ticket_timeline_completion_analytics_2026.json",
    "modelSeries/partsFailureSummary": ROOT / "outputs" / "analysis_parts_failure_summary.json",
}


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def init_firebase(db_url: str, sa_path: str) -> None:
    if firebase_admin._apps:
        return
    if not Path(sa_path).exists():
        raise SystemExit(f"Firebase service account not found: {sa_path}")
    firebase_admin.initialize_app(credentials.Certificate(sa_path), {"databaseURL": db_url})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--firebase-db-url", default=DEFAULT_DB_URL)
    parser.add_argument("--firebase-sa-path", default=DEFAULT_SA_PATH)
    parser.add_argument("--monitor-root", default=DEFAULT_MONITOR_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    monitor_root = clean(args.monitor_root) or DEFAULT_MONITOR_ROOT
    init_firebase(clean(args.firebase_db_url) or DEFAULT_DB_URL, clean(args.firebase_sa_path) or DEFAULT_SA_PATH)

    base_ref = db.reference(f"{monitor_root}/analytics/pageAssets")
    uploaded = []
    for asset_key, path in ASSETS.items():
        info = {"key": asset_key}
        if not path.exists():
            info.update({"ok": False, "reason": "missing", "path": str(path)})
            uploaded.append(info)
            continue
        payload = load_json(path)
        base_ref.child(asset_key).set(payload)
        info.update({
            "ok": True,
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "uploadedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        })
        uploaded.append(info)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    meta = {
        "generatedAt": generated_at,
        "source": "sync_dashboard_assets_to_firebase.py",
        "assets": uploaded,
    }
    base_ref.child("meta").set(meta)
    db.reference(f"{monitor_root}/analytics/meta").update({
        "generatedAt": generated_at,
        "pageAssetsSyncedAt": generated_at,
    })
    print(f"Synced dashboard page assets to {monitor_root}/analytics/pageAssets")
    for info in uploaded:
        print(f"- {info.get('key')}: {'ok' if info.get('ok') else info.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
