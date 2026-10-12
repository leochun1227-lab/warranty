import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from issue_ai import IssueAIError
from issue_ai_daily import run_after_daily_fetch
from issue_position_config import load_position_config
from refresh_dashboard_snapshots import top10_publication_errors


class PositionDeploymentTests(unittest.TestCase):
    def test_existing_credentials_work_even_with_both_old_flags_disabled(self):
        credentials = {'C4C_ISSUE_USERNAME': 'user', 'C4C_ISSUE_PASSWORD': 'secret'}
        with patch('issue_position_config.load_config', return_value={
            **credentials, 'C4C_ISSUE_AI_ENABLED': '0', 'C4C_ISSUE_POSITION_ENABLED': '0',
            'OPENAI_API_KEY': 'must-not-be-used'}):
            self.assertEqual(load_position_config('unused'), credentials)

    def test_visible_bundle_recovers_missing_account_settings_without_saving_secrets(self):
        credentials = {'C4C_ISSUE_USERNAME': 'user', 'C4C_ISSUE_PASSWORD': 'secret'}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = root / 'issue_position_bootstrap.json'
            bundle.write_text('{}')
            with patch('issue_position_config.load_config', return_value={'C4C_ISSUE_USERNAME': 'incomplete'}), \
                 patch('issue_position_config.unseal', return_value=credentials) as decrypt:
                self.assertEqual(load_position_config('key.json', root=root), credentials)
                decrypt.assert_called_once_with({}, 'key.json')
            self.assertEqual(list(root.iterdir()), [bundle])

    def test_missing_credentials_fail_daily_instead_of_silently_skipping(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('issue_position_config.load_config', return_value={}):
            with self.assertRaisesRegex(IssueAIError, 'Top10 credentials missing'):
                load_position_config('key', root=folder)
        with patch('issue_position_config.load_position_config', side_effect=IssueAIError('missing')), \
             patch('issue_ai_daily.subprocess.run') as run:
            with self.assertRaises(IssueAIError):
                run_after_daily_fetch('tickets', 'db', 'key')
            run.assert_not_called()

    def test_failed_top10_process_fails_daily(self):
        with patch('issue_position_config.load_position_config', return_value={}), \
             patch('issue_ai_daily.subprocess.run', return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'Issue Position sync failed'):
                run_after_daily_fetch('tickets', 'db', 'key')

    def test_top10_must_match_current_claim_source_not_just_a_success_status(self):
        versions = dict(team='t1', core='c1', so='s1')
        snapshot = dict(schema='issue-startup-v2', categoryDimension='issue_position',
                        reportingBasis='approved_on', sourceVersion='g1', generatedAt='g1',
                        claimSourceVersion=json.dumps(['t1', 'c1', 's1']), ticketDataAsOf='c1')
        def envelope():
            return dict(sourceVersion='g1', generatedAt='g1', data=json.dumps(snapshot))
        self.assertEqual(top10_publication_errors(versions, envelope()), [])
        self.assertTrue(top10_publication_errors({**versions, 'core': 'c2'}, envelope()))
        self.assertTrue(top10_publication_errors(versions, None))
        self.assertTrue(top10_publication_errors(versions, {'data': 'broken'}))
        snapshot['ticketDataAsOf'] = 'c0'
        self.assertTrue(top10_publication_errors(versions, envelope()))


if __name__ == '__main__':
    unittest.main()
