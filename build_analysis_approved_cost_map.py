import argparse
import json
import os
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
    return parser.parse_args()


def main():
    args = parse_args()
    monitor_root = clean(args.monitor_root) or DEFAULT_MONITOR_ROOT
    latest_path = f"{monitor_root}/analytics/approvedCost/sapPoShortText/latest"
    latest_url = f"{clean(args.firebase_db_url).rstrip('/')}/{latest_path}.json"

    payload = fetch_json(latest_url)
    if not isinstance(payload, dict):
        payload = {}

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_js_global(Path(args.js_output), "ANALYSIS_APPROVED_COST_BY_TICKET", payload)

    by_ticket = payload.get("byTicket") if isinstance(payload, dict) else {}
    ticket_count = len(by_ticket) if isinstance(by_ticket, dict) else 0
    total_amount = 0
    if isinstance(payload.get("summary"), dict):
        total_amount = payload["summary"].get("totalAmount", 0)
    print(f"Wrote approved cost map for {ticket_count} tickets to {output_path} (total={total_amount})")


if __name__ == "__main__":
    main()
