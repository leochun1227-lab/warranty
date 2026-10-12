import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from build_planning_top10_launcher import build
from install_planning_top10_update import install
from issue_ai import IssueAIError


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'run_daily_5pm_mandt800_rejection_filter.bat').write_text('@echo off\nexit /b 0\n')
        self.files = {'issue_position_bootstrap.json': b'{}', 'issue_position_config.py': b'# replacement\n'}

    def tearDown(self):
        self.temp.cleanup()

    def package(self):
        raw = io.BytesIO()
        with ZipFile(raw, 'w') as archive:
            for name, content in self.files.items():
                archive.writestr(name, content)
            archive.writestr('deployment_manifest.json', json.dumps({
                'requiredExistingFiles': ['firebase-service-account.json'],
                'sha256': {k: hashlib.sha256(v).hexdigest() for k, v in self.files.items()}}))
        return raw.getvalue()

    def test_missing_bundle_is_restored_and_existing_code_is_backed_up(self):
        (self.root / 'issue_position_config.py').write_bytes(b'# old\n')
        with ZipFile(io.BytesIO(self.package())) as archive, \
             patch('bootstrap_issue_ai.unseal', return_value={'C4C_ISSUE_USERNAME': 'u', 'C4C_ISSUE_PASSWORD': 'p'}):
            backup = install(self.root, archive)
        self.assertEqual((self.root / 'issue_position_bootstrap.json').read_bytes(), b'{}')
        with ZipFile(backup) as saved:
            self.assertEqual(saved.read('issue_position_config.py'), b'# old\n')
        self.assertEqual((self.root / 'issue_position_config.py').read_bytes(), b'# replacement\n')

    def test_wrong_firebase_key_stops_before_writing_files(self):
        with ZipFile(io.BytesIO(self.package())) as archive, \
             patch('bootstrap_issue_ai.unseal', side_effect=IssueAIError('different Firebase key')):
            with self.assertRaisesRegex(IssueAIError, 'different Firebase key'):
                install(self.root, archive)
        self.assertFalse((self.root / 'issue_position_bootstrap.json').exists())
        self.assertFalse((self.root / 'logs').exists())

    def test_path_escape_and_private_key_overwrite_are_rejected(self):
        for name in ('../outside.py', 'firebase-service-account.json'):
            with self.subTest(name=name):
                self.files[name] = b'forbidden'
                with ZipFile(io.BytesIO(self.package())) as archive:
                    with self.assertRaisesRegex(ValueError, 'invalid destination'):
                        install(self.root, archive)
                del self.files[name]

    def test_single_bat_extracts_embedded_installer_and_runs_only_local_stub(self):
        package = self.root / 'input.zip'
        package.write_bytes(self.package())
        # Exercise actual cmd parsing, multiline payload and paths containing spaces.
        launcher = self.root / 'Top10 Repair.bat'
        fake_installer = b"from pathlib import Path\nPath('installer_ran').write_text('ok')\n"
        original = Path.read_bytes
        def content(p):
            return fake_installer if p.name == 'install_planning_top10_update.py' else original(p)
        with patch.object(Path, 'read_bytes', content):
            build(package, launcher)
        result = subprocess.run(['cmd', '/d', '/c', str(launcher)], input='\n',
                                text=True, capture_output=True, timeout=20, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / 'installer_ran').is_file())
        self.assertIn('SUCCESS:', result.stdout)


if __name__ == '__main__':
    unittest.main()
