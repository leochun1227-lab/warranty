"""Read back the completed sync and compare it with the pre-sync backup."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import sync_recall_claims_to_firebase as sync

OUT = Path(__file__).resolve().parent
before = json.loads((OUT / "firebase_snapshot.json").read_text(encoding="utf-8"))
sync.firebase_init()
after = sync.db.reference(sync.RECALL_CLAIMS_TABLE_PATH).get()
(OUT / "firebase_after.json").write_text(json.dumps(after, ensure_ascii=False), encoding="utf-8")
old, new = before["tickets"], after["tickets"]
api = json.loads((OUT / "api_50000_50000.json").read_text(encoding="utf-8"))
api_ids = {str(row["TicketID"]) for row in api["rows"]}
postcode_ids = {tid for tid, t in old.items() if str(t.get("postcode") or "").strip()}
summary = {
    "before_count": len(old), "after_count": len(new),
    "added_count": len(set(new) - set(old)), "lost_ids": sorted(set(old) - set(new)),
    "api_only_ids": sorted(api_ids - set(new)),
    "postcodes_before": len(postcode_ids),
    "postcode_losses": sorted(tid for tid in postcode_ids
                              if new.get(tid, {}).get("postcode") != old[tid]["postcode"]),
    "model_statuses": dict(Counter(t.get("modelLookup", {}).get("status") for t in new.values())),
    "meta": after["meta"],
}
(OUT / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
assert after["meta"]["syncComplete"] is True
assert after["meta"]["count"] == len(new)
assert not summary["lost_ids"] and not summary["api_only_ids"] and not summary["postcode_losses"]
