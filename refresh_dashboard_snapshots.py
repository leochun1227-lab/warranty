"""Check or repair dashboard publications from existing Firebase data.

Default: read-only checks. --dry-run builds and validates all three snapshots
without publishing; --publish repairs them without fetching SAP/C4C again.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_URL = "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app"


class ReadReference:
    """Use the same public REST reads as the pages; never expose credentials."""
    def __init__(self, base, path=""):
        self.base, self.path = base.rstrip("/"), path.strip("/")

    def child(self, path):
        return ReadReference(self.base, f"{self.path}/{path}")

    def get(self):
        request = urllib.request.Request(f"{self.base}/{self.path}.json", headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response)


def read_versions(analytics, source):
    refs = {
        "team": analytics.child("team/generatedAt"),
        "core": source.child("ticketCoreSyncAt"),
        "so": source.child("ticketSoSyncAt"),
        "history": source.child("deliveryFlowHistory/latestSyncAt"),
        "model": analytics.child("pageAssets/modelSeries/modelMtmCache/generatedAt"),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        return dict(zip(refs, pool.map(lambda ref: ref.get(), refs.values())))


def publication_errors(versions, claim, delivery, model):
    errors = []
    for name, version in versions.items():
        if not isinstance(version, str) or not version:
            errors.append(f"Missing source version: {name}")
    try:
        claim_versions = json.loads((claim or {}).get("sourceVersion", "null"))
    except (ValueError, TypeError):
        claim_versions = None
    if ((claim or {}).get("schema") != "claim-startup-v1"
            or claim_versions != [versions["team"], versions["core"], versions["so"]]):
        errors.append("Claim Trend Overview: snapshot missing or behind source data")
    if ((delivery or {}).get("schema") != "delivery-startup-v1"
            or delivery.get("sourceVersion") != f'{versions["history"]}|{versions["so"]}'):
        errors.append("Parts Delivery: snapshot missing or behind source data")
    if ((model or {}).get("deliverySchema") != "model-summary-v1"
            or model.get("generatedAt") != versions["model"]):
        errors.append("Model-wise Distribution: summary missing or behind published model data")
    return errors


def check_publications(analytics, source):
    versions = read_versions(analytics, source)
    # Read metadata only, not ticket details or the large legacy model cache.
    def metadata(ref, keys):
        return {key: ref.child(key).get() for key in keys}
    claim = metadata(analytics.child("claimTrendStartup"), ("schema", "sourceVersion"))
    delivery = metadata(source.child("deliveryFlowHistory/startup"), ("schema", "sourceVersion"))
    model = metadata(analytics.child("pageAssets/modelSeries/modelMtmSummary"), ("deliverySchema", "generatedAt"))
    errors = publication_errors(versions, claim, delivery, model)
    print("Source versions: " + json.dumps(versions))
    for error in errors:
        print("FAIL " + error)
    if not errors:
        print("PASS all three dashboard publications match their source generations")
    return errors


def rebuild(analytics, source, *, publish=False):
    from build_claim_startup import publish_claim_startup, save_claim_startup
    from build_delivery_startup import publish_delivery_startup, save_delivery_startup
    from rebuild_model_series_assets import resolve_node_executable
    from sync_dashboard_assets_to_firebase import publish_model_snapshot

    versions = read_versions(analytics, source)
    if not all(isinstance(value, str) and value for value in versions.values()):
        raise ValueError("Source versions are incomplete; run the daily refresh first")
    tickets = source.child("tickets").get()
    if not tickets:
        raise ValueError("Ticket source is empty")
    print("Building Claim Trend Overview from the existing ticket generation", flush=True)
    claim = publish_claim_startup(analytics, source, tickets, versions["core"], versions["so"], publish=False)
    print("Building Parts Delivery from existing history and tickets", flush=True)
    delivery = publish_delivery_startup(source.child("deliveryFlowHistory"), tickets,
        versions["history"], versions["so"], source.child("ticketSoSyncAt").get,
        publish=False, use_local_summary=False)
    del tickets
    print("Building Model-wise Distribution from the published full model cache", flush=True)
    legacy = analytics.child("pageAssets/modelSeries/modelMtmCache").get()
    if not legacy or legacy.get("generatedAt") != versions["model"]:
        raise ValueError("Model source changed or is missing; previous publications retained")
    with tempfile.TemporaryDirectory(prefix="warranty-snapshots-") as folder:
        output = Path(folder)
        subprocess.run([resolve_node_executable(), str(ROOT / "model-cache-assets.mjs"), str(output)],
            input=json.dumps(legacy, ensure_ascii=False), text=True, encoding="utf-8",
            capture_output=True, check=True, timeout=180, cwd=ROOT)
        model = json.loads((output / "analysis_model_mtm_summary.json").read_text(encoding="utf-8"))
        errors = publication_errors(versions, claim, delivery, model)
        if errors or read_versions(analytics, source) != versions:
            raise ValueError("Sources changed or snapshots are invalid; previous publications retained: " + "; ".join(errors))
        print("PASS all three snapshots built and source versions rechecked", flush=True)
        if not publish:
            print("Dry run complete: no Firebase data or deployed output files were changed")
            return
        # Details first, summary last; readers always see a complete generation.
        def validate_source():
            if read_versions(analytics, source) != versions:
                raise ValueError("Sources changed during upload; rerun after the daily job finishes")
        publish_model_snapshot(analytics.child("pageAssets"), model, output_dir=output,
                               validate_source=validate_source)
        analytics.child("claimTrendStartup").set({
            **{key: claim[key] for key in ("schema", "generatedAt", "sourceVersion")},
            "data": json.dumps({"monthly": claim["monthly"], "closedIndex": claim["closedIndex"]}, separators=(",", ":")),
        })
        source.child("deliveryFlowHistory/startup").set({
            **delivery, "page": json.dumps(delivery["page"], ensure_ascii=False, separators=(",", ":")),
        })
        save_claim_startup(ROOT, claim)
        save_delivery_startup(ROOT, delivery)
    if check_publications(analytics, source):
        raise ValueError("Publication verification failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only publication check (default)")
    mode.add_argument("--dry-run", action="store_true", help="Build and validate without publishing")
    mode.add_argument("--publish", action="store_true", help="Repair the three Firebase page snapshots")
    parser.add_argument("--firebase-db-url", default=os.getenv("FIREBASE_DB_URL", DEFAULT_DB_URL))
    parser.add_argument("--firebase-sa-path", default=os.getenv("FIREBASE_SA_PATH", str(ROOT / "firebase-service-account.json")))
    parser.add_argument("--firebase-root", default=os.getenv("FIREBASE_ROOT", "c4cTickets_test"))
    parser.add_argument("--monitor-root", default=os.getenv("MONITOR_ROOT", "ctmTicketStatusMonitorV44"))
    args = parser.parse_args()
    if args.publish:
        from sync_dashboard_assets_to_firebase import init_firebase
        from firebase_admin import db
        init_firebase(args.firebase_db_url, args.firebase_sa_path)
        analytics = db.reference(f"{args.monitor_root}/analytics")
        source = db.reference(args.firebase_root)
    else:
        analytics = ReadReference(args.firebase_db_url, f"{args.monitor_root}/analytics")
        source = ReadReference(args.firebase_db_url, args.firebase_root)
    try:
        if args.publish or args.dry_run:
            rebuild(analytics, source, publish=args.publish)
            return 0
        return 1 if check_publications(analytics, source) else 0
    except subprocess.CalledProcessError as error:
        print("FAIL snapshot builder: " + (error.stderr or str(error)))
        return 1
    except Exception as error:
        print(f"FAIL {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
