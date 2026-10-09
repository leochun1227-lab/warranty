import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from bootstrap_issue_ai import ensure_private_configuration, seal, unseal
from issue_ai import IssueAIError, read_env
from issue_ai_daily import run_after_daily_fetch

CONFIG={'OPENAI_API_KEY':'fake-api-key','OPENAI_BASE_URL':'https://api.openai.com/v1',
        'ISSUE_AI_MODEL':'gpt-5.5','C4C_ISSUE_USERNAME':'fake-user','C4C_ISSUE_PASSWORD':'fake-password',
        'C4C_ISSUE_AI_ENABLED':'1','ISSUE_AI_MAX_PER_RUN':'250','ISSUE_AI_BATCH_SIZE':'10'}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.root=self.base/'warranty';self.root.mkdir()
        self.target=self.base/'account'/'settings.env'
        self.sa=self.root/'firebase-service-account.json'
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        self.pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()).decode()
        self.sa.write_text(json.dumps({'private_key':self.pem}),encoding='utf-8')
        self.envelope=seal(CONFIG,self.sa)
        self.bundle=self.root/'.issue-ai'/'bootstrap.json'
        self.bundle.parent.mkdir()
        self.bundle.write_text(json.dumps(self.envelope),encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def run_bootstrap(self):
        return ensure_private_configuration(self.sa,root=self.root,target=self.target)

    def test_portable_envelope_contains_no_plaintext_and_roundtrips(self):
        encoded=json.dumps(self.envelope)
        for value in [CONFIG['OPENAI_API_KEY'],CONFIG['C4C_ISSUE_PASSWORD'],self.pem]:
            self.assertNotIn(value,encoded)
        self.assertEqual(unseal(self.envelope,self.sa),CONFIG)

    def test_position_package_needs_no_openai_key(self):
        config={'C4C_ISSUE_USERNAME':'u','C4C_ISSUE_PASSWORD':'p','C4C_ISSUE_POSITION_ENABLED':'1','C4C_ISSUE_AI_ENABLED':'0'}
        self.assertEqual(unseal(seal(config,self.sa),self.sa),config)

    def test_first_daily_creates_private_settings_without_network(self):
        with patch('requests.sessions.Session.request',side_effect=AssertionError('No network during bootstrap')):
            self.assertEqual(self.run_bootstrap(),'created')
        self.assertEqual(read_env(self.target),CONFIG)
        self.assertFalse(list(self.target.parent.glob('*.tmp')))
        self.assertTrue(self.bundle.exists()) # Keep encrypted bootstrap for another task account.

    def test_existing_settings_and_explicit_opt_out_are_preserved(self):
        self.target.parent.mkdir();self.target.write_text('C4C_ISSUE_AI_ENABLED="0"\n',encoding='utf-8')
        self.sa.unlink()
        self.assertEqual(self.run_bootstrap(),'existing')
        self.assertEqual(read_env(self.target),{'C4C_ISSUE_AI_ENABLED':'0'})

    def test_another_windows_account_can_bootstrap_from_same_package(self):
        self.run_bootstrap()
        second=self.base/'other-account'/'settings.env'
        self.assertEqual(ensure_private_configuration(self.sa,root=self.root,target=second),'created')
        self.assertEqual(read_env(second),CONFIG)

    def test_wrong_firebase_key_is_rejected_without_saving(self):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        self.sa.write_text(json.dumps({'private_key':key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()}),encoding='utf-8')
        with self.assertRaisesRegex(IssueAIError,'different Firebase key'):
            self.run_bootstrap()
        self.assertFalse(self.target.exists())

    def test_tampered_ciphertext_rejected_without_saving(self):
        raw=bytearray(base64.b64decode(self.envelope['ciphertext']));raw[-1]^=1
        self.envelope['ciphertext']=base64.b64encode(raw).decode()
        self.bundle.write_text(json.dumps(self.envelope),encoding='utf-8')
        with self.assertRaisesRegex(IssueAIError,'could not be verified'):
            self.run_bootstrap()
        self.assertFalse(self.target.exists())

    def test_no_plaintext_in_project_and_missing_bundle_skips(self):
        with self.assertRaisesRegex(IssueAIError,'outside the project'):
            ensure_private_configuration(self.sa,root=self.root,target=self.root/'secret.env')
        self.bundle.unlink()
        self.assertEqual(self.run_bootstrap(),'absent')
        self.assertFalse(self.target.exists())

    def test_concurrent_creation_does_not_overwrite_other_process(self):
        def race(src,dst):
            Path(dst).write_text('C4C_ISSUE_AI_ENABLED="0"\n',encoding='utf-8')
            raise FileExistsError()
        with patch('bootstrap_issue_ai.os.link',side_effect=race):
            self.assertEqual(self.run_bootstrap(),'existing')
        self.assertEqual(read_env(self.target),{'C4C_ISSUE_AI_ENABLED':'0'})
        self.assertFalse(list(self.target.parent.glob('*.tmp')))

    def test_failed_publish_leaves_no_half_config_or_temp_file(self):
        with patch('bootstrap_issue_ai.os.link',side_effect=OSError('simulated failure')):
            with self.assertRaisesRegex(IssueAIError,'could not be saved'):
                self.run_bootstrap()
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.target.parent.glob('*.tmp')))

    def test_daily_auto_bootstrap_then_uses_same_interpreter_and_config(self):
        import sys
        config={**CONFIG,'envPath':str(self.target)}
        with patch('bootstrap_issue_ai.ensure_private_configuration',return_value='created') as bootstrap, \
             patch('issue_ai.load_config',return_value=config), \
             patch('issue_ai_daily.subprocess.run',return_value=Mock(returncode=0)) as runner:
            run_after_daily_fetch('c4cTickets_test','https://example.invalid','existing-sa.json')
        bootstrap.assert_called_once_with('existing-sa.json')
        args=runner.call_args.args[0]
        self.assertEqual(args[0],sys.executable)
        self.assertIn('--publish',args)
        self.assertTrue(args[1].endswith('sync_issue_positions.py'))
        self.assertIn(str(self.target),args)
        self.assertNotIn(CONFIG['OPENAI_API_KEY'],str(runner.call_args))


if __name__=='__main__':
    unittest.main()
