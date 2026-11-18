import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class SetupTests(unittest.TestCase):
    def module(self, name):
        self.assertTrue((ROOT / "scripts" / (name + ".py")).exists(), name + " is not implemented")
        return importlib.import_module(name)

    def test_seller_credentials_use_environment_specific_private_file(self):
        user = self.module("setup_user")
        for environment, variable in (("prod", "OPENANY_SELLER_KEY"), ("dev", "OPENANY_DEV_SELLER_KEY")):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                original = "API_SERVER_KEY=existing-hermes-key-1234567890\nMODEL_NAME=keep-model\n"
                (home / ".env").write_text(original)
                result = user.configure(home, 8642, environment, "fixture-seller-token-123456789")
                data = user.read_environment(Path(result["seller_env_file"]))
                self.assertEqual(data[variable], "fixture-seller-token-123456789")
                self.assertNotIn("OPENANY_DEV_SELLER_KEY" if environment == "prod" else "OPENANY_SELLER_KEY", data)
                self.assertNotIn("fixture-seller-token", json.dumps(result))
                self.assertIn("MODEL_NAME=keep-model", (home / ".env").read_text())
                self.assertEqual((home / "openany-api.key").read_text().strip(), "existing-hermes-key-1234567890")
                self.assertEqual(stat.S_IMODE(Path(result["seller_env_file"]).stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(Path(result["task_root"]).stat().st_mode), 0o700)
                repeated = user.configure(home, 8642, environment, "fixture-seller-token-123456789")
                self.assertFalse(repeated["seller_changed"])

    def test_missing_seller_key_does_not_generate_or_modify_api_key(self):
        user, common = self.module("setup_user"), importlib.import_module("common")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.assertRaises(common.Failure):
                user.configure(home, 8642, "prod", "")
            self.assertFalse((home / ".env").exists())

    def test_seller_shell_metacharacters_remain_literal(self):
        user = self.module("setup_user")
        with tempfile.TemporaryDirectory() as tmp:
            key = 'fixture!#"\\$SAFE/+=_seller'
            result = user.configure(Path(tmp), 8642, "dev", key)
            env = user.read_environment(Path(result["seller_env_file"]))
            self.assertEqual(env["OPENANY_DEV_SELLER_KEY"], key)

    def test_private_secret_file_rejects_public_permissions(self):
        runtime = self.module("setup_runtime")
        common = importlib.import_module("common")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seller"
            path.write_text("fixture-seller-token")
            path.chmod(0o644)
            with self.assertRaises(common.Failure):
                runtime.read_secret(path, os.geteuid())
            path.chmod(0o600)
            self.assertEqual(runtime.read_secret(path, os.geteuid()), "fixture-seller-token")

    def test_cli_probe_binds_selected_environment_and_requires_node22(self):
        runtime = self.module("setup_runtime")
        config = {"identity": {"username": "ubuntu", "uid": 1000, "gid": 1000, "home": "/home/ubuntu/.hermes", "login_home": "/home/ubuntu"}, "environment": "dev"}
        with patch.dict(os.environ):
            runner = runtime.Runtime(config, Path("/opt/bundle"), Path("/tmp/state"))
            def as_user(args, *values, **kwargs):
                if "-c" in args:
                    value = json.dumps({"node": "/node/bin/node", "npm": "/node/npm.js", "cli": "/tools/openany-dev.js"})
                elif args[-1] == "--version": value = "v22.22.0"
                else: value = "https://api-dev.openany.si\nseller hermes serve\nseller tasks upload"
                return SimpleNamespace(returncode=0, stdout=value)
            runner.as_user = as_user
            result = runner.discover_cli()
            self.assertFalse(result["install_required"])
            self.assertEqual(result["command"], "openany-dev")

    def test_worker_job_is_separate_from_gateway_scope(self):
        setup = self.module("setup")
        args = setup.systemd_job_command(Path("/opt/openany/bundle"), "a" * 24)
        self.assertEqual(args[0], "systemd-run")
        self.assertNotIn("--scope", args)
        self.assertNotIn("--user", args)
        self.assertIn("--no-block", args)
        self.assertTrue(any("ExecStartPre=/usr/bin/sleep 3" in item for item in args))
        self.assertIn("--worker", args)

    def test_worker_runs_configuration_restart_and_https_in_order(self):
        setup = self.module("setup")
        fake = FakeRuntime()
        events = []
        result = setup.run_steps(fake, lambda stage, state: events.append((stage, state)))
        self.assertEqual(fake.calls, ["preflight", "gateway", "configure", "bind", "runtime_ready", "restart", "wait_api", "record_runtime", "delivery", "https_ready", "https", "verify_https", "listing"])
        self.assertEqual(result["base_url"], "https://fixture.example.com")
        self.assertEqual(result["seller_key"]["remote_verified"], False)
        self.assertIn(("gateway", "restarted"), events)

    def test_ready_installation_skips_restart_and_certificate_setup(self):
        setup = self.module("setup")
        fake = FakeRuntime(ready=True)
        events = []
        setup.run_steps(fake, lambda stage, state: events.append((stage, state)))
        self.assertNotIn("restart", fake.calls)
        self.assertNotIn("https", fake.calls)
        self.assertIn(("gateway", "skipped_verified"), events)
        self.assertIn(("https", "skipped_verified"), events)

    def test_failure_stops_before_https(self):
        setup = self.module("setup")
        common = importlib.import_module("common")
        fake = FakeRuntime()
        def failure():
            raise common.Failure("fixture restart failed")
        fake.restart_gateway = failure
        with self.assertRaises(common.Failure):
            setup.run_steps(fake, lambda *args: None)
        self.assertNotIn("https", fake.calls)

    def test_cli_delivery_failure_stops_before_https(self):
        setup = self.module("setup")
        common = importlib.import_module("common")
        fake = FakeRuntime()
        def failure(): raise common.Failure("fixture collector failed")
        fake.prepare_delivery = failure
        with self.assertRaises(common.Failure): setup.run_steps(fake, lambda *args: None)
        self.assertNotIn("https", fake.calls)

    def test_readonly_existing_site_check_needs_no_new_dns_or_email(self):
        setup = self.module("setup")
        with tempfile.TemporaryDirectory() as tmp:
            user = {"username":"fixture", "uid":os.geteuid(), "gid":os.getegid(), "home":tmp, "login_home":tmp}
            fake = SimpleNamespace(hermes="/tools/hermes",cli={"command":"openany-dev","install_required":False})
            fake.discover_hermes = lambda: None
            fake.discover_cli = lambda: None
            fake.inspect_site = lambda: {"domain":"existing.example.com", "path":"/etc/nginx/site.conf", "sha256":"fixture"}
            args = setup.parser().parse_args(["--check", "--environment", "dev"])
            with patch.object(setup.https_proxy,"check_os"), patch.object(setup.setup_runtime,"identity",return_value=user), patch.object(setup.setup_runtime,"Runtime",return_value=fake):
                result = setup.queue(args)
            self.assertEqual(result["runs_url"],"https://existing.example.com/openany/v1/runs")
            self.assertFalse(result["system_changes"])
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_cli_accepts_seller_key_path_but_not_plaintext_key(self):
        setup = self.module("setup")
        args = setup.parser().parse_args(["--domain", "fixture.example.com", "--public-ip", "192.0.2.1", "--email", "ops@example.com", "--seller-key-file", "/private/seller", "--environment", "dev"])
        self.assertEqual(args.environment, "dev")
        self.assertFalse(hasattr(args, "seller_key"))

    def test_existing_site_cli_options_need_no_new_certificate_inputs(self):
        setup = self.module("setup")
        args = setup.parser().parse_args(["--domain", "existing.example.com", "--environment", "dev", "--skill", "space-xhs-title", "--cli-bin", "/tools/openany-dev", "--nginx-site", "/etc/nginx/sites-available/hermes"])
        self.assertFalse(args.new_site)
        self.assertEqual(args.skills, ["space-xhs-title"])
        self.assertFalse(args.public_ip)
        self.assertIsNone(args.email)

    def test_wait_api_distinguishes_missing_process_credentials_from_api_failure(self):
        runtime = self.module("setup_runtime")
        common = importlib.import_module("common")
        config = {"identity": {"username":"root", "uid":0, "gid":0, "home":"/root/.hermes", "login_home":"/root"}}
        with patch.dict(os.environ):
            runner = runtime.Runtime(config, Path("/opt/bundle"), Path("/tmp/state"))
            runner.local_api = lambda: {"authenticated": True}
            runner.credentials_loaded = lambda: False
            with patch.object(runtime.time,"monotonic",side_effect=[0,0,121]), patch.object(runtime.time,"sleep"):
                with self.assertRaisesRegex(common.Failure,"credentials.*EnvironmentFile"):
                    runner.wait_api()

    def test_bootstrap_help_is_available_without_system_changes(self):
        self.assertTrue((ROOT / "install.sh").is_file())
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--seller-key-file", result.stdout)
        self.assertIn("--status", result.stdout)
        self.assertIn("existing Hermes", result.stdout)

    def test_gateway_matching_uses_home_and_execution_account(self):
        runtime = self.module("setup_runtime")
        config = {"identity": {"username": "ubuntu", "uid": 1000, "gid": 1000, "home": "/home/ubuntu/.hermes", "login_home": "/home/ubuntu"}, "gateway_scope": "auto"}
        with patch.dict(os.environ):
            runner = runtime.Runtime(config, Path("/opt/bundle"), Path("/tmp/state"))
            def control(scope, args, allow_failure=False):
                if args[0] == "list-unit-files":
                    return SimpleNamespace(returncode=0, stdout="hermes-gateway.service enabled\nhermes-gateway-other.service enabled\n")
                if args[1] == "hermes-gateway-other.service":
                    return SimpleNamespace(returncode=0, stdout="User=ubuntu\nEnvironment=HERMES_HOME=/home/ubuntu/.hermes-other\n")
                owner = "someone-else" if scope == "system" else ""
                return SimpleNamespace(returncode=0, stdout="User=" + owner + "\nEnvironment=HERMES_HOME=/home/ubuntu/.hermes\n")
            runner.control = control
            self.assertEqual(runner.find_gateways(), [("user", "hermes-gateway.service")])

    def test_gateway_with_spaces_in_home_is_matched_without_shell_execution(self):
        runtime = self.module("setup_runtime")
        self.assertTrue(runtime.matching_home('OTHER=value "HERMES_HOME=/home/ubuntu/a space"', "/home/ubuntu/a space", "/home/ubuntu"))
        self.assertFalse(runtime.matching_home("HERMES_HOME=/home/ubuntu/other", "/home/ubuntu/.hermes", "/home/ubuntu"))

    def test_runtime_user_commands_strip_root_environment_and_use_stdin_for_secret(self):
        runtime = self.module("setup_runtime")
        config = {"environment": "dev", "port": 8642, "identity": {"username": "ubuntu", "uid": 1000, "gid": 1000, "home": "/home/ubuntu/.hermes", "login_home": "/home/ubuntu"}}
        with patch.dict(os.environ), patch.object(runtime.os, "geteuid", return_value=0):
            runner = runtime.Runtime(config, Path("/opt/bundle"), Path("/tmp/state"))
            calls = []
            def command(args, payload=None, allow_failure=False, **kwargs):
                calls.append((args, payload))
                return SimpleNamespace(returncode=0, stdout='{"ok":true}')
            runner.command = command
            runner.helper("configure", {"seller_key": "fixture-seller-secret"})
            args, data = calls[-1]
            self.assertEqual(args[:4], ["runuser", "-u", "ubuntu", "--"])
            self.assertIn("-i", args)
            self.assertIn("HOME=/home/ubuntu", args)
            self.assertNotIn("fixture-seller-secret", " ".join(args))
            self.assertIn("fixture-seller-secret", data)

    def test_ambiguous_gateways_fail_instead_of_restarting_both(self):
        runtime, common = self.module("setup_runtime"), importlib.import_module("common")
        cfg = {"identity": {"username": "ubuntu", "uid": 1000, "gid": 1000, "home": "/home/ubuntu/.hermes", "login_home": "/home/ubuntu"}}
        with patch.dict(os.environ):
            runner = runtime.Runtime(cfg, Path("/opt/bundle"), Path("/tmp/state"))
            runner.find_gateways = lambda: [("system", "hermes-gateway.service"), ("user", "hermes-gateway.service")]
            with self.assertRaises(common.Failure):
                runner.prepare_gateway()

    def test_stale_runtime_fingerprint_forces_restart_before_skip(self):
        runtime = self.module("setup_runtime")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            home = Path(tmp)
            (home / ".openany-provider").mkdir()
            (home / ".openany-provider/runtime-prod.json").write_text('{"fingerprint":"old"}')
            cfg = {"environment": "prod", "identity": {"uid": os.geteuid(), "home": str(home)}}
            runner = runtime.Runtime(cfg, Path("/opt/bundle"), Path("/tmp/state"))
            runner.fingerprint, runner.binding_changed = "new", False
            self.assertFalse(runner.runtime_ready())

    def test_existing_seller_key_is_reused_only_for_selected_environment(self):
        setup, user = self.module("setup"), self.module("setup_user")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            result = user.configure(home, 8642, "dev", "fixture-dev-seller-token")
            args = setup.parser().parse_args(["--environment", "dev"])
            data = {"home": str(home), "uid": os.geteuid()}
            self.assertEqual(setup.seller_input(args, data), "fixture-dev-seller-token")
            self.assertNotIn("OPENANY_SELLER_KEY", user.read_environment(Path(result["seller_env_file"])))

    def test_dropin_uses_literal_absolute_path_and_never_contains_key_value(self):
        user = self.module("setup_user")
        text = user.dropin_text(Path("/home/a space/100%/prod.env"))
        self.assertIn('EnvironmentFile=/home/a space/100%%/prod.env\n', text)
        self.assertNotIn('EnvironmentFile="', text)
        self.assertNotIn("OPENANY_SELLER_KEY=", text)

    def test_systemd_environment_paths_are_utf8_not_json_unicode_escapes(self):
        user = self.module("setup_user")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "\u4e2d\u6587"
            home.mkdir()
            result = user.configure(home, 8642, "prod", "fixture-seller-token")
            self.assertNotIn("\\u", Path(result["seller_env_file"]).read_text())
            self.assertNotIn("\\u", user.dropin_text(result["seller_env_file"]))

    def test_installed_runtime_bundle_is_readable_under_private_umask(self):
        setup = self.module("setup")
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.umask(0o077)
            try:
                bundle = setup.install_bundle(Path(tmp) / "installed")
            finally:
                os.umask(previous)
            for path in (bundle.parent, bundle, bundle / "scripts"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((bundle / "scripts/setup_user.py").stat().st_mode), 0o644)

    def test_existing_user_gateway_gets_persistent_boot_configuration(self):
        runtime = self.module("setup_runtime")
        config = {"identity": {"username": "ubuntu", "uid": 1000, "gid": 1000, "home": "/home/ubuntu/.hermes", "login_home": "/home/ubuntu"}}
        with patch.dict(os.environ):
            runner = runtime.Runtime(config, Path("/opt/bundle"), Path("/tmp/state"))
            runner.find_gateways = lambda: [("user", "hermes-gateway.service")]
            commands = []
            def command(args, *values, **kwargs):
                commands.append(args)
                return SimpleNamespace(returncode=0, stdout="no\n")
            def control(scope, args, allow_failure=False):
                commands.append(["systemctl", scope, *args])
                return SimpleNamespace(returncode=1 if args[0] == "is-enabled" else 0, stdout="")
            runner.command, runner.control = command, control
            runner.prepare_gateway()
            self.assertIn(["loginctl", "enable-linger", "ubuntu"], commands)
            self.assertIn(["systemctl", "user", "enable", "hermes-gateway.service"], commands)

    def test_loaded_process_environment_must_match_selected_seller_key(self):
        runtime = self.module("setup_runtime")
        expected = {"OPENANY_DEV_SELLER_KEY": "fixture-dev-token", "OPENANY_TASK_ROOT": "/private/tasks/dev"}
        good = b"OTHER=ignored\x00OPENANY_DEV_SELLER_KEY=fixture-dev-token\x00OPENANY_TASK_ROOT=/private/tasks/dev\x00"
        self.assertTrue(runtime.process_credentials_match(good, expected))
        self.assertFalse(runtime.process_credentials_match(good.replace(b"fixture-dev-token", b"wrong-token"), expected))
        self.assertFalse(runtime.process_credentials_match(good.replace(b"OPENANY_DEV_SELLER_KEY", b"OPENANY_SELLER_KEY"), expected))


class FakeRuntime:
    def __init__(self, ready=False):
        self.calls, self.ready = [], ready
    def preflight(self): self.calls.append("preflight")
    def prepare_gateway(self): self.calls.append("gateway")
    def configure_user(self):
        self.calls.append("configure")
        return {"seller_env_file": "/private/prod.env", "api_key_file": "/private/api.key", "seller_changed": not self.ready}
    def bind_gateway(self, config): self.calls.append("bind")
    def runtime_ready(self): self.calls.append("runtime_ready"); return self.ready
    def restart_gateway(self): self.calls.append("restart")
    def wait_api(self): self.calls.append("wait_api")
    def record_runtime(self): self.calls.append("record_runtime")
    def prepare_delivery(self): self.calls.append("delivery"); return {"unit": "fixture-cli.service"}
    def listing_reference(self): self.calls.append("listing"); return {"upstream_path": "/openany/v1/runs"}
    def https_ready(self): self.calls.append("https_ready"); return self.ready
    def configure_https(self): self.calls.append("https")
    def verify_https(self): self.calls.append("verify_https"); return {"base_url": "https://fixture.example.com", "authenticated": True}


if __name__ == "__main__":
    unittest.main()
