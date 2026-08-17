from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = [
    "ctm_v44_history_safe_mandt800_rejection_filter.py",
    "fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py",
    "rebuild_model_series_assets.py",
    "build_analysis_parts_failure_summary.py",
    "delivery_flow_aggregator.py",
    "export_ticket_timeline_segments_2025_2026.py",
    "sync_dashboard_assets_to_firebase.py",
    "build_parts_classification.mjs",
    "repairs.html",
    "infieldpredelivery.html",
    "firebase-service-account.json",
    "outputs/parts_classified_meta.json",
    "outputs/analysis_parts_failure_light.json",
    "outputs/analysis_parts_derived_cache.json",
]

REQUIRED_MODULES = [
    ("requests", "requests"),
    ("urllib3", "urllib3"),
    ("pandas", "pandas"),
    ("pyodbc", "pyodbc"),
    ("firebase_admin", "firebase-admin"),
    ("openpyxl", "openpyxl"),
]


def has_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def node_executable() -> str:
    for env_name in ("NODE_EXE", "CODEX_NODE_PATH"):
        candidate = os.getenv(env_name, "").strip()
        if candidate and Path(candidate).exists():
            return candidate
    return shutil.which("node") or ""


def check_writable_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / "_deployment_write_test.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def check_repair_page_contract(failures: list[str]) -> None:
    path = ROOT / "repairs.html"
    if not path.exists():
        return
    text = read_text(path)
    required_snippets = [
        'browser-page-cache.js?v=repairer-name-map-v16',
        'c4c-eligible-sap-po-authoritative-repairer-name-map-v16',
        'REPAIR_CLAIM_TREND_LIVE_APPROVAL_CLOSED_START="2026-06"',
        'REPAIR_CLAIM_TREND_HISTORICAL_UNAPPROVED',
        '"2026-01":{inField:517,preDelivery:67}',
        '"2026-05":{inField:235,preDelivery:22}',
        "repairApprovedPlusUnapprovedTicketTotal",
        "Total tickets QTY (approved+unapproved)",
    ]
    missing = [snippet for snippet in required_snippets if snippet not in text]
    if missing:
        failures.append("repairs.html is missing the latest repeated-repair denominator contract: " + ", ".join(missing))
        print("FAIL repairs.html repeated-repair denominator contract")
    else:
        print("PASS repairs.html repeated-repair denominator contract")


def check_claim_trend_contract(failures: list[str]) -> None:
    path = ROOT / "infieldpredelivery.html"
    if not path.exists():
        return
    text = read_text(path)
    required_snippets = [
        'const LIVE_APPROVAL_CLOSED_START="2026-06"',
        '"2026-01":{createdIn:586,createdPre:123,approvedIn:340,approvedPre:100,unapprovedIn:517,unapprovedPre:67}',
        '"2026-05":{createdIn:782,createdPre:208,approvedIn:1080,approvedPre:245,unapprovedIn:235,unapprovedPre:22}',
        'arr(view.approvalClosedMonthly).forEach',
    ]
    missing = [snippet for snippet in required_snippets if snippet not in text]
    if missing:
        failures.append("infieldpredelivery.html Claim Trend historical/live unapproved contract changed or is missing: " + ", ".join(missing))
        print("FAIL Claim Trend unapproved contract")
    else:
        print("PASS Claim Trend unapproved contract")


def check_repairer_output_consistency(failures: list[str], warnings: list[str]) -> None:
    path = ROOT / "outputs" / "repairers_2026" / "repairers_2026_fast.json"
    if not path.exists():
        warnings.append("repairers_2026_fast.json not found; skipping repairer output consistency check.")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        failures.append(f"Could not parse repairers_2026_fast.json: {exc}")
        print(f"FAIL repairer output consistency: {exc}")
        return
    summary_total = int((payload.get("summary") or {}).get("total_tickets") or 0)
    state_sum = sum(int((row or {}).get("ticket_count") or 0) for row in payload.get("states") or [])
    if summary_total and state_sum and summary_total != state_sum:
        failures.append(f"Repairer approved-cost total mismatch: summary.total_tickets={summary_total}, states sum={state_sum}.")
        print("FAIL repairer output consistency")
    else:
        print(f"PASS repairer output consistency: approved-cost tickets {summary_total or state_sum}")


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    print("Deployment readiness check")
    print(f"Root: {ROOT}")
    print(f"Python: {sys.executable}")

    for rel_path in REQUIRED_FILES:
        path = ROOT / rel_path
        if path.exists():
            print(f"PASS file: {rel_path}")
        else:
            failures.append(f"Missing required file: {rel_path}")
            print(f"FAIL file: {rel_path}")

    for module_name, package_name in REQUIRED_MODULES:
        if has_module(module_name):
            print(f"PASS python module: {module_name}")
        else:
            failures.append(f"Missing Python package: {package_name}")
            print(f"FAIL python module: {module_name}")

    node = node_executable()
    if node:
        print(f"PASS node: {node}")
    else:
        failures.append("Node.js executable not found. Install Node.js or set NODE_EXE.")
        print("FAIL node: not found")

    if has_module("pyodbc"):
        try:
            import pyodbc

            drivers = [str(driver) for driver in pyodbc.drivers()]
            has_hana_driver = any("HDBODBC" in driver.upper() or "HANA" in driver.upper() for driver in drivers)
            if has_hana_driver:
                print("PASS ODBC: SAP HANA driver found")
            else:
                failures.append("SAP HANA ODBC driver was not found in pyodbc.drivers(). Install SAP HANA client/driver.")
                print("FAIL ODBC: SAP HANA driver not found")
        except Exception as exc:
            failures.append(f"Could not inspect ODBC drivers: {exc}")
            print(f"FAIL ODBC: {exc}")

    check_repair_page_contract(failures)
    check_claim_trend_contract(failures)
    check_repairer_output_consistency(failures, warnings)

    for rel_dir in ("logs", "outputs", "generated_exports"):
        ok, err = check_writable_dir(ROOT / rel_dir)
        if ok:
            print(f"PASS writable: {rel_dir}")
        else:
            failures.append(f"Directory is not writable: {rel_dir} ({err})")
            print(f"FAIL writable: {rel_dir}")

    firebase_key = ROOT / "firebase-service-account.json"
    if firebase_key.exists() and firebase_key.stat().st_size < 100:
        warnings.append("firebase-service-account.json exists but looks unusually small.")

    if warnings:
        print("")
        print("Warnings:")
        for item in warnings:
            print(f"  WARN {item}")

    if failures:
        print("")
        print("Readiness check failed:")
        for item in failures:
            print(f"  FAIL {item}")
        return 1

    print("")
    print("Readiness check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
