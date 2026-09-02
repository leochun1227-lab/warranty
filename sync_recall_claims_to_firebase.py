from __future__ import annotations

import time

import fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER as fetcher


def main() -> None:
    if not fetcher.USERNAME or not fetcher.PASSWORD:
        raise SystemExit("Please set C4C_USERNAME / C4C_PASSWORD")

    started = time.time()
    fetcher.logger.info("Recall Claims standalone sync started.")
    fetcher.logger.info(
        "Fetching type %s with top=%s skip=%s and no CCSRQ_DPY_ROLE_CD filter.",
        fetcher.RECALL_CLAIMS_TICKET_TYPE,
        fetcher.RECALL_CLAIMS_API_TOP,
        fetcher.RECALL_CLAIMS_API_SKIP_START,
    )

    try:
        new_snapshot, total_rows = fetcher.build_recall_claims_snapshot()
        fetcher.logger.info("C4C Recall Claims API rows/count processed: %s", total_rows)
        fetcher.logger.info("C4C Recall Claims unique TicketIDs: %s", len(new_snapshot))

        fetcher.firebase_init()
        fetcher.upload_recall_claims_to_firebase(new_snapshot)
    finally:
        fetcher.close_thread_session()

    fetcher.logger.info("Recall Claims standalone sync done. Total elapsed: %.1fs", time.time() - started)


if __name__ == "__main__":
    main()
