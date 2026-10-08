import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from build_claim_startup import publish_claim_startup


class ClaimStartupPublicationTests(unittest.TestCase):
    def publish(self, *, changed=False, fail=False):
        analytics, source = Mock(), Mock()
        nodes = {key: Mock() for key in ("team/generatedAt", "teamDashboard/views/all/approvedAmountMonthly", "claimTrendStartup")}
        analytics.child.side_effect = nodes.__getitem__
        nodes["team/generatedAt"].get.return_value = "team-v1"
        nodes["teamDashboard/views/all/approvedAmountMonthly"].get.return_value = [{"month": "2026-01", "inField": 12}]
        core, so = Mock(), Mock()
        core.get.return_value = "core-v2" if changed else "core-v1"
        so.get.return_value = "so-v1"
        source.child.side_effect = {"ticketCoreSyncAt": core, "ticketSoSyncAt": so}.__getitem__
        snapshot = {"schema": "claim-startup-v1", "generatedAt": "team-v1",
                    "sourceVersion": json.dumps(["team-v1", "core-v1", "so-v1"]),
                    "monthly": [{"month": "2026-01", "approvedIn": 0}], "closedIndex": {}}
        with patch("rebuild_model_series_assets.resolve_node_executable", return_value="node"), patch(
            "build_claim_startup.subprocess.run", return_value=Mock(stdout=json.dumps(snapshot)),
            side_effect=subprocess.CalledProcessError(1, "node") if fail else None
        ), patch("build_claim_startup.save_claim_startup") as save:
            try:
                publish_claim_startup(analytics, source, {"0001": {}}, "core-v1", "so-v1")
            except (ValueError, subprocess.CalledProcessError):
                nodes["claimTrendStartup"].set.assert_not_called()
                save.assert_not_called()
                raise
            save.assert_called_once()
        return nodes["claimTrendStartup"].set.call_args.args[0]

    def test_atomic_summary_publication_preserves_zero_counts(self):
        payload = self.publish()
        self.assertEqual(json.loads(payload["data"])["monthly"][0]["approvedIn"], 0)

    def test_source_change_keeps_previous_snapshot(self):
        with self.assertRaises(ValueError):
            self.publish(changed=True)

    def test_failed_calculation_keeps_previous_snapshot(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish(fail=True)


if __name__ == "__main__":
    unittest.main()
