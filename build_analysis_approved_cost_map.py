import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "outputs" / "analysis_approved_cost_by_ticket.json"
OUT_JS = ROOT / "outputs" / "analysis_approved_cost_by_ticket.js"
DEFAULT_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app",
)
DEFAULT_MONITOR_ROOT = os.getenv("MONITOR_ROOT", "ctmTicketStatusMonitorV44")


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def fetch_json(url):
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def read_json_if_exists(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def by_ticket_count(payload):
    by_ticket = payload.get("byTicket") if isinstance(payload, dict) else {}
    return len(by_ticket) if isinstance(by_ticket, dict) else 0


def parse_generated_at(payload):
    value = clean(payload.get("generatedAt") if isinstance(payload, dict) else "")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def choose_payload(remote_payload, existing_payload, force_remote=False):
    remote = remote_payload if isinstance(remote_payload, dict) else {}
    existing = existing_payload if isinstance(existing_payload, dict) else {}
    if force_remote:
        return remote, "firebase forced"
    existing_count = by_ticket_count(existing)
    remote_count = by_ticket_count(remote)
    if existing_count and not remote_count:
        return existing, "existing local cache; firebase empty"
    existing_at = parse_generated_at(existing)
    remote_at = parse_generated_at(remote)
    if existing_count and existing_at and remote_at and existing_at >= remote_at:
        return existing, "existing local cache; not older than firebase"
    if remote_count:
        return remote, "firebase latest"
    return existing if existing_count else remote, "existing local cache" if existing_count else "firebase latest"


def write_js_global(path, global_name, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(f"globalThis.{global_name} = {text};\n", encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--firebase-db-url", default=DEFAULT_DB_URL)
    parser.add_argument("--monitor-root", default=DEFAULT_MONITOR_ROOT)
    parser.add_argument("--output", default=str(OUT_JSON))
    parser.add_argument("--js-output", default=str(OUT_JS))
    parser.add_argument("--force-remote", action="store_true", help="Overwrite local approved-cost cache even when it is newer than Firebase.")
    return parser.parse_args()


def main():
    args = parse_args()
    monitor_root = clean(args.monitor_root) or DEFAULT_MONITOR_ROOT
    latest_path = f"{monitor_root}/analytics/approvedCost/sapPoShortText/latest"
    latest_url = f"{clean(args.firebase_db_url).rstrip('/')}/{latest_path}.json"

    output_path = Path(args.output)
    remote_payload = fetch_json(latest_url)
    existing_payload = read_json_if_exists(output_path)
    payload, source_label = choose_payload(remote_payload, existing_payload, args.force_remote)
    if not isinstance(payload, dict):
        payload = {}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_js_global(Path(args.js_output), "ANALYSIS_APPROVED_COST_BY_TICKET", payload)

    by_ticket = payload.get("byTicket") if isinstance(payload, dict) else {}
    ticket_count = len(by_ticket) if isinstance(by_ticket, dict) else 0
    total_amount = 0
    if isinstance(payload.get("summary"), dict):
        total_amount = payload["summary"].get("totalAmount", 0)
    print(f"Wrote approved cost map for {ticket_count} tickets to {output_path} (total={total_amount}, source={source_label})")


if __name__ == "__main__":
    main()
