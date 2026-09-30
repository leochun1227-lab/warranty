"""Read-only comparison of C4C recall results and the Firebase snapshot."""
import concurrent.futures
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import sync_recall_claims_to_firebase as sync

OUT = Path(__file__).resolve().parent


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def database():
    sync.firebase_init()
    data = sync.db.reference(sync.RECALL_CLAIMS_TABLE_PATH).get()
    save("firebase_snapshot.json", data)
    tickets = data.get("tickets", {})
    print("DATABASE " + json.dumps({
        "meta": data.get("meta"), "actual_count": len(tickets),
        "status_counts": dict(Counter(t.get("statusText", "") for t in tickets.values())),
    }, ensure_ascii=False), flush=True)


def api(top, skip):
    with sync.build_session() as session:
        rows, meta = sync.fetch_recall_claims_page(session, top, skip)
    save(f"api_{top}_{skip}.json", {"meta": meta, "rows": rows})
    payload = sync.build_recall_claims_payload(rows, meta, top=top, skip=skip)
    print("API " + json.dumps(payload["meta"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
    skip = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(database), pool.submit(api, top, skip)]
        for future in concurrent.futures.as_completed(futures):
            future.result()
