import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import daily_refresh_support as support
import vehicle_summary_publication as vehicle
from firebase_admin.exceptions import InvalidArgumentError
import fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER as fetcher
import ctm_v44_history_safe_mandt800_rejection_filter as daily
from sync_issue_positions import fetch_type_with_recovery
from issue_ai import IssueAIError
from export_vehicle_failure_timing_2025_2026 import default_local_ticket_csv, load_tickets_from_csv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pdv_dashboard'))
spec = importlib.util.spec_from_file_location('pdv_refresh_resilience', ROOT / 'pdv_dashboard/refresh.py')
pdv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pdv)


class VehicleTests(unittest.TestCase):
    def test_illegal_aliases_keep_all_data_and_frontend_lookup(self):
        payload = {'totalVehicles': 10, 'seriesBase': {'RRV': 10}}
        for name in vehicle.LOOKUP_MAPS:
            payload[name] = {'RRV220082/STOCK': 'value', 'RRV220082STOCK': 'value', 'VALID': 'other'}
        original = copy.deepcopy(payload)
        safe = vehicle.prepare_vehicle_summary(payload)
        self.assertEqual(payload, original)
        for name in vehicle.LOOKUP_MAPS:
            self.assertEqual(safe[name], {'RRV220082STOCK': 'value', 'VALID': 'other'})
        self.assertEqual(safe['totalVehicles'], 10)
        self.assertEqual(safe['seriesBase'], payload['seriesBase'])

    def test_collision_and_unknown_invalid_keys_do_not_publish(self):
        for payload in ({'pgiByChassis': {'A/B': 'x', 'AB': 'y'}}, {'other': {'A/B': 'x'}}):
            ref = Mock()
            with self.assertRaises(ValueError):
                vehicle.publish_vehicle_summary(ref, payload)
            ref.set.assert_not_called()

    def test_atomic_upload_and_verified_readback(self):
        payload = {'pgiByChassis': {'A/B': 'date'}, 'empty': {}, 'none': None}
        ref = Mock()
        ref.get.side_effect = [None, {'pgiByChassis': {'AB': 'date'}}]
        vehicle.publish_vehicle_summary(ref, payload)
        ref.set.assert_called_once_with({'pgiByChassis': {'AB': 'date'}, 'empty': {}, 'none': None})
        ref.update.assert_not_called()

    def test_existing_summary_uses_one_atomic_delta_and_removes_old_entries(self):
        before = {'generatedAt': 'old', 'pgiByChassis': {'A': 'date', 'obsolete': 'date'}}
        after = {'generatedAt': 'new', 'pgiByChassis': {'A': 'date', 'B': 'new date'}}
        ref = Mock()
        ref.get.side_effect = [before, after]
        vehicle.publish_vehicle_summary(ref, after)
        ref.set.assert_not_called()
        ref.update.assert_called_once_with({'generatedAt': 'new', 'pgiByChassis/obsolete': None, 'pgiByChassis/B': 'new date'})

    def test_bad_readback_and_transient_network_retry(self):
        ref = Mock()
        ref.get.side_effect = [ConnectionError('temporary'), None, {'totalVehicles': 10}]
        with patch.object(vehicle.time, 'sleep'):
            vehicle.publish_vehicle_summary(ref, {'totalVehicles': 10})
        self.assertEqual(ref.set.call_count, 1)
        ref.get.side_effect = None
        ref.get.return_value = {'totalVehicles': 9}
        with patch.object(vehicle.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'read-back'):
            vehicle.publish_vehicle_summary(ref, {'totalVehicles': 10})

    def test_invalid_argument_is_not_reported_as_size_or_retried(self):
        exc = InvalidArgumentError('Invalid key: A/B')
        with patch.object(fetcher.db, 'reference') as reference, patch.object(fetcher, '_update_bisect') as split:
            reference.return_value.update.side_effect = exc
            with self.assertRaises(InvalidArgumentError) as caught:
                fetcher.fb_update_with_retry('root', {'key': 'value'})
            self.assertIs(caught.exception, exc)
            self.assertEqual(reference.return_value.update.call_count, 1)
            split.assert_not_called()
        with patch.object(fetcher, '_ref_update_once', side_effect=exc):
            with self.assertRaises(InvalidArgumentError):
                fetcher._update_bisect('root', [('key', 'value')])

    def test_real_size_error_still_splits(self):
        calls = []
        def write(path, payload):
            calls.append(payload)
            if len(payload) > 1:
                raise InvalidArgumentError('Payload exceeds the maximum size')
        with patch.object(fetcher, '_ref_update_once', side_effect=write):
            fetcher._update_bisect('root', [('a', 1), ('b', 2)])
        self.assertEqual(calls, [{'a': 1, 'b': 2}, {'a': 1}, {'b': 2}])

    def test_batch_size_accounting_preserves_exact_old_boundaries(self):
        payload = {'ticket/'+str(i): {'text': '车辆\\"' * i, 'values': [i, None, False]} for i in range(20)}
        for paths, byte_limit in ((3, 10000), (100, 300), (1, 1)):
            expected, batch = [], {}
            for key, value in payload.items():
                batch[key] = value
                if len(batch) >= paths or fetcher._rough_bytes(batch) >= byte_limit:
                    expected.append(batch)
                    batch = {}
            if batch:
                expected.append(batch)
            with patch.object(fetcher, 'MAX_PATHS_PER_UPDATE', paths), patch.object(fetcher, 'MAX_BYTES_PER_UPDATE', byte_limit), patch.object(fetcher, 'fb_update_with_retry') as upload, patch.object(fetcher, '_rough_bytes', wraps=fetcher._rough_bytes) as size:
                fetcher.upload_payload_in_batches('root', payload)
                self.assertEqual([c.args[1] for c in upload.call_args_list], expected)
                self.assertEqual(size.call_count, len(payload))
                self.assertTrue(all(len(c.args[0]) == 1 for c in size.call_args_list))


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ)
        self.env.start()
        support.start_run(self.temp.name)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_auxiliary_failures_continue_but_interrupts_propagate(self):
        for error in (ValueError('invalid optional data'), SystemExit(1)):
            self.assertIsNone(support.run_optional_step('export', Mock(side_effect=error)))
        self.assertEqual(support.run_optional_step('next step', lambda: 123), 123)
        self.assertEqual(len(support.run_warnings()), 2)
        with self.assertRaises(KeyboardInterrupt):
            support.run_optional_step('user stop', Mock(side_effect=KeyboardInterrupt))

    def test_child_warning_report_reaches_parent(self):
        subprocess.run([sys.executable, '-c', "from daily_refresh_support import run_optional_step; run_optional_step('child export', lambda: 1/0)"], cwd=ROOT, check=True, capture_output=True)
        self.assertEqual(support.run_warnings()[0]['step'], 'child export')

    def test_core_failure_stops_compare_and_issue_sync(self):
        args = SimpleNamespace(skip_fetch=False, skip_dealer_analytics=False, company_file='company',
            firebase_db_url='url', firebase_sa_path='key', source_root='source', monitor_root='monitor')
        with patch.object(daily, 'start_run'), patch.object(daily, 'run_company_fetch', side_effect=RuntimeError('core failed')), patch.object(daily, 'compare_and_write') as compare, patch('issue_ai_daily.run_after_daily_fetch') as issue:
            with self.assertRaisesRegex(RuntimeError, 'core failed'):
                daily.run_once(args)
            compare.assert_not_called()
            issue.assert_not_called()

    def test_issue_failure_marks_partial_after_core_success(self):
        args = SimpleNamespace(skip_fetch=False, skip_dealer_analytics=False, company_file='company',
            firebase_db_url='url', firebase_sa_path='key', source_root='source', monitor_root='monitor')
        with patch.object(daily, 'start_run'), patch.object(daily, 'run_company_fetch'), patch.object(daily, 'compare_and_write'), patch.object(daily.db, 'reference') as ref, patch('issue_ai_daily.run_after_daily_fetch', side_effect=RuntimeError('incomplete scan')):
            self.assertEqual(daily.run_once(args), 2)
            status = ref.return_value.child.return_value.update.call_args.args[0]
            self.assertEqual(status['status'], 'success_with_warnings')
            self.assertEqual(status['warningSteps'], ['Issue Position sync'])

    def test_failed_compare_does_not_advance_status_generation(self):
        args = SimpleNamespace(skip_fetch=True, skip_dealer_analytics=False, source_root='source',
            monitor_root='monitor', firebase_db_url='url', firebase_sa_path='key')
        with patch.object(daily, 'start_run'), patch.object(daily, 'compare_and_write', side_effect=ValueError('bad analytics')), patch.object(daily.db, 'reference') as ref:
            self.assertEqual(daily.run_once(args), 2)
            self.assertNotIn('lastRunFinishedAt', ref.return_value.child.return_value.update.call_args.args[0])

    def test_csv_prefers_current_generated_file(self):
        root = Path(self.temp.name)
        self.assertEqual(default_local_ticket_csv(root), '')
        legacy = root / 'SAPAnalyticsReport(ZF8C06456D7698BCB54F44D).csv'
        legacy.touch()
        self.assertEqual(default_local_ticket_csv(root), str(legacy))
        (root / 'outputs').mkdir()
        generated = root / 'outputs/analysis_ticket_base.csv'
        generated.touch()
        self.assertEqual(default_local_ticket_csv(root), str(generated))

    def test_numeric_ticket_subject_does_not_replace_canonical_csv_id(self):
        path = Path(self.temp.name)/'tickets.csv'
        path.write_text('Ticket,C4C Ticket ID,Ticket ID,Created On\n5555556,21388,5555556,2026-01-01\n', encoding='utf-8')
        result = load_tickets_from_csv(str(path))
        self.assertEqual(result['Ticket ID'].tolist(), ['21388'])

    @unittest.skipUnless(os.name == 'nt', 'Windows batch integration')
    def test_daily_batch_continues_pdv_on_auxiliary_failure_only(self):
        root = Path(self.temp.name)
        (root/'run_daily_5pm_mandt800_rejection_filter.bat').write_bytes((ROOT/'run_daily_5pm_mandt800_rejection_filter.bat').read_bytes())
        for filename in ('fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py', 'firebase-service-account.json'):
            (root/filename).touch()
        for filename, code in (('check_deployment_readiness.py', '0'),
                ('ctm_v44_history_safe_mandt800_rejection_filter.py', "int(os.environ['FAKE_CORE_CODE'])"),
                ('refresh_dashboard_snapshots.py', '1')):
            (root/filename).write_text('import os,sys\nsys.exit('+code+')\n')
        (root/'run_pdv_dashboard_update.bat').write_bytes(b'@echo off\r\necho ran>pdv-ran.txt\r\nexit /b 0\r\n')
        for code, expected in ((0, 2), (2, 2), (1, 1)):
            marker = root/'pdv-ran.txt'
            marker.unlink(missing_ok=True)
            result = subprocess.run(['cmd', '/d', '/c', str(root/'run_daily_5pm_mandt800_rejection_filter.bat')], cwd=root,
                env={**os.environ, 'FAKE_CORE_CODE': str(code)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            self.assertEqual(marker.exists(), code != 1)


class PDVTests(unittest.TestCase):
    def test_incomplete_issue_scan_restarts_once_and_never_accepts_partial_data(self):
        complete = ([{'TicketID': '1'}], {'retrievalComplete': True})
        with patch('sync_issue_positions.fetch_type', side_effect=[IssueAIError('C4C scan incomplete: Z005'), complete]) as scan:
            self.assertEqual(fetch_type_with_recovery(Mock(), 'Z005', Mock()), complete)
            self.assertEqual(scan.call_count, 2)
        with patch('sync_issue_positions.fetch_type', side_effect=IssueAIError('C4C scan incomplete: Z005')) as scan:
            with self.assertRaises(IssueAIError):
                fetch_type_with_recovery(Mock(), 'Z005', Mock())
            self.assertEqual(scan.call_count, 2)
        with patch('sync_issue_positions.fetch_type', side_effect=IssueAIError('C4C GET failed: HTTP 403')) as scan:
            with self.assertRaises(IssueAIError):
                fetch_type_with_recovery(Mock(), 'Z005', Mock())
            self.assertEqual(scan.call_count, 1)

    def test_transient_read_retries_without_retrying_permissions(self):
        response = Mock()
        response.headers = {}
        response.read.return_value = b'{"ready":true}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(pdv.urllib.request, 'urlopen', side_effect=[TimeoutError(), response]) as call, patch.object(pdv.time, 'sleep'):
            self.assertEqual(pdv.read('path'), {'ready': True})
            self.assertEqual(call.call_count, 2)
        error = pdv.urllib.error.HTTPError('url', 403, 'forbidden', {}, None)
        with patch.object(pdv.urllib.request, 'urlopen', side_effect=error) as call:
            with self.assertRaises(pdv.urllib.error.HTTPError):
                pdv.read('path')
            self.assertEqual(call.call_count, 1)

    def test_corrupt_optional_cache_is_discarded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'cache.json'
            path.write_text('{incomplete')
            self.assertEqual(pdv.read_cache(path), {})


if __name__ == '__main__':
    unittest.main()
