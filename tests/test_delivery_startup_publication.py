import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from build_delivery_startup import publish_delivery_startup


class DeliveryStartupPublicationTests(unittest.TestCase):
    def run_publication(self, snapshot, *, live_history="h1", live_tickets="t1", error=None):
        ref = Mock()
        nodes = {name: Mock() for name in ("daily", "latestSyncAt", "startup")}
        ref.child.side_effect = nodes.__getitem__
        nodes["daily"].get.return_value = {"day": {"asOf": "2026-10-07"}}
        nodes["latestSyncAt"].get.return_value = live_history
        with patch("build_delivery_startup.Path.exists", return_value=False), patch(
            "rebuild_model_series_assets.resolve_node_executable", return_value="node"
        ), patch(
            "build_delivery_startup.subprocess.run", return_value=Mock(stdout=json.dumps(snapshot)), side_effect=error
        ), patch("build_delivery_startup.save_delivery_startup") as save:
            try:
                publish_delivery_startup(ref, {"0001": {}}, "h1", "t1", lambda: live_tickets)
            except (ValueError, subprocess.CalledProcessError):
                nodes["startup"].set.assert_not_called()
                save.assert_not_called()
                raise
            save.assert_called_once()
        return nodes["startup"].set.call_args.args[0]

    def snapshot(self):
        return {"schema": "delivery-startup-v1", "generatedAt": "h1", "sourceVersion": "h1|t1",
                "page": {"history": [{"asOf": "2026-10-07"}], "currentTickets": [], "currentSummary": {"awaitingNow": 0}}}

    def test_atomic_publication_preserves_arrays_and_real_zero(self):
        snapshot = self.snapshot()
        payload = self.run_publication(snapshot)
        self.assertEqual(payload["sourceVersion"], "h1|t1")
        self.assertEqual(json.loads(payload["page"]), snapshot["page"])

    def test_changed_sources_do_not_replace_previous_publication(self):
        for changed in ({"live_history": "h2"}, {"live_tickets": "t2"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.run_publication(self.snapshot(), **changed)

    def test_wrong_generation_does_not_replace_previous_publication(self):
        snapshot = self.snapshot()
        snapshot["sourceVersion"] = "h0|t0"
        with self.assertRaises(ValueError):
            self.run_publication(snapshot)

    def test_failed_build_does_not_replace_previous_publication(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_publication(self.snapshot(), error=subprocess.CalledProcessError(1, "node"))


if __name__ == "__main__":
    unittest.main()
