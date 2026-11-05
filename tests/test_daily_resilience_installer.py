from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from build_daily_resilience_update import ROOT, FILES, build_manifest
from install_daily_resilience_update import prepare, install


class InstallerTests(unittest.TestCase):
    def test_mixed_pdv_versions_and_old_helpers_are_upgraded_with_backup(self):
        manifest = build_manifest()
        names = ['pdv_dashboard/refresh.py', 'pdv_dashboard/claim_trend_snapshot.py',
                 'pdv_dashboard/claim_trend_page.py', 'pdv_dashboard/pdv_scope.py',
                 'daily_refresh_support.py', 'vehicle_summary_publication.py']
        manifest = {'files': {n: manifest['files'][n] for n in names}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'run_daily_5pm_mandt800_rejection_filter.bat').touch()
            key = root/'firebase-service-account.json'
            key.write_text('private machine configuration')
            for name in names[:-1]:
                target = root/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('# different installed version\n')
            backup = install(root, manifest)
            self.assertTrue(backup.exists())
            for name in names:
                self.assertTrue((root/name).read_text(encoding='utf-8') == manifest['files'][name]['content'], name)
            self.assertEqual(key.read_text(), 'private machine configuration')
            install(root, manifest)

    def test_replace_failure_restores_already_installed_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'run_daily_5pm_mandt800_rejection_filter.bat').touch()
            for name in ('a.py', 'b.py'):
                (root/name).write_text('value=1\n')
            manifest = {'files': {n: {'changes': [{'before': 'value=1\n', 'after': 'value=2\n'}]} for n in ('a.py', 'b.py')}}
            original_replace = Path.replace
            def locked(path, target):
                if Path(target).name == 'b.py':
                    raise PermissionError('simulated locked file')
                return original_replace(path, target)
            with patch.object(Path, 'replace', locked), self.assertRaises(PermissionError):
                install(root, manifest)
            self.assertEqual((root/'a.py').read_text(), 'value=1\n')
            self.assertEqual((root/'b.py').read_text(), 'value=1\n')
            self.assertFalse(list(root.glob('*.resilience.tmp')))

    def test_invalid_python_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'run_daily_5pm_mandt800_rejection_filter.bat').touch()
            with self.assertRaises(SyntaxError):
                install(root, {'files': {'daily_refresh_support.py': {'content': 'def broken(', 'replace_existing': True}}})
            self.assertFalse((root/'daily_refresh_support.py').exists())

    def test_package_path_cannot_escape_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                prepare(Path(folder), {'files': {'../outside.py': {'content': 'x=1'}}})

    def test_preflight_preserves_machine_settings_and_is_idempotent(self):
        manifest = build_manifest()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in FILES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                original = subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=ROOT)
                target.write_bytes(original)
            fetch = root / FILES[0]
            with fetch.open('a', encoding='utf-8') as out:
                out.write('\n# machine-specific setting retained\n')
            prepare(root, manifest)
            backup = install(root, manifest)
            self.assertTrue(backup.is_file())
            self.assertTrue(fetch.read_text(encoding='utf-8').endswith('# machine-specific setting retained\n'))
            for name in FILES:
                expected = (ROOT/name).read_text(encoding='utf-8-sig')
                if name == FILES[0]:
                    expected += '\n# machine-specific setting retained\n'
                self.assertTrue((root/name).read_text(encoding='utf-8-sig') == expected, name)
            before = {name: (root/name).read_bytes() for name in manifest['files']}
            install(root, manifest)
            self.assertTrue(all((root/name).read_bytes() == content for name, content in before.items()))

    def test_conflict_prevents_all_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'run_daily_5pm_mandt800_rejection_filter.bat').touch()
            a = root/'a.py'
            b = root/'b.py'
            a.write_text('value=1\n')
            b.write_text('unexpected\n')
            manifest = {'files': {'a.py': {'changes': [{'before': 'value=1\n', 'after': 'value=2\n'}]},
                                  'b.py': {'changes': [{'before': 'value=1\n', 'after': 'value=2\n'}]}}}
            with self.assertRaises(ValueError):
                install(root, manifest)
            self.assertEqual(a.read_text(), 'value=1\n')
            self.assertFalse((root/'logs').exists())


if __name__ == '__main__':
    unittest.main()
