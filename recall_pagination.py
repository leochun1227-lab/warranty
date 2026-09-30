"""Fetch the complete recall result; C4C counts flattened rows, not tickets."""
import logging

logger = logging.getLogger(__name__)


def fetch_all_recall_claims(fetch_page, page_size=10000):
    """Cover the API's raw-row total without imposing a ticket or page limit.

    A short ticket list does not indicate the last page: the CPI service groups
    raw rows into tickets. Overlapping TicketIDs must be merged by the caller.
    Missing counts or premature empty pages fail before any database write.
    """
    if page_size <= 0:
        raise ValueError("Recall page size must be positive")
    rows, pages = [], []
    skip = 0
    total = 0
    while True:
        page, meta = fetch_page(page_size, skip)
        raw_total = meta.get("totalCount")
        if raw_total is None:
            raw_total = meta.get("count")
        try:
            reported_total = int(str(raw_total))
        except (ValueError, TypeError):
            raise RuntimeError("C4C recall response has no valid raw-row total; sync aborted")
        if reported_total < 0:
            raise RuntimeError("C4C recall response has a negative total; sync aborted")
        total = max(total, reported_total)
        actual_size = meta.get("pageSize")
        actual_size = page_size if actual_size is None else int(actual_size)
        if actual_size <= 0 or actual_size > page_size:
            raise RuntimeError("C4C recall returned an invalid page size; sync aborted")
        if meta.get("pageNumber") is not None and int(meta["pageNumber"]) != skip // actual_size + 1:
            raise RuntimeError("C4C recall returned the wrong page number; sync aborted")
        if not page and skip < total:
            raise RuntimeError(f"C4C recall returned an empty page at {skip} before total {total}")
        for row in page:
            if not isinstance(row, dict) or not str(row.get("TicketID") or "").strip():
                raise RuntimeError("C4C recall returned a row without TicketID; sync aborted")
            if str(row.get("TicketType") or "").strip().upper() != "Z011":
                raise RuntimeError("C4C recall returned a non-Z011 row; sync aborted")
        if page and total == 0:
            raise RuntimeError("C4C recall returned tickets with a zero total; sync aborted")
        rows.extend(page)
        pages.append({"skip": skip, "pageSize": actual_size,
                      "returnedTicketRows": len(page), "rawRowCount": reported_total})
        logger.info("Recall page: skip=%s size=%s tickets=%s rawTotal=%s",
                    skip, actual_size, len(page), reported_total)
        skip += actual_size
        if skip >= total:
            break
    return rows, {"count": total, "totalCount": total,
                  "syncComplete": True, "pageSize": page_size,
                  "pagesFetched": len(pages), "pages": pages,
                  "rawRowsCovered": min(skip, total)}


def validate_recall_replacement(payload, current):
    """Reject incomplete snapshots and unexpected ticket loss before commit."""
    if payload.get("meta", {}).get("syncComplete") is not True:
        raise RuntimeError("Recall snapshot is not verified complete; database unchanged")
    old = current.get("tickets", {}) if isinstance(current, dict) else {}
    missing = set(old or {}) - set(payload.get("tickets", {}))
    if missing:
        raise RuntimeError(f"Recall sync would remove {len(missing)} existing tickets; "
                           "database unchanged, investigate source completeness")
    return payload
