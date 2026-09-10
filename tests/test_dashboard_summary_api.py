import copy
import unittest
from unittest.mock import MagicMock

from build_dashboard_summary_api import build_summary, publish_summary


def sources():
    stage = {
        "exportLabel": "Warranty Approval", "overLabel": "Ticket complete over avg time: 50.0%",
        "buckets": [["0-7", 100, "#000", 2]],
        "trend": [{"label": "Jan", "avg": 3.5, "roll3": 3.5, "completed": 2}],
        "tickets": [{"id": "PRIVATE", "customer": "Must not be exported"}],
    }
    completion = {"year": 2026, "generatedAt": "2026-09-08T12:00:00",
                  "basis": "completed month in selected year", "stages": {
                      "approval": stage, "parts": copy.deepcopy(stage)}}
    team = {"generatedAt": "2026-09-09T08:53:33+00:00", "minDate": "2026-05-25",
            "views": {"all": {
                "trend": [{"date": "2026-09-09"}], "summary": {"criticalNow": 28},
                "statusMix": [{"status": f"Status {i}", "count": i} for i in range(1, 8)],
                "periodSnapshots": {"2026-09-01|2026-09-09": {"repairCostDistribution": [
                    {"key": "0", "label": "$0", "count": 19, "total": 0},
                    {"key": "100-500", "label": "$100 - $500", "count": 1, "total": 200},
                    {"key": "500-1000", "label": "$500 - $1k", "count": 1, "total": 800},
                ]}},
            }}}
    return team, completion


class SummaryTests(unittest.TestCase):
    def test_contract_and_chart_denominators(self):
        team, completion = sources()
        result = build_summary(team, completion)
        self.assertEqual(result["remainingStatusMix"]["items"][-1]["count"], 3)
        self.assertEqual(result["remainingStatusMix"]["totalTickets"], 28)
        cost = result["approvedRepairCostDistribution"]
        self.assertEqual(cost["totalTickets"], 21)
        self.assertEqual(cost["totalAmount"], 1000)
        self.assertEqual(cost["buckets"][1]["amountPercent"], 20)
        self.assertEqual(cost["buckets"][1]["ticketPercent"], 4.76)
        self.assertEqual(cost["buckets"][0]["count"], 19)
        self.assertEqual(cost["periodEnd"], "2026-09-09")
        self.assertNotIn("PRIVATE", str(result))
        self.assertNotEqual(result["ticketTimeline"]["sourceGeneratedAt"], cost["sourceGeneratedAt"])
        self.assertEqual(result["ticketTimeline"]["stages"]["approval"]["monthlyTrend"][0]["month"], "2026-01")

    def test_six_statuses_are_not_grouped(self):
        team, completion = sources()
        view = team["views"]["all"]
        view["statusMix"].pop()
        view["summary"]["criticalNow"] = 21
        self.assertNotIn("Others", str(build_summary(team, completion)["remainingStatusMix"]))

    def test_missing_period_and_inconsistent_sources_fail(self):
        team, completion = sources()
        completion["stages"]["approval"]["trend"][0]["completed"] = 3
        with self.assertRaises(ValueError):
            build_summary(team, completion)
        team, completion = sources()
        team["views"]["all"]["periodSnapshots"] = {}
        with self.assertRaises(KeyError):
            build_summary(team, completion)

    def test_empty_firebase_arrays_and_null_averages(self):
        team, completion = sources()
        view = team["views"]["all"]
        view.pop("statusMix")
        view["summary"]["criticalNow"] = 0
        view["trend"] = {"0": {"date": "2026-09-09"}}
        for row in view["periodSnapshots"]["2026-09-01|2026-09-09"]["repairCostDistribution"]:
            row.update(count=0, total=0)
        completion["stages"]["parts"]["trend"][0].update(avg=None, roll3=None)
        result = build_summary(team, completion)
        self.assertEqual(result["remainingStatusMix"]["items"], [])
        self.assertEqual(result["approvedRepairCostDistribution"]["buckets"][0]["amountPercent"], 0)
        self.assertIsNone(result["ticketTimeline"]["stages"]["parts"]["monthlyTrend"][0]["averageDays"])

    def test_invalid_data_does_not_overwrite_published_summary(self):
        ref = MagicMock()
        team, completion = sources()
        team["views"]["all"]["summary"]["criticalNow"] = 999
        with self.assertRaises(ValueError):
            publish_summary(ref, team=team, completion=completion)
        ref.child.assert_not_called()

    def test_publication_is_one_atomic_write(self):
        ref = MagicMock()
        team, completion = sources()
        result = publish_summary(ref, team=team, completion=completion)
        ref.child.assert_called_once_with("dashboardSummary")
        ref.child.return_value.set.assert_called_once_with(result)


if __name__ == "__main__":
    unittest.main()
