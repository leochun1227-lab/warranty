import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from refresh_dashboard_snapshots import publication_errors
from sync_dashboard_assets_to_firebase import publish_model_snapshot
from build_claim_startup import publish_claim_startup
from build_delivery_startup import publish_delivery_startup


class DashboardRefreshTests(unittest.TestCase):
    def test_missing_or_stale_publications_fail_even_when_sources_are_current(self):
        versions = dict(team="t1", core="c1", so="s1", history="h1", model="m1")
        self.assertEqual(len(publication_errors(versions, None, None, None)), 3)
        claim = dict(schema="claim-startup-v1", sourceVersion=json.dumps(["t1", "c1", "s1"]))
        delivery = dict(schema="delivery-startup-v1", sourceVersion="h1|s1")
        model = dict(deliverySchema="model-summary-v1", generatedAt="m1")
        self.assertEqual(publication_errors(versions, claim, delivery, model), [])
        versions["core"] = "c2"
        self.assertEqual(len(publication_errors(versions, claim, delivery, model)), 1)
        claim["sourceVersion"] = "invalid"
        self.assertEqual(len(publication_errors(versions, claim, delivery, model)), 1)
        versions["model"] = "m2"
        self.assertEqual(len(publication_errors(versions, claim, delivery, model)), 2)

    def test_dry_run_builds_claim_without_writes(self):
        analytics, source = Mock(), Mock()
        analytics.child.side_effect = lambda key: Mock(get=lambda: "t1" if key == "team/generatedAt" else [])
        source.child.side_effect = lambda key: Mock(get=lambda: "c1" if key == "ticketCoreSyncAt" else "s1")
        snapshot = dict(schema="claim-startup-v1", generatedAt="t1", sourceVersion=json.dumps(["t1", "c1", "s1"]), monthly=[{}], closedIndex={})
        with patch("rebuild_model_series_assets.resolve_node_executable", return_value="node"), patch(
            "build_claim_startup.subprocess.run", return_value=Mock(stdout=json.dumps(snapshot))
        ), patch("build_claim_startup.save_claim_startup") as save:
            self.assertEqual(publish_claim_startup(analytics, source, {}, "c1", "s1", publish=False), snapshot)
            save.assert_not_called()
        self.assertNotIn("claimTrendStartup", [call.args[0] for call in analytics.child.call_args_list])

    def test_delivery_repair_does_not_merge_stale_machine_local_summary(self):
        ref = Mock()
        ref.child.side_effect = lambda key: Mock(get=lambda: "h1" if key == "latestSyncAt" else {"day": {}})
        snapshot = dict(schema="delivery-startup-v1", generatedAt="h1", sourceVersion="h1|s1", page={"history": [{}]})
        with patch("rebuild_model_series_assets.resolve_node_executable", return_value="node"), patch(
            "build_delivery_startup.subprocess.run", return_value=Mock(stdout=json.dumps(snapshot))
        ) as run, patch("build_delivery_startup.save_delivery_startup") as save:
            publish_delivery_startup(ref, {}, "h1", "s1", lambda: "s1", publish=False, use_local_summary=False)
            self.assertIsNone(json.loads(run.call_args.kwargs["input"])["summary"])
            save.assert_not_called()
        self.assertNotIn("startup", [call.args[0] for call in ref.child.call_args_list])

    def test_missing_model_export_does_not_replace_published_summary(self):
        ref = Mock()
        summary = {"periods": {"total": {"scopes": {"ALL": {"detailKey": "a" * 64}}}}}
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(FileNotFoundError):
            publish_model_snapshot(ref, summary, output_dir=Path(folder))
        ref.child.assert_not_called()

    def test_source_change_after_detail_upload_keeps_previous_model_summary(self):
        ref = Mock()
        data = json.dumps({"generatedAt": "m1", "rows": [{"id": "0001", "cost": 0}]}).encode()
        key = hashlib.sha256(data).hexdigest()
        summary = {"periods": {"total": {"scopes": {"ALL": {"detailKey": key}}}}}
        def changed():
            raise ValueError("source changed")
        with tempfile.TemporaryDirectory() as folder:
            details = Path(folder) / "model_mtm_details"
            details.mkdir()
            (details / f"{key}.json").write_bytes(data)
            with self.assertRaisesRegex(ValueError, "source changed"):
                publish_model_snapshot(ref, summary, output_dir=Path(folder), validate_source=changed)
        ref.child.assert_called_once_with(f"modelSeries/modelMtmDetails/{key}")
        self.assertEqual(ref.child.return_value.set.call_args.args[0]["rows"][0]["id"], "0001")


if __name__ == "__main__":
    unittest.main()
