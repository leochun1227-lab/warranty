import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from build_dashboard_startup import publish_dashboard_startup


class StartupPublicationTests(unittest.TestCase):
    def test_one_atomic_write_preserves_json_cache_keys_and_leading_zero_ids(self):
        ref = Mock()
        snapshot = {"schema": "team-startup-v1", "generatedAt": "v1", "page": {
            "renderSnapshot": {"values": {'["criticalRows",["2026-09-30"]]': [{"id": "0001", "amount": 0}]}}
        }}
        with patch("build_dashboard_startup.save_local_startup"), patch("rebuild_model_series_assets.resolve_node_executable", return_value="node"), patch(
            "build_dashboard_startup.subprocess.run", return_value=Mock(stdout=json.dumps(snapshot))
        ):
            publish_dashboard_startup(ref, {"generatedAt": "v1"}, {}, {})
        ref.child.assert_called_once_with("teamStartup")
        payload = ref.child.return_value.set.call_args.args[0]
        self.assertEqual(json.loads(payload["page"]), snapshot["page"])

    def test_failed_calculation_keeps_last_published_snapshot(self):
        ref = Mock()
        with patch("rebuild_model_series_assets.resolve_node_executable", return_value="node"), patch(
            "build_dashboard_startup.subprocess.run", side_effect=subprocess.CalledProcessError(1, "node")
        ), self.assertRaises(subprocess.CalledProcessError):
            publish_dashboard_startup(ref, {"generatedAt": "v1"}, {}, {})
        ref.child.assert_not_called()

    def test_mismatched_generation_is_not_published(self):
        ref = Mock()
        with patch("rebuild_model_series_assets.resolve_node_executable", return_value="node"), patch(
            "build_dashboard_startup.subprocess.run", return_value=Mock(stdout=json.dumps({
                "schema": "team-startup-v1", "generatedAt": "v2", "page": {}
            }))
        ), self.assertRaises(ValueError):
            publish_dashboard_startup(ref, {"generatedAt": "v1"}, {}, {})
        ref.child.assert_not_called()


if __name__ == "__main__":
    unittest.main()
