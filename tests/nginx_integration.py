"""Real Nginx + local HTTP fixture + temporary TLS certificate; never calls Hermes."""
import contextlib
import http.server
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import common
import hermes_api
import https_proxy
import nginx_shared

FIXTURE_KEY = "local-fixture-only-not-a-real-credential"
NGINX = os.environ.get("NGINX_BINARY")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Fixture(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []
    release_sse = threading.Event()
    active_key = FIXTURE_KEY
    enabled = True

    def log_message(self, *args):
        pass

    def reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def record(self, body=b""):
        self.seen.append({"task_id": self.headers.get("X-OpenAny-Task-Id"), "deadline": self.headers.get("X-OpenAny-Deadline"), "delivery": self.headers.get("X-OpenAny-Artifact-Delivery"), "path": self.path, "method": self.command, "auth": self.headers.get("Authorization"), "idempotency": self.headers.get("Idempotency-Key"), "session": self.headers.get("X-Hermes-Session-Key"), "content_type": self.headers.get("Content-Type"), "body": body})

    def do_GET(self):
        self.record()
        if not self.enabled:
            return self.reply(503, {"error": "fixture API disabled"})
        if self.headers.get("Authorization") != "Bearer " + self.active_key:
            return self.reply(401, {"error": "unauthorized"})
        if self.path == "/v1/capabilities":
            return self.reply(200, {"object": "hermes.api_server.capabilities", "features": {"run_submission": True, "run_status": True}})
        if self.path == "/v1/skills":
            return self.reply(200, {"object": "list", "data": [{"name": "local-fixture-skill"}]})
        if self.path.endswith("/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"event: run.started\ndata: {}\n\n")
            self.wfile.flush()
            self.release_sse.wait(5)
            self.wfile.write(b"event: run.completed\ndata: {}\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        return self.reply(200, {"status": "completed", "run_id": "run_localfixture"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.record(body)
        if self.headers.get("Authorization") != "Bearer " + self.active_key:
            return self.reply(401, {"error": "unauthorized"})
        return self.reply(202, {"run_id": "run_localfixture", "status": "started"})


@unittest.skipUnless(NGINX, "Set NGINX_BINARY to a local Nginx executable to run this integration suite")
class NginxIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="openany-nginx-integration-")
        cls.root = Path(cls.temp.name)
        cls.backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        cls.thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.thread.start()
        cls.http_port, cls.tls_port = free_port(), free_port()
        cls.base = "https://127.0.0.1:" + str(cls.tls_port)
        cert, key = cls.root / "cert.pem", cls.root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], check=True, capture_output=True)
        cls.trust = patch.dict(os.environ, {"SSL_CERT_FILE": str(cert)})
        cls.trust.start()
        cls.acme = cls.root / "acme/.well-known/acme-challenge"
        cls.acme.mkdir(parents=True)
        (cls.acme / "test-token").write_text("local-acme-fixture")
        body = https_proxy.render_site("hermes.example.test", cls.backend.server_port, True, False)
        # Match new-site setup: create TLS, then add the same managed CLI
        # namespace used by shared-site setup.
        selected = nginx_shared.select_site({str(cls.root / 'site.conf'): body}, 'hermes.example.test')
        body = nginx_shared.patch_site(body, selected, cls.backend.server_port)
        body = body.replace("listen 80;", f"listen 127.0.0.1:{cls.http_port};").replace("listen 443 ssl;", f"listen 127.0.0.1:{cls.tls_port} ssl;")
        body = body.replace("/var/lib/openany-hermes/acme", str(cls.root / "acme"))
        body = body.replace("/etc/letsencrypt/live/openany-hermes-hermes.example.test/fullchain.pem", str(cert)).replace("/etc/letsencrypt/live/openany-hermes-hermes.example.test/privkey.pem", str(key))
        cls.site = cls.root / "site.conf"
        cls.site.write_text(body)
        cls.config = cls.root / "nginx.conf"
        cls.config.write_text(f"pid {cls.root}/nginx.pid;\nerror_log {cls.root}/error.log;\nevents {{}}\nhttp {{\nclient_body_temp_path {cls.root}/client;\nproxy_temp_path {cls.root}/proxy;\nfastcgi_temp_path {cls.root}/fastcgi;\nuwsgi_temp_path {cls.root}/uwsgi;\nscgi_temp_path {cls.root}/scgi;\ninclude {cls.site};\n}}\n")
        result = subprocess.run([NGINX, "-p", str(cls.root), "-c", str(cls.config), "-t"], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.process = subprocess.Popen([NGINX, "-p", str(cls.root), "-c", str(cls.config), "-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", cls.tls_port), timeout=0.1):
                    return
            except OSError:
                if cls.process.poll() is not None:
                    raise AssertionError(cls.process.stderr.read().decode())
                time.sleep(0.02)
        raise AssertionError("Nginx did not start")

    @classmethod
    def tearDownClass(cls):
        Fixture.release_sse.set()
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.process.stderr.close()
        cls.backend.shutdown()
        cls.backend.server_close()
        cls.thread.join(timeout=5)
        cls.trust.stop()
        cls.temp.cleanup()

    def opener(self):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), common.NoRedirect())

    def test_authenticated_discovery_and_auth_failure(self):
        result = common.verify_api(self.base, FIXTURE_KEY)
        self.assertEqual(result["skill_count"], 1)
        self.assertEqual(result["model_tasks_started"], 0)
        with self.assertRaises(common.Failure):
            common.verify_api(self.base, "wrong-placeholder")

    def test_new_site_exposes_the_same_prefixed_authenticated_discovery(self):
        result = common.verify_api(self.base, FIXTURE_KEY, lambda base, path, key=None: common.request_json(base, '/openany' + path, key))
        self.assertTrue(result['authenticated'])
        self.assertEqual(result['skill_count'], 1)

    def test_new_site_prefixed_runs_and_status_keep_the_native_path(self):
        headers = {'Authorization': 'Bearer ' + FIXTURE_KEY, 'Content-Type': 'application/json', 'Idempotency-Key': 'new-site-fixture'}
        body = b'{"input":"local route fixture"}'
        request = urllib.request.Request(self.base + '/openany/v1/runs', data=body, headers=headers)
        with self.opener().open(request, timeout=3) as response:
            self.assertEqual(response.status, 202)
        self.assertEqual(Fixture.seen[-1]['path'], '/v1/runs')
        self.assertEqual(Fixture.seen[-1]['body'], body)
        request = urllib.request.Request(self.base + '/openany/v1/runs/run_localfixture', headers=headers)
        with self.opener().open(request, timeout=3) as response:
            self.assertEqual(json.load(response)['status'], 'completed')
        self.assertEqual(Fixture.seen[-1]['path'], '/v1/runs/run_localfixture')

    def test_dashboard_saved_configuration_key_file_and_https_handoff(self):
        # A source-shaped Dashboard fixture, not an actual Hermes process.
        with tempfile.TemporaryDirectory(prefix="openany-dashboard-handoff-") as tmp:
            home = Path(tmp)
            saved_key = "dashboard-saved-private-fixture-key-12345"
            (home / ".env").write_text('API_SERVER_KEY="' + saved_key + '"\nAPI_SERVER_MODEL_NAME=keep-alias\n')
            (home / "config.yaml").write_text("platforms:\n  api_server:\n    enabled: false\n")
            try:
                Fixture.enabled = False
                with self.assertRaises(common.Failure):
                    common.verify_api(self.base, saved_key)
                configured = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/openany_hermes.py"), "api-config", "--home", str(home), "--port", str(self.backend.server_port), "--require-existing-key", "--apply"], capture_output=True, text=True)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assertEqual(json.loads(configured.stdout)["key_action"], "reuse")
                env = dict(line.split("=", 1) for line in (home / ".env").read_text().splitlines())
                self.assertEqual(env["API_SERVER_KEY"], saved_key)
                self.assertEqual(env["API_SERVER_MODEL_NAME"], "keep-alias")
                self.assertIn("    enabled: true\n", (home / "config.yaml").read_text())
                # Model a restart that loads this saved state into the local API fixture.
                Fixture.active_key, Fixture.enabled = env["API_SERVER_KEY"], True
                key = common.read_key(home / "openany-api.key")
                common.verify_api(f"http://127.0.0.1:{self.backend.server_port}", key)
                self.assertTrue(common.verify_api(self.base, key)["authenticated"])
                # A later Dashboard save must be synchronized before the key file works again.
                changed_key = "dashboard-updated-private-fixture-key-67890"
                (home / ".env").write_text((home / ".env").read_text().replace(saved_key, changed_key))
                Fixture.active_key = changed_key
                with self.assertRaises(common.Failure):
                    common.verify_api(self.base, common.read_key(home / "openany-api.key"))
                hermes_api.configure_api(home, self.backend.server_port, True, require_existing=True)
                self.assertTrue(common.verify_api(self.base, common.read_key(home / "openany-api.key"))["authenticated"])
            finally:
                Fixture.active_key, Fixture.enabled = FIXTURE_KEY, True

    def test_run_body_path_auth_and_idempotency_are_preserved(self):
        payload = b'{"input":"local fixture only","instructions":"Use local-fixture-skill"}'
        request = urllib.request.Request(self.base + "/v1/runs", data=payload, headers={"Authorization": "Bearer " + FIXTURE_KEY, "Content-Type": "application/json", "Idempotency-Key": "fixture-idempotency-1", "X-Hermes-Session-Key": "fixture-session"})
        with self.opener().open(request, timeout=2) as response:
            self.assertEqual(response.status, 202)
        received = Fixture.seen[-1]
        self.assertEqual(received["path"], "/v1/runs")
        self.assertEqual(received["body"], payload)
        self.assertEqual(received["auth"], "Bearer " + FIXTURE_KEY)
        self.assertEqual(received["idempotency"], "fixture-idempotency-1")
        self.assertEqual(received["session"], "fixture-session")
        self.assertEqual(received["content_type"], "application/json")

    def test_status_query_string_is_preserved(self):
        request = urllib.request.Request(self.base + "/v1/runs/run_localfixture?view=compact", headers={"Authorization": "Bearer " + FIXTURE_KEY})
        with self.opener().open(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(Fixture.seen[-1]["path"], "/v1/runs/run_localfixture?view=compact")

    def test_sse_first_frame_arrives_before_completion(self):
        Fixture.release_sse.clear()
        request = urllib.request.Request(self.base + "/v1/runs/run_localfixture/events", headers={"Authorization": "Bearer " + FIXTURE_KEY})
        try:
            with self.opener().open(request, timeout=2) as response:
                # The fixture cannot complete until this assertion reads the first frame.
                self.assertEqual(response.readline(), b"event: run.started\n")
                self.assertEqual(response.readline(), b"data: {}\n")
                Fixture.release_sse.set()
                self.assertIn(b"event: run.completed", response.read())
        finally:
            Fixture.release_sse.set()

    def test_nonallowlisted_routes_do_not_reach_backend(self):
        for path in ("/", "/api/sessions", "/v1/chat/completions", "/v1/runs/run_localfixture/approval", "/etc/passwd"):
            with self.subTest(path=path):
                count = len(Fixture.seen)
                request = urllib.request.Request(self.base + path, headers={"Authorization": "Bearer " + FIXTURE_KEY})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.opener().open(request, timeout=2)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
                self.assertEqual(len(Fixture.seen), count)

    def test_http_acme_serves_file_and_other_routes_redirect(self):
        with self.opener().open(f"http://127.0.0.1:{self.http_port}/.well-known/acme-challenge/test-token", timeout=2) as response:
            self.assertEqual(response.read(), b"local-acme-fixture")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.opener().open(f"http://127.0.0.1:{self.http_port}/v1/skills", timeout=2)
        self.assertEqual(error.exception.code, 308)
        self.assertEqual(error.exception.headers["Location"], "https://hermes.example.test/v1/skills")
        error.exception.close()

    def test_tls_untrusted_certificate_is_rejected(self):
        with patch.dict(os.environ, {"SSL_CERT_FILE": "/nonexistent-trust-store"}):
            with self.assertRaises(common.Failure):
                common.request_json(self.base, "/v1/capabilities", FIXTURE_KEY)

    def test_bootstrap_and_ipv6_site_variants_pass_nginx_syntax(self):
        for tls, ipv6 in ((False, False), (False, True), (True, True)):
            with self.subTest(tls=tls, ipv6=ipv6):
                body = https_proxy.render_site("hermes.example.test", self.backend.server_port, tls, ipv6)
                body = body.replace("/etc/letsencrypt/live/openany-hermes-hermes.example.test/fullchain.pem", str(self.root / "cert.pem")).replace("/etc/letsencrypt/live/openany-hermes-hermes.example.test/privkey.pem", str(self.root / "key.pem"))
                variant = self.root / "variant-site.conf"
                variant.write_text(body)
                config = self.root / "variant-nginx.conf"
                config.write_text(self.config.read_text().replace(str(self.site), str(variant)))
                result = subprocess.run([NGINX, "-p", str(self.root), "-c", str(config), "-t"], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
