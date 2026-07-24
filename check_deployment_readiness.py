from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = [
    "ctm_v44_history_safe_mandt800_rejection_filter.py",
    "fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py",
    "rebuild_model_series_assets.py",
    "delivery_flow_aggregator.py",
    "export_ticket_timeline_segments_2025_2026.py",
    "sync_dashboard_assets_to_firebase.py",
    "build_parts_classification.mjs",
    "firebase-service-account.json",
    "outputs/parts_classified_meta.json",
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
        probe = path / ".deployment_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, ""
    except Exception as exc:
        return False, str(exc)


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
