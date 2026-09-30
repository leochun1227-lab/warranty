import unittest
from unittest.mock import Mock, patch

from recall_pagination import fetch_all_recall_claims, validate_recall_replacement


def ticket(tid, **extra):
    return {"TicketID": str(tid), "TicketType": "Z011", **extra}


class RecallPaginationTests(unittest.TestCase):
    def test_short_grouped_ticket_page_does_not_truncate_raw_rows(self):
        fetch = Mock(side_effect=[([ticket(1)], {"count": "73802"}),
                                  ([ticket(1), ticket(2)], {"count": "73802"})])
        rows, meta = fetch_all_recall_claims(fetch, 50000)
        self.assertEqual([call.args for call in fetch.call_args_list], [(50000, 0), (50000, 50000)])
        self.assertEqual({r["TicketID"] for r in rows}, {"1", "2"})
        self.assertTrue(meta["syncComplete"])
        self.assertEqual(meta["rawRowsCovered"], 73802)

    def test_total_can_grow_and_server_can_use_smaller_pages(self):
        fetch = Mock(side_effect=[([ticket(1)], {"count": 5, "pageSize": 3}),
                                  ([ticket(2)], {"count": 8, "pageSize": 3}),
                                  ([ticket(3)], {"count": 8, "pageSize": 3})])
        _, meta = fetch_all_recall_claims(fetch, 10)
        self.assertEqual([c.args[1] for c in fetch.call_args_list], [0, 3, 6])
        self.assertEqual(meta["totalCount"], 8)

    def test_no_fixed_total_or_page_cap(self):
        fetch = Mock(side_effect=lambda top, skip: ([ticket(skip)], {"count": 105}))
        with patch("recall_pagination.logger"):
            _, meta = fetch_all_recall_claims(fetch, 1)
        self.assertEqual(meta["pagesFetched"], 105)

    def test_missing_counts_empty_pages_and_bad_rows_abort(self):
        for response in [([], {}), ([], {"count": 1}), ([ticket(1)], {"count": 0}),
                         ([{"TicketType": "Z011"}], {"count": 1}),
                         ([ticket(1, TicketType="OTHER")], {"count": 1})]:
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                fetch_all_recall_claims(Mock(return_value=response))

    def test_later_page_failure_propagates(self):
        fetch = Mock(side_effect=[([ticket(1)], {"count": 60000}), RuntimeError("timeout")])
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            fetch_all_recall_claims(fetch)

    def test_repeated_page_number_aborts(self):
        fetch = Mock(return_value=([ticket(1)], {"count": 60000, "pageNumber": 1}))
        with self.assertRaisesRegex(RuntimeError, "wrong page number"):
            fetch_all_recall_claims(fetch)

    def test_empty_source_is_complete_but_cannot_erase_database(self):
        _, meta = fetch_all_recall_claims(Mock(return_value=([], {"count": 0})))
        with self.assertRaisesRegex(RuntimeError, "remove 1"):
            validate_recall_replacement({"meta": meta, "tickets": {}}, {"tickets": {"1": {}}})

    def test_partial_snapshot_rejected_and_existing_tickets_preserved(self):
        with self.assertRaisesRegex(RuntimeError, "not verified complete"):
            validate_recall_replacement({"tickets": {"1": {}}}, None)
        incoming = {"meta": {"syncComplete": True}, "tickets": {"1": {}, "2": {}}}
        self.assertIs(validate_recall_replacement(incoming, {"tickets": {"1": {}}}), incoming)

    def test_duplicate_ticket_across_pages_keeps_roles(self):
        import sync_recall_claims_to_firebase as sync
        rows = [ticket(1, InvolvedParties=[{"InvolvedPartyRoleID": "A"}]),
                ticket(1, InvolvedParties=[{"InvolvedPartyRoleID": "B"}])]
        result = sync.build_recall_claims_payload(rows, {"count": 10, "syncComplete": True})
        self.assertEqual(result["meta"]["count"], 1)
        self.assertEqual(set(result["tickets"]["1"]["roles"]), {"A", "B"})

    def test_daily_entrypoint_fetches_all_pages(self):
        import fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER as sync
        with patch.object(sync, "RECALL_CLAIMS_API_TOP", 50000), patch.object(sync, "fetch_recall_claims_page", side_effect=[
                ([ticket(1)], {"count": 60000}), ([ticket(2)], {"count": 60000})]):
            snapshot, meta = sync.build_recall_claims_snapshot()
        self.assertEqual(set(snapshot), {"1", "2"})
        self.assertEqual(meta["pagesFetched"], 2)


if __name__ == "__main__":
    unittest.main()
