"""Ubuntu/Debian Nginx + Certbot orchestration. No SSH or firewall mutation."""
import fnmatch
import ipaddress
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

import common
from common import Failure

MARKER = "# Managed by OpenAny Hermes onboarding v1"


class Paths:
    def __init__(self, root=Path("/")):
        self.root = Path(root)
        self.state = self.root / "var/lib/openany-hermes"
        self.webroot = self.state / "acme"
        self.backups = self.state / "backups"

    def site(self, domain):
        return self.root / "etc/nginx/conf.d" / ("openany-hermes-" + domain + ".conf")

    def hook(self, domain):
        return self.root / "etc/letsencrypt/renewal-hooks/deploy" / ("openany-hermes-" + domain)


def render_site(domain, port, tls, ipv6):
    domain, port = common.domain_name(domain), common.port_number(port)
    v6_http = "    listen [::]:80;\n" if ipv6 else ""
    http_action = f"return 308 https://{domain}$request_uri;" if tls else "return 503;"
    http = f"""{MARKER}; domain={domain}
server {{
    listen 80;
{v6_http}    server_name {domain};
    server_tokens off;
    access_log off;
    location ^~ /.well-known/acme-challenge/ {{
        root /var/lib/openany-hermes/acme;
        default_type text/plain;
        try_files $uri =404;
    }}
    location / {{ {http_action} }}
}}
"""
    if not tls:
        return http
    v6_tls = "    listen [::]:443 ssl;\n" if ipv6 else ""
    proxy = f"""        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Connection "";
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Idempotency-Key $http_idempotency_key;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_cache off;
        proxy_next_upstream off;
        proxy_read_timeout 3600s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 120s;
"""
    locations = ""
    for path, method in (("= /v1/runs", "POST"), ("= /v1/skills", "GET"), ("= /v1/capabilities", "GET"), ("= /v1/health", "GET"), ("~ ^/v1/runs/[A-Za-z0-9_-]+(?:/events)?$", "GET")):
        locations += f"    location {path} {{\n        limit_except {method} {{ deny all; }}\n{proxy}    }}\n"
    return http + f"""server {{
    listen 443 ssl;
{v6_tls}    server_name {domain};
    server_tokens off;
    access_log off;
    ssl_certificate /etc/letsencrypt/live/openany-hermes-{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/openany-hermes-{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 10m;
    gzip off;
{locations}    location / {{ return 404; }}
}}
"""


def run_command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError:
        raise Failure("Cannot execute required program: " + args[0]) from None
    if result.returncode:
        # Do not echo tool output: nginx -T or third-party errors may contain secrets.
        raise Failure("Command failed: " + args[0] + " " + (args[1] if len(args) > 1 else "") + ". Consult the relevant system journal or Certbot log on the VPS.")
    return result.stdout + result.stderr


def resolve_domain(domain):
    try:
        return {item[4][0] for item in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        raise Failure("DNS lookup failed. Configure A/AAAA records and wait for propagation.") from None


def check_dns(domain, expected, resolver=resolve_domain):
    try:
        expected = {str(ipaddress.ip_address(ip)) for ip in expected}
        actual = {str(ipaddress.ip_address(ip)) for ip in resolver(domain)}
    except ValueError:
        raise Failure("Every public IP must be a valid IPv4 or IPv6 address.") from None
    if not expected or actual != expected:
        raise Failure("DNS A/AAAA records must exactly match --public-ip values. Check stale AAAA records, multiple A records and CDN proxy mode.")
    return actual


def check_listeners(output, api_port):
    found = False
    for line in output.splitlines():
        columns = line.split()
        if len(columns) < 5:
            continue
        address = columns[3]
        if ":" not in address:
            continue
        host, port = address.rsplit(":", 1)
        if port == str(api_port):
            if host.strip("[]") not in ("127.0.0.1", "::1"):
                raise Failure("Hermes API is listening beyond loopback. Set API_SERVER_HOST=127.0.0.1 and restart the correct Gateway.")
            found = True
        if port in ("80", "443") and '"nginx"' not in line:
            raise Failure("Port 80/443 is held by another or unidentified process. Run check with sudo and resolve the conflict explicitly.")
    if not found:
        raise Failure("No loopback API listener found on the selected port. Start or restart Hermes Gateway first.")


def check_conflicts(output, domain, managed_site):
    current = None
    for line in output.splitlines():
        match = re.match(r"# configuration file (.+):$", line)
        if match:
            current = match.group(1)
        if current == str(managed_site):
            continue
        for names in re.findall(r"\bserver_name\s+([^;]+);", line.split("#", 1)[0]):
            for name in names.split():
                if name == domain or fnmatch.fnmatchcase(domain, name) or (name.startswith(".") and (domain == name[1:] or domain.endswith(name))):
                    raise Failure("An existing Nginx server_name matches this domain. Review that site before onboarding.")
                if name.startswith("~"):
                    raise Failure("Nginx contains a regex server_name; its domain overlap requires manual review.")


def require_managed(path):
    path = common.regular_file(path)
    if path.exists() and MARKER not in "\n".join(path.read_text().splitlines()[:3]):
        raise Failure("Refusing to overwrite a file not managed by this tool: " + str(path))


def check_os():
    if sys.platform != "linux":
        raise Failure("System operations require an Ubuntu/Debian VPS. Preview and verification can also run on macOS.")
    release = Path("/etc/os-release").read_text()
    if not re.search(r'^ID=["\']?(?:ubuntu|debian)["\']?$', release, re.M):
        raise Failure("This release supports Ubuntu and Debian only.")
    if not Path("/run/systemd/system").is_dir():
        raise Failure("A running systemd host is required. Containers are outside this release.")


def check_environment(domain, port, public_ips, key_file):
    check_os()
    actual = check_dns(domain, public_ips)
    if not shutil.which("ss"):
        raise Failure("Install iproute2 first: sudo apt-get install iproute2.")
    check_listeners(run_command(["ss", "-H", "-ltnp"]), port)
    if key_file is None:
        raise Failure("Provide --key-file pointing to the generated openany-api.key file.")
    key = common.read_key(key_file)
    verified = common.verify_api(f"http://127.0.0.1:{port}", key)
    return actual, key, verified


def check_nginx(domain, paths):
    require_managed(paths.site(domain))
    require_managed(paths.hook(domain))
    if not paths.site(domain).parent.is_dir():
        raise Failure("Expected /etc/nginx/conf.d is missing; custom Nginx layouts require manual integration.")
    dump = run_command(["nginx", "-T"])
    # A .conf in conf.d is ineffective unless that directory is included by http{}.
    if not re.search(r"\binclude\s+/etc/nginx/conf\.d/\*\.conf\s*;", dump):
        raise Failure("Nginx must include /etc/nginx/conf.d/*.conf inside http{}. Review the custom layout manually.")
    check_conflicts(dump, domain, paths.site(domain))


def reload_nginx(runner):
    runner(["nginx", "-t"])
    runner(["systemctl", "reload", "nginx"])


def operation_lock(paths):
    if paths.state.is_symlink():
        raise Failure("Refusing a symbolic link in managed state.")
    paths.state.mkdir(parents=True, exist_ok=True, mode=0o755)
    info = paths.state.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise Failure("Managed state must belong to this account and not be group/world writable.")
    return common.exclusive_lock(paths.state / "operation.lock")


def deploy_site(domain, port, email, ipv6, key, paths, runner=run_command, verifier=common.verify_api):
    if paths.state.is_symlink() or paths.webroot.is_symlink():
        raise Failure("Refusing a symbolic link in managed state or ACME directory.")
    paths.state.mkdir(parents=True, exist_ok=True, mode=0o755)
    paths.webroot.mkdir(exist_ok=True, mode=0o755)
    paths.hook(domain).parent.mkdir(parents=True, exist_ok=True)
    with operation_lock(paths):
        # A restrictive SSH umask must not make HTTP-01 files unreachable to nginx workers.
        paths.state.chmod(0o755)
        if paths.webroot.stat().st_uid != os.geteuid() or paths.webroot.stat().st_mode & 0o022:
            raise Failure("ACME webroot must belong to this account and not be group/world writable.")
        paths.webroot.chmod(0o755)
        require_managed(paths.site(domain))
        require_managed(paths.hook(domain))
        try:
            with common.Transaction(paths.backups) as tx:
                site = paths.site(domain)
                # Reuse an existing TLS site during renewal; do not temporarily remove HTTPS.
                if not site.exists() or "ssl_certificate " not in site.read_text():
                    tx.write(site, render_site(domain, port, False, ipv6).encode(), 0o644)
                    reload_nginx(runner)
                runner(["certbot", "certonly", "--webroot", "-w", str(paths.webroot), "--cert-name", "openany-hermes-" + domain, "-d", domain, "--email", email, "--agree-tos", "--non-interactive", "--keep-until-expiring"])
                tx.write(site, render_site(domain, port, True, ipv6).encode(), 0o644)
                hook = f'#!/bin/sh\n{MARKER}; domain={domain}\nset -eu\n[ "${{RENEWED_LINEAGE:-}}" = "/etc/letsencrypt/live/openany-hermes-{domain}" ] || exit 0\n/usr/sbin/nginx -t\n/usr/bin/systemctl reload nginx\n'
                tx.write(paths.hook(domain), hook.encode(), 0o755)
                reload_nginx(runner)
                runner(["systemctl", "enable", "--now", "certbot.timer"])
                runner(["systemctl", "is-active", "--quiet", "certbot.timer"])
                runner(["systemctl", "is-enabled", "--quiet", "certbot.timer"])
                verified = verifier("https://" + domain, key)
            return {**verified, "mode": "apply", "backup": str(tx.directory), "renewal_timer": "active_and_enabled", "renewal_dry_run": "not_run", "external_verification": "required_from_a_second_host"}
        except BaseException:
            # Transaction restored file contents; load the restored config if possible.
            try:
                reload_nginx(runner)
            except Failure:
                raise Failure("Setup failed and restored files could not be reloaded. Keep SSH open; run sudo nginx -t and review the private backup directory.") from None
            raise


def configure_proxy(domain, port, email, public_ips, key_file, apply=False, install_deps=False):
    domain, port = common.domain_name(domain), common.port_number(port)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or email.startswith("-"):
        raise Failure("Provide a valid certificate contact email.")
    try:
        expected = {str(ipaddress.ip_address(ip)) for ip in public_ips}
    except ValueError:
        raise Failure("Invalid --public-ip address.") from None
    if not expected:
        raise Failure("Provide at least one --public-ip.")
    if not apply:
        return {"mode": "preview", "base_url": "https://" + domain, "upstream": f"127.0.0.1:{port}", "expected_dns": sorted(expected), "install_missing_packages": install_deps, "steps": ["check DNS, loopback listener and authenticated API", "check existing Nginx domains", "configure HTTP-01 validation", "obtain certificate", "configure HTTPS and renewal", "verify authenticated API"], "system_changes": False}
    if os.geteuid() != 0:
        raise Failure("Run HTTPS setup with sudo. Run api-config separately as the Hermes account.")
    actual, key, _ = check_environment(domain, port, expected, key_file)
    missing = [tool for tool in ("nginx", "certbot") if not shutil.which(tool)]
    if missing:
        if not install_deps:
            raise Failure("Missing dependencies: " + ", ".join(missing) + ". Review --install-deps or install them first.")
        run_command(["apt-get", "update"])
        run_command(["apt-get", "install", "-y", *missing])
    paths = Paths()
    # Reject unsupported Snap/custom renewal layouts before requesting any certificate.
    run_command(["systemctl", "cat", "certbot.timer"])
    check_nginx(domain, paths)
    run_command(["systemctl", "enable", "--now", "nginx"])
    return deploy_site(domain, port, email, any(ipaddress.ip_address(ip).version == 6 for ip in actual), key, paths)
