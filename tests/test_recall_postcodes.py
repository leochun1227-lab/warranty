import unittest

from recall_postcodes import preserve_recall_postcodes


class RecallPostcodeTests(unittest.TestCase):
    def test_snapshot_refresh_preserves_only_postcode(self):
        incoming = {"meta": {"count": 2}, "tickets": {
            "1": {"statusText": "Closed"}, "2": {"statusText": "New"}
        }}
        current = {"meta": {"count": 99}, "tickets": {
            "1": {"statusText": "Open", "postcode": "0800", "oldField": True},
            "3": {"postcode": "3000"}
        }}
        result = preserve_recall_postcodes(incoming, current)
        self.assertEqual(result, {"meta": {"count": 2}, "tickets": {
            "1": {"statusText": "Closed", "postcode": "0800"},
            "2": {"statusText": "New"}
        }})
        self.assertNotIn("postcode", incoming["tickets"]["1"])

    def test_transaction_retry_uses_latest_postcode_without_stale_carryover(self):
        incoming = {"tickets": {"1": {"statusText": "Closed"}}}
        first = preserve_recall_postcodes(incoming, {"tickets": {"1": {"postcode": "3000"}}})
        second = preserve_recall_postcodes(incoming, {"tickets": {"1": {"postcode": "3216"}}})
        third = preserve_recall_postcodes(incoming, {"tickets": {"1": {}}})
        self.assertEqual(first["tickets"]["1"]["postcode"], "3000")
        self.assertEqual(second["tickets"]["1"]["postcode"], "3216")
        self.assertNotIn("postcode", third["tickets"]["1"])

    def test_empty_database_and_blank_values(self):
        incoming = {"tickets": {"1": {"statusText": "New"}}}
        for current in [None, {}, {"tickets": None}, {"tickets": {"1": {"postcode": " "}}}]:
            self.assertEqual(preserve_recall_postcodes(incoming, current), incoming)


if __name__ == "__main__":
    unittest.main()
