import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class OnboardingTests(unittest.TestCase):
    def module(self, name):
        self.assertTrue((ROOT / "scripts" / (name + ".py")).is_file(), "implementation is missing: " + name)
        return importlib.import_module(name)

    def test_cli_help_is_usable(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/openany_hermes.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("api-config", "https", "check", "verify", "restore"):
            self.assertIn(command, result.stdout)

    def test_cli_configure_and_restore_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = "MODEL=roundtrip-fixture\n"
            (home / ".env").write_text(original)
            base = [sys.executable, "-B", str(ROOT / "scripts/openany_hermes.py")]
            configured = subprocess.run(base + ["api-config", "--home", str(home), "--apply"], capture_output=True, text=True)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = json.loads(configured.stdout)
            key = (home / "openany-api.key").read_text().strip()
            restored = subprocess.run(base + ["restore", "--home", str(home), "--backup", result["backup"], "--apply"], capture_output=True, text=True)
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual((home / ".env").read_text(), original)
            self.assertFalse((home / "openany-api.key").exists())
            self.assertNotIn(key, configured.stdout + configured.stderr + restored.stdout + restored.stderr)

    def test_domain_and_port_reject_configuration_injection(self):
        c = self.module("common")
        for domain in ("localhost", "*.example.com", "a.example.com;", "a.example.com\n}", "https://a.example.com", "-a.example.com", "a..com", "127.0.0.1"):
            with self.subTest(domain=domain), self.assertRaises(c.Failure):
                c.domain_name(domain)
        self.assertEqual(c.domain_name("API.Example.COM"), "api.example.com")
        for port in (0, 65536, -1, "8642;whoami"):
            with self.assertRaises(c.Failure):
                c.port_number(port)

    def test_api_preview_creates_nothing(self):
        api = self.module("hermes_api")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text("OPENAI_API_KEY=provider-placeholder\n")
            before = sorted(p.name for p in home.iterdir())
            result = api.configure_api(home, 8642, False)
            self.assertEqual(result["mode"], "preview")
            self.assertEqual(before, sorted(p.name for p in home.iterdir()))
            self.assertEqual((home / ".env").read_text(), "OPENAI_API_KEY=provider-placeholder\n")

    def test_dashboard_disabled_flag_is_enabled_and_backed_up_without_other_yaml_changes(self):
        api = self.module("hermes_api")
        for prefix in ("platforms:\n  api_server:\n", "gateway:\n  platforms:\n    api_server:\n", "gateway:\n  api_server:\n"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                indent = " " * (len(prefix.splitlines()[-1]) - len(prefix.splitlines()[-1].lstrip()) + 2)
                original_yaml = prefix + indent + "enabled: false # disabled in Dashboard\n" + indent + "extra:\n" + indent + "  model_name: keep-this-alias\nother:\n  enabled: false\n"
                (home / "config.yaml").write_text(original_yaml)
                key = "existing-dashboard-key-keep-unchanged-123"
                (home / ".env").write_text("API_SERVER_KEY=" + key + "\nPROVIDER_SETTING=keep\n")
                preview = api.configure_api(home, 8642, False)
                self.assertEqual((home / "config.yaml").read_text(), original_yaml)
                self.assertTrue(preview["yaml_enable_changes"])
                result = api.configure_api(home, 8642, True)
                self.assertEqual((home / "config.yaml").read_text(), original_yaml.replace("enabled: false #", "enabled: true #", 1))
                self.assertEqual((home / "openany-api.key").read_text().strip(), key)
                self.assertIn("PROVIDER_SETTING=keep", (home / ".env").read_text())
                manifest = json.loads((Path(result["backup"]) / "manifest.json").read_text())
                self.assertIn(str(home / "config.yaml"), [entry["path"] for entry in manifest["files"]])

    def test_short_or_placeholder_dashboard_key_fails_without_changing_configuration(self):
        api, c = self.module("hermes_api"), self.module("common")
        for key in ("short-key", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "sk-xxxxxxxxxxxxxxxxxxxx", "your_access_key_here"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                original = "API_SERVER_KEY=" + key + "\nMODEL=keep\n"
                (home / ".env").write_text(original)
                with self.assertRaises(c.Failure):
                    api.configure_api(home, 8642, True)
                self.assertEqual((home / ".env").read_text(), original)
                self.assertFalse((home / "openany-api.key").exists())

    def test_dashboard_rotation_syncs_saved_key_and_keeps_model_alias(self):
        api = self.module("hermes_api")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text("API_SERVER_KEY=previous-dashboard-key-1234567890\nAPI_SERVER_MODEL_NAME=custom-alias\n")
            api.configure_api(home, 8642, True)
            updated = (home / ".env").read_text().replace("previous-dashboard-key-1234567890", "new-dashboard-key-123456789012345")
            (home / ".env").write_text(updated)
            api.configure_api(home, 8642, True)
            self.assertEqual((home / "openany-api.key").read_text().strip(), "new-dashboard-key-123456789012345")
            self.assertIn("API_SERVER_MODEL_NAME=custom-alias", (home / ".env").read_text())

    def test_dashboard_sync_requires_saved_key_and_does_not_generate_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = "platforms:\n  api_server:\n    enabled: false\n"
            (home / "config.yaml").write_text(original)
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/openany_hermes.py"), "api-config", "--home", str(home), "--require-existing-key", "--apply"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Save", result.stderr)
            self.assertFalse((home / ".env").exists())
            self.assertFalse((home / "openany-api.key").exists())
            self.assertEqual((home / "config.yaml").read_text(), original)

    def test_yaml_change_rolls_back_when_key_file_write_fails(self):
        api, c = self.module("hermes_api"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = "platforms:\n  api_server:\n    enabled: false\n"
            (home / "config.yaml").write_text(original)
            real_write = c.atomic_write
            failed = False
            def fail_once(path, *args, **kwargs):
                nonlocal failed
                if Path(path).name == "openany-api.key" and not failed:
                    failed = True
                    raise OSError("fixture storage error")
                return real_write(path, *args, **kwargs)
            with patch.object(c, "atomic_write", side_effect=fail_once), self.assertRaises(OSError):
                api.configure_api(home, 8642, True)
            self.assertEqual((home / "config.yaml").read_text(), original)
            self.assertFalse((home / ".env").exists())

    def test_complex_yaml_api_mapping_is_rejected_before_writes(self):
        api, c = self.module("hermes_api"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "config.yaml").write_text("platforms: {api_server: {enabled: false}}\n")
            (home / ".env").write_text("API_SERVER_KEY=existing-literal-key-1234567890\n")
            with self.assertRaises(c.Failure):
                api.configure_api(home, 8642, True)
            self.assertFalse((home / "openany-api.key").exists())

    def test_yaml_parent_anchor_cannot_hide_explicit_api_disable(self):
        api, c = self.module("hermes_api"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = "platforms: &existing\n  api_server:\n    enabled: false\n"
            (home / "config.yaml").write_text(original)
            with self.assertRaises(c.Failure):
                api.configure_api(home, 8642, True)
            self.assertEqual((home / "config.yaml").read_text(), original)
            self.assertFalse((home / ".env").exists())

    def test_multiline_provider_value_is_preserved_and_not_treated_as_api_settings(self):
        api = self.module("hermes_api")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            prompt = 'PROMPT="first line\nAPI_SERVER_HOST=sample-in-prompt\nlast line"\n'
            (home / ".env").write_text(prompt + "API_SERVER_KEY=existing-dashboard-key-123456789\n")
            api.configure_api(home, 8642, True)
            self.assertTrue((home / ".env").read_text().startswith(prompt))

    def test_cli_restore_recovers_dashboard_yaml_and_key_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original_yaml = "platforms:\n  api_server:\n    enabled: false\n"
            original_env = "API_SERVER_KEY=existing-dashboard-key-123456789\n"
            (home / "config.yaml").write_text(original_yaml)
            (home / ".env").write_text(original_env)
            base = [sys.executable, "-B", str(ROOT / "scripts/openany_hermes.py")]
            configured = subprocess.run(base + ["api-config", "--home", str(home), "--apply"], capture_output=True, text=True)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = json.loads(configured.stdout)
            restored = subprocess.run(base + ["restore", "--home", str(home), "--backup", result["backup"], "--apply"], capture_output=True, text=True)
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual((home / "config.yaml").read_text(), original_yaml)
            self.assertEqual((home / ".env").read_text(), original_env)
            self.assertFalse((home / "openany-api.key").exists())

    def test_yaml_literal_documentation_text_is_not_modified(self):
        api = self.module("hermes_api")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = "notes: |\n  platforms:\n    api_server:\n      enabled: false\nplatforms:\n  api_server:\n    enabled: true\n"
            (home / "config.yaml").write_text(original)
            api.configure_api(home, 8642, True)
            self.assertEqual((home / "config.yaml").read_text(), original)

    def test_public_acme_directories_are_readable_with_restrictive_umask(self):
        p = self.module("https_proxy")
        with tempfile.TemporaryDirectory() as tmp:
            paths = p.Paths(Path(tmp))
            paths.site("api.example.com").parent.mkdir(parents=True)
            old_mask = os.umask(0o077)
            try:
                p.deploy_site("api.example.com", 8642, "ops@example.com", False, "placeholder", paths, lambda args: "", lambda *args: {})
            finally:
                os.umask(old_mask)
            self.assertEqual(stat.S_IMODE(paths.state.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(paths.webroot.stat().st_mode), 0o755)

    def test_api_configuration_preserves_provider_and_reuses_key(self):
        api = self.module("hermes_api")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text("# keep this\nOPENAI_API_KEY=provider-placeholder\nAPI_SERVER_HOST=0.0.0.0\nAPI_SERVER_ENABLED=false\n")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                first = api.configure_api(home, 8642, True)
                key = (home / "openany-api.key").read_text().strip()
                second = api.configure_api(home, 8642, True)
            env = (home / ".env").read_text()
            self.assertIn("OPENAI_API_KEY=provider-placeholder", env)
            self.assertIn("API_SERVER_HOST=127.0.0.1", env)
            self.assertIn("API_SERVER_ENABLED=true", env)
            self.assertGreaterEqual(len(key), 40)
            self.assertIn("API_SERVER_KEY=" + key, env)
            self.assertEqual((home / "openany-api.key").read_text().strip(), key)
            self.assertFalse(second["changed"])
            self.assertNotIn(key, stream.getvalue() + json.dumps(first) + json.dumps(second))
            for path in (home / ".env", home / "openany-api.key"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(Path(first["backup"]).stat().st_mode), 0o700)

    def test_existing_key_and_duplicate_assignments(self):
        api = self.module("hermes_api")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            key = "keep-existing-credential-1234567890"
            (home / ".env").write_text('API_SERVER_ENABLED=false\nexport API_SERVER_KEY="' + key + '" # retained\nAPI_SERVER_ENABLED=true\nMODEL=keep-me\n')
            api.configure_api(home, 9000, True)
            env = (home / ".env").read_text()
            self.assertEqual(env.count("API_SERVER_ENABLED="), 1)
            self.assertIn("MODEL=keep-me", env)
            self.assertEqual((home / "openany-api.key").read_text().strip(), key)

    def test_api_does_not_replace_existing_yaml_key_with_generated_one(self):
        api, c = self.module("hermes_api"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "config.yaml").write_text("gateway:\n  api_server:\n    key: existing-yaml-credential\n")
            with self.assertRaises(c.Failure):
                api.configure_api(home, 8642, True)
            self.assertFalse((home / ".env").exists())

    def test_skills_accepts_official_list_envelope_and_legacy_array(self):
        c = self.module("common")
        def fetch(base, path, key=None):
            if key is None:
                return 401, {}
            if path == "/v1/skills":
                return 200, {"object": "list", "data": [{"name": "fixture-skill"}]}
            return 200, {"object": "hermes.api_server.capabilities", "features": {"run_submission": True, "run_status": True}}
        result = c.verify_api("https://api.example.com", "placeholder", fetch=fetch)
        self.assertEqual(result["skill_count"], 1)

    def test_api_rejects_symlink_and_interpolated_key(self):
        api, c = self.module("hermes_api"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = home / "real-env"
            target.write_text("MODEL=unchanged\n")
            (home / ".env").symlink_to(target)
            with self.assertRaises(c.Failure):
                api.configure_api(home, 8642, True)
            self.assertEqual(target.read_text(), "MODEL=unchanged\n")
            (home / ".env").unlink()
            (home / ".env").write_text("API_SERVER_KEY=${OTHER_SECRET}\n")
            with self.assertRaises(c.Failure):
                api.configure_api(home, 8642, True)

    def test_partial_file_failure_restores_both_api_files(self):
        api, c = self.module("hermes_api"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = "MODEL=original\n"
            (home / ".env").write_text(original)
            real_write = c.atomic_write
            failed = False
            def fail_once(path, *args, **kwargs):
                nonlocal failed
                if Path(path).name == "openany-api.key" and not failed:
                    failed = True
                    raise OSError("simulated storage error")
                return real_write(path, *args, **kwargs)
            with patch.object(c, "atomic_write", side_effect=fail_once), self.assertRaises(OSError):
                api.configure_api(home, 8642, True)
            self.assertEqual((home / ".env").read_text(), original)
            self.assertFalse((home / "openany-api.key").exists())

    def test_transaction_rolls_back_existing_and_new_files(self):
        c = self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "old"
            original.write_bytes(b"old-content")
            original.chmod(0o640)
            with self.assertRaises(RuntimeError):
                with c.Transaction(root / "backups") as tx:
                    tx.write(original, b"new-content", 0o600)
                    tx.write(root / "new", b"new-file", 0o600)
                    raise RuntimeError("simulated failure")
            self.assertEqual(original.read_bytes(), b"old-content")
            self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o640)
            self.assertFalse((root / "new").exists())

    def test_restore_validates_targets_and_recovers_original(self):
        c = self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".env"
            target.write_bytes(b"MODEL=before\n")
            with c.Transaction(root / "backups") as tx:
                tx.write(target, b"MODEL=after\n", 0o600)
            with self.assertRaises(c.Failure):
                c.restore_snapshot(tx.directory, {root / "other"})
            self.assertEqual(target.read_bytes(), b"MODEL=after\n")
            c.restore_snapshot(tx.directory, {target})
            self.assertEqual(target.read_bytes(), b"MODEL=before\n")

    def test_dns_requires_matching_ipv4_and_ipv6(self):
        p, c = self.module("https_proxy"), self.module("common")
        self.assertEqual(p.check_dns("api.example.com", ["192.0.2.1"], resolver=lambda _: {"192.0.2.1"}), {"192.0.2.1"})
        with self.assertRaises(c.Failure):
            p.check_dns("api.example.com", ["192.0.2.1"], resolver=lambda _: {"192.0.2.1", "2001:db8::2"})

    def test_site_conflicts_are_not_overwritten(self):
        p, c = self.module("https_proxy"), self.module("common")
        output = "# configuration file /etc/nginx/conf.d/other.conf:\nserver { server_name api.example.com; }\n"
        with self.assertRaises(c.Failure):
            p.check_conflicts(output, "api.example.com", Path("/etc/nginx/conf.d/openany-hermes-api.example.com.conf"))
        p.check_conflicts(output, "other.example.com", Path("/etc/nginx/conf.d/openany-hermes-other.example.com.conf"))

    def test_verify_requires_auth_and_capabilities_not_only_http_200(self):
        c = self.module("common")
        def fetch(base, path, key=None):
            if key is None:
                return 401, {}
            return (200, []) if path == "/v1/skills" else (200, {"object": "hermes.api_server.capabilities", "features": {"run_submission": True, "run_status": True}})
        result = c.verify_api("https://api.example.com", "test-placeholder", fetch=fetch)
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["skill_count"], 0)
        with self.assertRaises(c.Failure):
            c.verify_api("https://api.example.com", "test-placeholder", fetch=lambda *args: (200, {}))
        def broken(base, path, key=None):
            return (401, {}) if key is None else (200, {})
        with self.assertRaises(c.Failure):
            c.verify_api("https://api.example.com", "test-placeholder", fetch=broken)

    def test_verification_rejects_plaintext_remote_url(self):
        c = self.module("common")
        for url in ("http://api.example.com", "https://user:password@api.example.com", "https://api.example.com/?key=secret", "https://api.example.com/v1"):
            with self.assertRaises(c.Failure):
                c.validate_base_url(url)

    def test_proxy_preview_never_runs_system_commands(self):
        p = self.module("https_proxy")
        with patch.object(p, "run_command", side_effect=AssertionError("must not execute in preview")):
            result = p.configure_proxy("api.example.com", 8642, "ops@example.com", ["192.0.2.1"], None, False, False)
        self.assertEqual(result["mode"], "preview")

    def test_certificate_failure_restores_existing_site(self):
        p, c = self.module("https_proxy"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            paths = p.Paths(Path(tmp))
            site = paths.site("api.example.com")
            site.parent.mkdir(parents=True)
            original = p.render_site("api.example.com", 8642, False, False)
            site.write_text(original)
            def runner(args):
                if args[0] == "certbot":
                    raise c.Failure("simulated ACME rejection")
                return ""
            with self.assertRaises(c.Failure):
                p.deploy_site("api.example.com", 8642, "ops@example.com", False, "placeholder", paths, runner, lambda *args: {})
            self.assertEqual(site.read_text(), original)
            self.assertFalse(paths.hook("api.example.com").exists())

    def test_https_validation_failure_restores_new_site_and_hook(self):
        p, c = self.module("https_proxy"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            paths = p.Paths(Path(tmp))
            paths.site("api.example.com").parent.mkdir(parents=True)
            def bad_verify(*args):
                raise c.Failure("simulated TLS verification failure")
            with self.assertRaises(c.Failure):
                p.deploy_site("api.example.com", 8642, "ops@example.com", False, "placeholder", paths, lambda args: "", bad_verify)
            self.assertFalse(paths.site("api.example.com").exists())
            self.assertFalse(paths.hook("api.example.com").exists())

    def test_listener_check_rejects_public_api_binding(self):
        p, c = self.module("https_proxy"), self.module("common")
        good = 'LISTEN 0 128 127.0.0.1:8642 0.0.0.0:* users:(("python",pid=1,fd=2))\n'
        p.check_listeners(good, 8642)
        with self.assertRaises(c.Failure):
            p.check_listeners(good + 'LISTEN 0 128 0.0.0.0:8642 0.0.0.0:*\n', 8642)

    def test_domain_operations_share_an_exclusive_lock(self):
        p, c = self.module("https_proxy"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            paths = p.Paths(Path(tmp))
            with p.operation_lock(paths):
                with self.assertRaises(c.Failure):
                    with p.operation_lock(paths):
                        self.fail("concurrent operation must not enter")

    def test_reloading_invalid_new_config_restores_original_file(self):
        p, c = self.module("https_proxy"), self.module("common")
        with tempfile.TemporaryDirectory() as tmp:
            paths = p.Paths(Path(tmp))
            site = paths.site("api.example.com")
            site.parent.mkdir(parents=True)
            original = p.render_site("api.example.com", 8642, False, False)
            site.write_text(original)
            def runner(args):
                if args == ["nginx", "-t"] and "ssl_certificate " in site.read_text():
                    raise c.Failure("simulated invalid TLS config")
                return ""
            with self.assertRaises(c.Failure):
                p.deploy_site("api.example.com", 8642, "ops@example.com", False, "placeholder", paths, runner, lambda *args: {})
            self.assertEqual(site.read_text(), original)
            self.assertFalse(paths.hook("api.example.com").exists())

    def test_render_site_preserves_upstream_uri_and_streaming(self):
        p = self.module("https_proxy")
        site = p.render_site("api.example.com", 8642, True, True)
        self.assertIn("proxy_pass http://127.0.0.1:8642;", site)
        self.assertIn("proxy_buffering off;", site)
        self.assertIn("proxy_next_upstream off;", site)
        self.assertIn("Authorization $http_authorization", site)
        self.assertIn("Idempotency-Key $http_idempotency_key", site)
        self.assertNotIn("location /api/", site)
        self.assertIn("listen [::]:443 ssl;", site)


if __name__ == "__main__":
    unittest.main()
