"""Regression coverage for incomplete Planning overlays (no network)."""
import unittest
from pathlib import Path

from build_issue_ai_update import BASE_FILES, FILES, validate_package_files
from issue_ai import IssueAIError

ROOT = Path(__file__).resolve().parents[1]


class PlanningPackageTests(unittest.TestCase):
    def test_all_packaged_paths_exist_and_are_relative(self):
        validate_package_files(FILES)
        for name in FILES:
            self.assertTrue((ROOT / name).is_file(), name)
            self.assertTrue((ROOT / name).resolve().is_relative_to(ROOT), name)
        self.assertFalse(set(FILES) & BASE_FILES)

    def test_missing_daily_dependency_is_rejected(self):
        for name in ('build_claim_startup.py', 'build_claim_startup.mjs',
                     'claim-trend-ticket-metrics.js', 'delivery_flow_aggregator.py',
                     'refresh_dashboard_snapshots.py', 'tests/startup-cache.test.cjs',
                     'model-overview.js', 'claim-overview.css',
                     'run_pdv_dashboard_update.bat', 'pdv_dashboard/pdv_scope.py'):
            with self.subTest(name=name), self.assertRaises(IssueAIError):
                validate_package_files(set(FILES) - {name})

    def test_original_credentials_and_generated_data_are_not_overwritten(self):
        for name in BASE_FILES:
            with self.subTest(name=name), self.assertRaises(IssueAIError):
                validate_package_files(set(FILES) | {name})


if __name__ == '__main__':
    unittest.main()
