from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"

PARTS_COST_JSON = OUTPUT_DIR / "analysis_parts_ticket_cost_map.json"
PARTS_COST_JS = OUTPUT_DIR / "analysis_parts_ticket_cost_map.js"
APPROVED_COST_JSON = OUTPUT_DIR / "analysis_approved_cost_by_ticket.json"
APPROVED_COST_JS = OUTPUT_DIR / "analysis_approved_cost_by_ticket.js"
REPAIR_SOURCE_CSV = ROOT / "SAPAnalyticsReport_ZF8C06456D7698BCB54F44D_.csv"
REFRESHED_REPAIR_SOURCE_CSV = OUTPUT_DIR / "analysis_ticket_base.csv"
REPAIR_OUTPUT_DIR = OUTPUT_DIR / "repairers_2026"
PARTS_SOURCE_CSV = OUTPUT_DIR / "parts_classification_source.csv"
PARTS_CLASSIFIED_FLAT_CSV = OUTPUT_DIR / "parts_classified.csv"
PARTS_CLASSIFIED_STABLE_META = OUTPUT_DIR / "parts_classified_meta.json"
PARTS_CLASSIFIED_DATA_JS = OUTPUT_DIR / "parts_classified_data.js"
PARTS_CLASSIFICATION_NODE_HEAP_MB = 8192

DEFAULT_FIREBASE_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app",
)
DEFAULT_FIREBASE_SA_PATH = os.getenv(
    "FIREBASE_SA_PATH",
    str(ROOT / "firebase-service-account.json"),
)
DEFAULT_FIREBASE_ROOT = os.getenv("FIREBASE_ROOT", "c4cTickets_test")
DEFAULT_MONITOR_ROOT = os.getenv("MONITOR_ROOT", "ctmTicketStatusMonitorV44")
DEFAULT_SAP_CLIENT = os.getenv("SAP_CLIENT", "800")


logger = logging.getLogger("rebuild_model_series_assets")


def write_js_global(
    path: Path,
    global_name: str,
    payload: Any,
    *,
    is_text: bool = False,
    extra_globals: Optional[Dict[str, Any]] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value_text = json.dumps(payload, ensure_ascii=False) if is_text else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lines = [f"globalThis.{global_name} = {value_text};"]
    for key, value in (extra_globals or {}).items():
        lines.append(f"globalThis.{key} = {json.dumps(value, ensure_ascii=False)};")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def iter_latest_meta_candidates() -> Iterable[Path]:
    seen = set()
    for candidate in [PARTS_CLASSIFIED_STABLE_META, *sorted(OUTPUT_DIR.glob("parts_classification_*/parts_classified_meta.json"), reverse=True)]:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        yield candidate


def load_valid_meta(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    categories = payload.get("categories")
    if not isinstance(categories, list) or not categories:
        return None
    return payload


def resolve_seed_meta_path() -> Path:
    for candidate in iter_latest_meta_candidates():
        if load_valid_meta(candidate) is not None:
            return candidate
    raise FileNotFoundError("No usable parts classification seed meta was found.")


def resolve_node_executable() -> str:
    for env_name in ("NODE_EXE", "CODEX_NODE_PATH"):
        candidate = os.getenv(env_name, "").strip()
        if candidate and Path(candidate).exists():
            return candidate

    which_node = shutil.which("node")
    if which_node:
        return which_node

    candidate = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / ("node.exe" if os.name == "nt" else "node")
    if candidate.exists():
        return str(candidate)

    raise FileNotFoundError("Node.js executable not found. Set NODE_EXE or install Node.")


def run_command(command: list[str], label: str, env: Optional[Dict[str, str]] = None) -> None:
    logger.info("%s: %s", label, " ".join(command))
    subprocess.run(command, check=True, cwd=str(ROOT), env=env)


def sync_page_assets_to_firebase(env: Dict[str, str], args: argparse.Namespace) -> None:
    script = ROOT / "sync_dashboard_assets_to_firebase.py"
    if not script.exists():
        logger.warning("Dashboard page asset Firebase sync skipped. Script not found: %s", script)
        return
    run_command(
        [
            sys.executable,
            str(script),
            "--firebase-db-url",
            args.firebase_db_url,
            "--firebase-sa-path",
            args.firebase_sa_path,
            "--monitor-root",
            args.monitor_root,
        ],
        "Dashboard page asset Firebase sync",
        env=env,
    )


def with_node_heap(env: Dict[str, str], heap_mb: int) -> Dict[str, str]:
    updated = env.copy()
    existing = updated.get("NODE_OPTIONS", "").strip()
    if "--max-old-space-size=" in existing:
        return updated
    heap_option = f"--max-old-space-size={heap_mb}"
    updated["NODE_OPTIONS"] = f"{existing} {heap_option}".strip() if existing else heap_option
    return updated


def dated_parts_output_dir(as_of: date) -> Path:
    return OUTPUT_DIR / f"parts_classification_{as_of.isoformat()}"


def resolve_repair_source_csv() -> Path:
    if REFRESHED_REPAIR_SOURCE_CSV.exists():
        return REFRESHED_REPAIR_SOURCE_CSV
    return REPAIR_SOURCE_CSV


def sync_parts_outputs(parts_output_dir: Path) -> None:
    csv_path = parts_output_dir / "parts_classified.csv"
    meta_path = parts_output_dir / "parts_classified_meta.json"
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing classified CSV: {csv_path}")
    if not meta_path.exists():
        raise FileNotFoundError(f"Missing classified meta: {meta_path}")

    PARTS_CLASSIFIED_FLAT_CSV.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(csv_path, PARTS_CLASSIFIED_FLAT_CSV)
    shutil.copy2(meta_path, PARTS_CLASSIFIED_STABLE_META)


def write_parts_cost_js_fallback() -> None:
    if not PARTS_COST_JSON.exists():
        return
    payload = json.loads(PARTS_COST_JSON.read_text(encoding="utf-8"))
    write_js_global(PARTS_COST_JS, "ANALYSIS_PARTS_TICKET_COST_MAP", payload)


def write_approved_cost_js_fallback() -> None:
    if not APPROVED_COST_JSON.exists():
        return
    payload = json.loads(APPROVED_COST_JSON.read_text(encoding="utf-8"))
    write_js_global(APPROVED_COST_JS, "ANALYSIS_APPROVED_COST_BY_TICKET", payload)


def write_parts_classified_js_fallback() -> None:
    meta_payload = load_valid_meta(PARTS_CLASSIFIED_STABLE_META)
    if not meta_payload:
        return
    csv_path_value = str(meta_payload.get("csvPath") or "").strip()
    if not csv_path_value:
        return
    csv_path = Path(csv_path_value)
    if not csv_path.is_absolute():
        csv_path = (ROOT / csv_path).resolve()
    if not csv_path.exists():
        return
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    write_js_global(
        PARTS_CLASSIFIED_DATA_JS,
        "ANALYSIS_PARTS_CLASSIFIED_CSV_TEXT",
        csv_text,
        is_text=True,
        extra_globals={"ANALYSIS_PARTS_CLASSIFIED_SOURCE": relative_to_root(csv_path)},
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--firebase-db-url", default=DEFAULT_FIREBASE_DB_URL)
    parser.add_argument("--firebase-sa-path", default=DEFAULT_FIREBASE_SA_PATH)
    parser.add_argument("--firebase-root", default=DEFAULT_FIREBASE_ROOT)
    parser.add_argument("--monitor-root", default=DEFAULT_MONITOR_ROOT)
    parser.add_argument("--sap-client", default=DEFAULT_SAP_CLIENT)
    parser.add_argument("--parts-as-of", default=date.today().isoformat(), help="YYYY-MM-DD folder suffix for parts classification outputs.")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--no-ai", action="store_true", help="Disable AI-assisted parts classification and use history/rules only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(asctime)s - %(levelname)s - %(message)s")

    env = os.environ.copy()
    env["FIREBASE_DB_URL"] = args.firebase_db_url
    env["FIREBASE_SA_PATH"] = args.firebase_sa_path
    env["FIREBASE_ROOT"] = args.firebase_root
    env["MONITOR_ROOT"] = args.monitor_root
    env["SAP_CLIENT"] = args.sap_client

    parts_output_dir = dated_parts_output_dir(date.fromisoformat(args.parts_as_of))
    seed_meta_path = resolve_seed_meta_path()
    node_exe = resolve_node_executable()

    logger.info("Rebuilding model-series assets with parts output dir: %s", parts_output_dir)
    logger.info("Parts seed meta: %s", seed_meta_path)

    run_command(
        [
            sys.executable,
            "export_vehicle_failure_timing_2025_2026.py",
            "--firebase-db-url",
            args.firebase_db_url,
            "--firebase-sa-path",
            args.firebase_sa_path,
            "--firebase-root",
            args.firebase_root,
            "--log-level",
            args.log_level,
        ],
        "Failure timing export",
        env=env,
    )

    run_command(
        [
            sys.executable,
            "build_analysis_parts_ticket_cost_map.py",
            "--firebase-db-url",
            args.firebase_db_url,
            "--firebase-root",
            args.firebase_root,
        ],
        "Parts ticket cost map",
        env=env,
    )

    run_command(
        [
            sys.executable,
            "build_analysis_approved_cost_map.py",
            "--firebase-db-url",
            args.firebase_db_url,
            "--monitor-root",
            args.monitor_root,
            "--output",
            str(APPROVED_COST_JSON),
            "--js-output",
            str(APPROVED_COST_JS),
        ],
        "Approved cost map",
        env=env,
    )

    repair_source_csv = resolve_repair_source_csv()
    logger.info("Repair source CSV: %s", repair_source_csv)
    run_command(
        [
            sys.executable,
            "extract_repairs_2026.py",
            "--source",
            str(repair_source_csv),
            "--output-dir",
            str(REPAIR_OUTPUT_DIR),
            "--mandt",
            args.sap_client,
            "--firebase-root",
            args.firebase_root,
        ],
        "Repair analysis export",
        env=env,
    )

    run_command(
        [
            sys.executable,
            "export_parts_classification_source.py",
            "--firebase-db-url",
            args.firebase_db_url,
            "--firebase-sa-path",
            args.firebase_sa_path,
            "--firebase-root",
            args.firebase_root,
            "--output",
            str(PARTS_SOURCE_CSV),
            "--log-level",
            args.log_level,
        ],
        "Parts classification source export",
        env=env,
    )

    classification_command = [
        node_exe,
        "build_parts_classification.mjs",
        "--input",
        str(PARTS_SOURCE_CSV),
        "--seed-meta",
        str(seed_meta_path),
        "--output-dir",
        str(parts_output_dir),
    ]
    if args.no_ai:
        classification_command.append("--no-ai")
    classification_env = with_node_heap(env, PARTS_CLASSIFICATION_NODE_HEAP_MB)
    run_command(classification_command, "Parts classification build", env=classification_env)

    sync_parts_outputs(parts_output_dir)
    run_command([sys.executable, "build_analysis_parts_failure_summary.py"], "Parts failure summary build", env=env)
    run_command([sys.executable, "export_ticket_timeline_segments_2025_2026.py"], "Ticket timeline export", env=env)

    write_parts_cost_js_fallback()
    write_approved_cost_js_fallback()
    write_parts_classified_js_fallback()
    sync_page_assets_to_firebase(env, args)
    logger.info("Model-series assets rebuild completed successfully, including repair, parts, and approved-cost outputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
