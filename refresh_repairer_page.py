import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent

FETCH_SCRIPT = ROOT / "fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py"
SAP_SOURCE_SCRIPT = ROOT / "sap_authoritative_repair_payments.py"
SAP_WORKBOOK_SCRIPT = ROOT / "build_sap_authoritative_repair_payments.mjs"
SAP_TO_REPAIRERS_SCRIPT = ROOT / "build_repairers_from_sap_authoritative.py"
FAST_CACHE_SCRIPT = ROOT / "build_repairers_fast_cache.mjs"
WORKBOOK_SCRIPT = ROOT / "build_repairers_2026_workbook.mjs"


def run_step(label: str, command: list[str]) -> None:
    print(f"\n=== {label} ===")
    print(" ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode != 0:
        raise SystemExit(f"{label} failed with exit code {completed.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh everything needed by the repairer page."
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip the main C4C/SAP fetch step if analysis_ticket_base.csv is already refreshed.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2025,
        help="Deprecated; repairer analysis date scope now comes from SAP PO document date.",
    )
    parser.add_argument(
        "--skip-workbook",
        action="store_true",
        help="Skip rebuilding repairers_2026_analysis_state.xlsx.",
    )
    args = parser.parse_args()

    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js was not found in PATH. Please install Node or open a shell where node works.")

    if not args.skip_fetch:
        run_step(
            "Step 1 - Refresh analysis_ticket_base.csv and Firebase snapshot",
            [sys.executable, str(FETCH_SCRIPT)],
        )

    run_step(
        "Step 2 - Build C4C-eligible SAP authoritative repair PO source",
        [sys.executable, str(SAP_SOURCE_SCRIPT)],
    )

    run_step(
        "Step 3 - Build SAP authoritative audit workbook",
        [node, "--max-old-space-size=8192", str(SAP_WORKBOOK_SCRIPT)],
    )

    run_step(
        "Step 4 - Convert SAP authoritative source into repairer page JSON",
        [sys.executable, str(SAP_TO_REPAIRERS_SCRIPT)],
    )

    run_step(
        "Step 5 - Rebuild repairer fast/light cache",
        [node, str(FAST_CACHE_SCRIPT)],
    )

    if not args.skip_workbook:
        run_step(
            "Step 6 - Rebuild repairer workbook",
            [node, str(WORKBOOK_SCRIPT)],
        )

    print("\nRepairer page refresh completed.")
    print("Updated outputs:")
    print("- outputs/analysis_ticket_base.csv")
    print("- outputs/repairers_2026/sap_authoritative_repair_payments.json")
    print("- outputs/repairers_2026/sap_authoritative_repair_payments.xlsx")
    print("- outputs/repairers_2026/repairers_2026_data.json")
    print("- outputs/repairers_2026/repairers_2026_data.js")
    print("- outputs/repairers_2026/repairers_2026_fast.json")
    print("- outputs/repairers_2026/repairers_2026_light.json")
    if not args.skip_workbook:
        print("- outputs/repairers_2026/repairers_2026_analysis_state.xlsx")


if __name__ == "__main__":
    main()
