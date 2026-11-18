import contextlib
import importlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import ssl
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import setup
import setup_user
import setup_runtime
import prerequisites
import common
import https_proxy
import nginx_shared
import build_release


class PrerequisiteTests(unittest.TestCase):
    def user(self, home):
        import pwd
        return {'username': pwd.getpwuid(os.geteuid()).pw_name, 'uid': os.geteuid(), 'gid': os.getegid(), 'home': str(home), 'login_home': str(home)}

    def test_noninteractive_missing_seller_provides_settings_save_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), patch.object(setup.sys.stdin, 'isatty', return_value=False):
            args = setup.parser().parse_args([])
            with self.assertRaises(prerequisites.Required) as caught:
                setup.seller_input(args, self.user(tmp))
            error = caught.exception
            self.assertEqual(error.code, 'SELLER_KEY_REQUIRED')
            for text in ['https://openany.si/seller-console/settings#seller-key', '--save-seller-key', '--seller-key-file', '--user', '--home', 'Do not rotate', 'Never paste']:
                self.assertIn(text, str(error))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_missing_supplied_key_gets_same_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = setup.parser().parse_args(['--seller-key-file', tmp + '/absent.key'])
            with self.assertRaisesRegex(prerequisites.Required, 'seller-console/settings#seller-key'):
                setup.seller_input(args, self.user(tmp), interactive=False)

    def test_saved_prod_key_is_reused_without_prompt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), patch.object(setup.getpass, 'getpass') as prompt:
            setup_user.configure(Path(tmp), 8642, 'prod', 'fixture-production-seller')
            args = setup.parser().parse_args([])
            self.assertEqual(setup.seller_input(args, self.user(tmp), interactive=False), 'fixture-production-seller')
            prompt.assert_not_called()

    def test_hidden_save_is_private_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'private' / 'seller.key'
            args = setup.parser().parse_args(['--save-seller-key', '--seller-key-file', str(path)])
            secret = 'fixture-private-seller-token'
            with patch.object(setup.sys.stdin, 'isatty', return_value=True), patch.object(setup.getpass, 'getpass', return_value=secret):
                result = setup.save_seller_key(args, self.user(tmp))
            self.assertEqual(path.read_text(), secret + '\n')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertNotIn(secret, json.dumps(result))
            self.assertFalse(result['setup_started'])
            with patch.object(setup.getpass, 'getpass') as prompt, self.assertRaises(common.Failure):
                setup.save_seller_key(args, self.user(tmp))
            prompt.assert_not_called()
            self.assertEqual(path.read_text(), secret + '\n')

    def test_noninteractive_save_requests_independent_terminal(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(setup.sys.stdin, 'isatty', return_value=False):
            args = setup.parser().parse_args(['--save-seller-key', '--seller-key-file', tmp + '/private/seller.key'])
            with self.assertRaises(prerequisites.Required):
                setup.save_seller_key(args, self.user(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_save_does_not_change_an_existing_public_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(setup.sys.stdin, 'isatty', return_value=True), patch.object(setup.getpass, 'getpass') as prompt:
            directory = Path(tmp) / 'public'; directory.mkdir(mode=0o755); directory.chmod(0o755)
            args = setup.parser().parse_args(['--save-seller-key', '--seller-key-file', str(directory/'key')])
            with self.assertRaisesRegex(common.Failure, '0700'):
                setup.save_seller_key(args, self.user(tmp))
            prompt.assert_not_called()
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)

    def test_missing_hermes_home_does_not_initialize_it(self):
        import pwd
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)/'absent'
            with self.assertRaises(prerequisites.Required) as caught:
                setup_runtime.identity(pwd.getpwuid(os.geteuid()).pw_name, home)
            self.assertEqual(caught.exception.code, 'HERMES_REQUIRED')
            self.assertIn('hermes model', str(caught.exception))
            self.assertIn('final exit status', str(caught.exception))
            self.assertFalse(home.exists())

    def test_missing_tls_site_offers_both_branches(self):
        with self.assertRaises(prerequisites.Required) as caught:
            nginx_shared.select_site({'/fixture/http.conf': 'server { listen 80; server_name hermes.example.com; }'}, 'hermes.example.com')
        self.assertEqual(caught.exception.code, 'HTTPS_BRANCH_REQUIRED')
        for text in ['--new-site', '--public-ip', '--email', 'Do not delete', 'not HTTPS readiness']:
            self.assertIn(text, str(caught.exception))

    def test_tls_failure_is_actionable_and_does_not_skip_validation(self):
        opener = SimpleNamespace(open=lambda *a, **k: (_ for _ in ()).throw(ssl.SSLError('fixture handshake failed')))
        with self.assertRaises(prerequisites.Required):
            https_proxy.verify_tls('hermes.example.com', opener)

    def test_dashboard_auth_response_still_proves_tls_only(self):
        error = urllib.error.HTTPError('https://hermes.example.com', 401, 'unauthorized', {}, None)
        opener = SimpleNamespace(open=lambda *a, **k: (_ for _ in ()).throw(error))
        result = https_proxy.verify_tls('hermes.example.com', opener)
        self.assertTrue(result['tls_handshake_verified'])
        self.assertEqual(result['external_reachability'], 'requires_independent_host')

    def test_preflight_missing_key_cannot_report_checked_or_queue(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            calls=[]
            runtime=SimpleNamespace(hermes='/fixture/hermes', cli={}, discover_hermes=lambda: None, discover_cli=lambda: None,
                inspect_site=lambda: {'domain':'hermes.example.com','path':'/fixture/site','sha256':'fixture'}, verify_existing_tls=lambda: calls.append('tls'))
            with patch.object(https_proxy,'check_os'), patch.object(setup_runtime,'identity',return_value=self.user(tmp)), patch.object(setup_runtime,'Runtime',return_value=runtime), patch.object(setup,'install_bundle') as bundle:
                with self.assertRaises(prerequisites.Required):
                    setup.queue(setup.parser().parse_args(['--check']))
                bundle.assert_not_called()
            self.assertEqual(calls, ['tls'])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_new_site_preflight_uses_dns_and_conflict_check_without_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            runtime=SimpleNamespace(hermes='/fixture/hermes', cli={}, discover_hermes=lambda: None, discover_cli=lambda: None,
                inspect_new_site=lambda: calls.append('new-site-conflicts'))
            args=setup.parser().parse_args(['--check','--new-site','--domain','hermes.example.com','--public-ip','192.0.2.1','--email','ops@example.com'])
            with patch.object(https_proxy,'check_os'), patch.object(https_proxy,'check_dns') as dns, patch.object(setup_runtime,'identity',return_value=self.user(tmp)), patch.object(setup_runtime,'Runtime',return_value=runtime), patch.object(setup,'seller_input',return_value='fixture'):
                result=setup.queue(args)
            dns.assert_called_once_with('hermes.example.com',['192.0.2.1'])
            self.assertEqual(calls,['new-site-conflicts'])
            self.assertEqual(result['runs_url'],'https://hermes.example.com/openany/v1/runs')
            self.assertFalse(result['system_changes'])

    def test_cli_json_failure_has_actionable_fields(self):
        output=io.StringIO()
        with patch.object(sys,'argv',['setup.py','--check']), patch.object(setup,'queue',side_effect=prerequisites.seller_required()), contextlib.redirect_stderr(output):
            self.assertEqual(setup.main(),1)
        body=json.loads(output.getvalue())
        self.assertEqual(body['code'],'SELLER_KEY_REQUIRED')
        self.assertTrue(body['next_steps'])
        self.assertFalse(body['setup_started'])

    def test_release_hashes_are_deterministic_and_match_source_state(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first=build_release.build(one,'2026-11-18')
            second=build_release.build(two,'2026-11-18')
            self.assertEqual(first,second)
            self.assertEqual(first['release_state'],build_release.RELEASE_STATE)
            self.assertEqual(first['verification_scope'],'local_tests')
            self.assertEqual(first['version'],setup.VERSION)

    def test_cli_package_must_match_tested_poll_version_not_only_command_help(self):
        import cli_delivery
        cli_delivery.validate_package('prod', 'openany-cli', '0.3.12')
        for name, version in [('openany-cli','0.3.11'),('openany-cli','0.3.13'),('openany-cli-dev','0.3.12'),(None,None)]:
            with self.subTest(name=name,version=version), self.assertRaisesRegex(common.Failure,'poll compatibility'):
                cli_delivery.validate_package('prod',name,version)


if __name__ == '__main__':
    unittest.main()
