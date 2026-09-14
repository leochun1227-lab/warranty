import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from recall_models import attach_recall_models, enrich_recall_models, query_model_rows
from recall_postcodes import preserve_recall_postcodes


def payload():
    return {"meta": {"count": 1}, "tickets": {"1": {
        "product": {"serialId": "SRC253530", "chassisNumber": "LA9202ES2S1ZDD094"},
        "statusText": "Open", "model": "OLD", "modelDescription": "Old model",
    }}}


def row(code="Z12112305", **changes):
    result = {"INPUT_ID": "SRC253530", "SERIAL_ID": "SRC253530", "SALES_ORDER": "0010033443",
              "SALES_ORG": "3110", "MODEL_ITEM": "000010", "MATERIAL_CODE": code,
              "MATERIAL_DESCRIPTION": "2023 SRC19", "ORDER_DATE": "20250307"}
    return {**result, **changes}


class RecallModelTests(unittest.TestCase):
    def test_same_model_on_multiple_orders_is_unique_and_keeps_provenance(self):
        incoming = payload()
        original = copy.deepcopy(incoming)
        result = attach_recall_models(incoming, [row(), row(SALES_ORDER="0010099999", ORDER_DATE="20260914")], "now")
        ticket = result["tickets"]["1"]
        self.assertEqual(ticket["model"], "2023 SRC19")
        self.assertNotIn("modelDescription", ticket)
        self.assertNotIn("materialCode", json.dumps(result))
        self.assertNotIn("Z12112305", json.dumps(result))
        self.assertEqual(ticket["modelLookup"]["status"], "matched")
        self.assertEqual(len(ticket["modelLookup"]["salesOrders"]), 2)
        self.assertEqual(incoming, original)

    def test_vin_and_serial_disagreement_never_selects_an_arbitrary_model(self):
        rows = [row(), row("Z12112304", INPUT_ID="LA9202ES2S1ZDD094", SERIAL_ID="SRC253321")]
        ticket = attach_recall_models(payload(), rows, "now")["tickets"]["1"]
        self.assertNotIn("model", ticket)
        self.assertNotIn("modelDescription", ticket)
        self.assertEqual(ticket["modelLookup"]["status"], "conflict")
        self.assertEqual(len(ticket["modelLookup"]["candidates"]), 2)

    def test_other_organizations_and_order_items_cannot_supply_model(self):
        rows = [row("D14212305", SALES_ORG="3090"), row("WRONG", MODEL_ITEM="000020"), row()]
        result = attach_recall_models(payload(), rows, "now")
        self.assertEqual(result["tickets"]["1"]["model"], "2023 SRC19")

    def test_blank_description_never_falls_back_to_material_code(self):
        result = attach_recall_models(payload(), [row(MATERIAL_DESCRIPTION=" ")], "now")
        ticket = result["tickets"]["1"]
        self.assertNotIn("model", ticket)
        self.assertNotIn("modelDescription", ticket)
        self.assertEqual(ticket["modelLookup"]["status"], "missing_model_description")
        self.assertNotIn("Z12112305", json.dumps(result))

    def test_latest_description_is_refreshed_without_storing_codes(self):
        rows = [row(), row(MATERIAL_DESCRIPTION="2026 SRC19", ORDER_DATE="20260914", SALES_ORDER="0010099999")]
        result = attach_recall_models(payload(), rows, "now")
        self.assertEqual(result["tickets"]["1"]["model"], "2026 SRC19")
        self.assertNotIn("Z12112305", json.dumps(result))

    def test_missing_material_and_invalid_vehicle_clear_stale_model(self):
        missing = attach_recall_models(payload(), [row(None, MODEL_ITEM=None)], "now")["tickets"]["1"]
        self.assertEqual(missing["modelLookup"]["status"], "missing_0010_material")
        self.assertNotIn("model", missing)
        source = {"tickets": {"1": {"product": {"chassisNumber": "TEST"}, "model": "OLD"}}}
        invalid = attach_recall_models(source, [], "now")["tickets"]["1"]
        self.assertEqual(invalid["modelLookup"]["status"], "invalid_vehicle_id")
        self.assertNotIn("model", invalid)

    def test_model_refresh_keeps_latest_postcode_during_transaction_retry(self):
        enriched = attach_recall_models(payload(), [row()], "now")
        for postcode in ["0800", "3216"]:
            result = preserve_recall_postcodes(enriched, {"tickets": {"1": {"postcode": postcode, "model": "OLD"}}})
            self.assertEqual(result["tickets"]["1"]["postcode"], postcode)
            self.assertEqual(result["tickets"]["1"]["model"], "2023 SRC19")
        self.assertNotIn("postcode", enriched["tickets"]["1"])

    def test_hana_failure_propagates_without_modifying_payload(self):
        source = payload()
        original = copy.deepcopy(source)
        with patch("pyodbc.connect", side_effect=RuntimeError("HANA unavailable")):
            with self.assertRaisesRegex(RuntimeError, "HANA unavailable"):
                enrich_recall_models(source, dsn="test")
        self.assertEqual(source, original)

    def test_query_batches_parameterized_vehicle_keys_and_uses_agreed_scope(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.description = [("INPUT_ID",)]
        cursor.fetchall.return_value = []
        keys = ["SRC" + str(i).zfill(7) for i in range(201)]
        self.assertEqual(query_model_rows(connection, keys + [keys[0], "TEST"]), [])
        self.assertEqual(cursor.execute.call_count, 2)
        batches = cursor.execute.call_args_list
        self.assertEqual([len(call.args[1]) for call in batches], [200, 1])
        self.assertIn("v.VKORG='3110'", batches[0].args[0])
        self.assertIn("p.POSNR='000010'", batches[0].args[0])
        self.assertNotIn(keys[0], batches[0].args[0])

    def test_main_sync_entrypoint_enriches_before_transaction_and_aborts_on_failure(self):
        import fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER as sync
        enriched = attach_recall_models(payload(), [row()], "now")
        current = {"tickets": {"1": {"postcode": "0800"}}}
        with patch.object(sync, "build_recall_claims_payload", return_value=payload()), \
             patch.object(sync, "enrich_recall_models", return_value=enriched) as enrich, \
             patch.object(sync.db, "reference") as reference:
            committed = []
            reference.return_value.transaction.side_effect = lambda callback: committed.append(callback(current))
            sync.upload_recall_claims_to_firebase({})
            enrich.assert_called_once()
            reference.return_value.transaction.assert_called_once()
            self.assertEqual(committed[0]["tickets"]["1"]["model"], "2023 SRC19")
            self.assertEqual(committed[0]["tickets"]["1"]["postcode"], "0800")
        with patch.object(sync, "build_recall_claims_payload", return_value=payload()), \
             patch.object(sync, "enrich_recall_models", side_effect=RuntimeError("HANA unavailable")), \
             patch.object(sync.db, "reference") as reference:
            with self.assertRaises(RuntimeError):
                sync.upload_recall_claims_to_firebase({})
            reference.assert_not_called()

    def test_standalone_failure_aborts_before_firebase_and_dry_run_still_enriches(self):
        import sync_recall_claims_to_firebase as sync
        source = payload()
        source["meta"].update(rawRowCount=1, returnedTicketRows=1, uniqueTicketCount=1, skippedOtherTypes=0)
        args = SimpleNamespace(top=50000, skip=0, print_url=False, dry_run=True)
        with patch.object(sync, "parse_args", return_value=args), \
             patch.object(sync, "build_session"), \
             patch.object(sync, "fetch_recall_claims_page", return_value=([], {})), \
             patch.object(sync, "build_recall_claims_payload", return_value=source), \
             patch.object(sync, "enrich_recall_models") as enrich, \
             patch.object(sync, "firebase_init") as initialize, \
             patch.object(sync.db, "reference") as reference, \
             patch("builtins.print"):
            enrich.side_effect = RuntimeError("HANA unavailable")
            with self.assertRaises(RuntimeError):
                sync.main()
            initialize.assert_not_called()
            reference.assert_not_called()
            enrich.side_effect = None
            enrich.return_value = attach_recall_models(source, [row()], "now")
            sync.main()
            self.assertEqual(enrich.call_count, 2)
            initialize.assert_not_called()
            reference.assert_not_called()


if __name__ == "__main__":
    unittest.main()
