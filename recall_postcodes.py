"""Keep confirmed postcode enrichment when replacing a recall snapshot."""
from typing import Any, Dict


def preserve_recall_postcodes(payload: Dict[str, Any], current: Any) -> Dict[str, Any]:
    """Merge only postcodes for tickets present in the incoming snapshot.

    Called from a Firebase transaction so a simultaneous postcode update is
    included on retry. Never mutate payload: callbacks may run more than once.
    """
    previous = current.get("tickets", {}) if isinstance(current, dict) else {}
    if not isinstance(previous, dict):
        previous = {}
    tickets = {}
    for ticket_id, ticket in payload.get("tickets", {}).items():
        merged = dict(ticket)
        old = previous.get(ticket_id)
        postcode = old.get("postcode") if isinstance(old, dict) else None
        if postcode is not None and str(postcode).strip():
            merged["postcode"] = postcode
        tickets[ticket_id] = merged
    return {**payload, "tickets": tickets}
