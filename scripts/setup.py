#!/usr/bin/env python3
"""One-command setup for an existing Hermes, with an independent systemd worker."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import uuid

sys.dont_write_bytecode = True
import common
import hermes_api
import https_proxy
import setup_runtime
import setup_user
import cli_delivery
import prerequisites
from release import VERSION

STATE = Path("/var/lib/openany-hermes/setup")


def parser():
    p = argparse.ArgumentParser(description="One-command API/HTTPS and Seller Key setup for an existing Hermes. Hermes itself is never installed.")
    p.add_argument("--domain")
    p.add_argument("--public-ip", action="append", default=[])
    p.add_argument("--email")
    p.add_argument("--new-site", action="store_true", help="Create a new HTTPS site; otherwise reuse an existing TLS site and certificate")
    p.add_argument("--nginx-site", type=Path, help="Select an existing loaded Nginx site if discovery is ambiguous")
    p.add_argument("--cli-bin", help="Existing environment-specific OpenAny CLI executable")
    p.add_argument("--node-bin", help="Existing Node.js 22+ executable if not discoverable")
    p.add_argument("--skill", action="append", dest="skills", default=[], help="Installed Skill for the generated listing reference; repeat for multiple Skills")
    p.add_argument("--files", action="store_true", help="Generate mandatory auto_cli file-delivery contracts; omitted for JSON-only Skills")
    p.add_argument("--relay-port", type=int, help="Private CLI port, default 8790 in prod or 8791 in Dev")
    p.add_argument("--environment", choices=("prod", "dev"), default="prod")
    p.add_argument("--seller-key-file", type=Path, help="Private file; never pass the key value in arguments")
    p.add_argument("--save-seller-key", action="store_true", help="In an independent terminal, save hidden Seller Key input to a new private file; no setup or service changes")
    p.add_argument("--user")
    p.add_argument("--home", type=Path)
    p.add_argument("--hermes-bin")
    p.add_argument("--user-path", help=argparse.SUPPRESS)
    p.add_argument("--gateway-scope", choices=("auto", "system", "user"), default="auto")
    p.add_argument("--port", type=int, default=8642)
    p.add_argument("--check", "--dry-run", action="store_true", help="Read-only preflight; do not submit a system task")
    p.add_argument("--status", nargs="?", const="latest", help="Read the latest or a specific job result")
    p.add_argument("--wait", action="store_true", help="Wait for the independent job; recommended only from SSH")
    p.add_argument("--worker", help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version="OpenAny Hermes one-click setup " + VERSION)
    return p


def run_steps(runtime, progress):
    progress("preflight", "running")
    runtime.preflight()
    progress("hermes", "existing_verified")
    runtime.prepare_gateway()
    progress("credentials", "running")
    config = runtime.configure_user()
    runtime.bind_gateway(config)
    progress("credentials", "configured")
    if runtime.runtime_ready():
        progress("gateway", "skipped_verified")
    else:
        progress("gateway", "restarting")
        runtime.restart_gateway()
        runtime.wait_api()
        runtime.record_runtime()
        progress("gateway", "restarted")
    progress("cli_delivery", "configuring")
    delivery = runtime.prepare_delivery()
    progress("cli_delivery", "ready")
    if runtime.https_ready():
        progress("https", "skipped_verified")
    else:
        progress("https", "configuring")
        runtime.configure_https()
        progress("https", "configured")
    result = runtime.verify_https()
    listing = runtime.listing_reference()
    return {**result, "listing": listing, "delivery": delivery, "execution": {"adapter_type": "http_json", "delivery_mode": "poll", "artifact_delivery": "auto_cli"}, "api_key_file": config["api_key_file"], "seller_key": {"configured": True, "remote_verified": False, "environment_file": config["seller_env_file"], "variable": config.get("seller_variable"), "environment": runtime.cfg["environment"] if hasattr(runtime, "cfg") else "prod"}, "backups": {"api": config.get("api", {}).get("backup"), "seller": config.get("seller_backup"), "gateway": getattr(runtime, "binding", {}).get("backup"), "https": getattr(runtime, "https_result", {}).get("backup")}, "external_verification": "required_from_another_host", "file_delivery_and_billing": "not_tested", "model_tasks_started": 0}


def systemd_job_command(bundle, job):
    return ["systemd-run", "--unit=openany-hermes-setup-" + job, "--no-block", "--collect", "--property=Type=exec", "--property=ExecStartPre=/usr/bin/sleep 3", "--property=RuntimeMaxSec=1800", "--property=TimeoutStopSec=20", "/usr/bin/python3", "-B", str(Path(bundle) / "scripts/setup.py"), "--worker", job]


def seller_input(args, user, interactive=True):
    name = setup_user.seller_variable(args.environment)
    if args.seller_key_file:
        if not args.seller_key_file.is_absolute():
            raise common.Failure("Use an absolute --seller-key-file path so it cannot change meaning after sudo.")
        if not args.seller_key_file.exists():
            raise prerequisites.seller_required(args.environment, user)
        return setup_runtime.read_secret(args.seller_key_file, user["uid"])
    value = os.environ.get(name, "").strip()
    if value:
        return setup_user.validate_seller(value)
    path = Path(user["home"]) / ".openany-provider" / (args.environment + ".env")
    if path.exists():
        info = common.regular_file(path).stat()
        if info.st_uid != user["uid"] or info.st_mode & 0o077:
            raise common.Failure("Existing Seller Key environment file has unsafe ownership or permissions.")
        value = setup_user.read_environment(path).get(name, "")
        if value:
            return setup_user.validate_seller(value)
    # The selected .env is a data source only; never source or print it.
    env_path = Path(user["home"]) / ".env"
    if env_path.exists():
        quote = None
        for line in common.regular_file(env_path).read_text().splitlines():
            if quote is not None:
                if hermes_api.has_closing_quote(line, quote):
                    quote = None
                continue
            match = hermes_api.ASSIGNMENT.match(line)
            if not match:
                continue
            raw = match.group(2).lstrip()
            if raw[:1] in ("'", '"') and not hermes_api.has_closing_quote(raw[1:], raw[0]):
                quote = raw[0]
                continue
            if match.group(1) == name:
                import shlex
                values = shlex.split(raw, comments=True)
                if len(values) == 1:
                    return setup_user.validate_seller(values[0])
    if interactive and sys.stdin.isatty():
        print(str(prerequisites.seller_required(args.environment, user)), file=sys.stderr)
        return setup_user.validate_seller(getpass.getpass("OpenAny " + args.environment + " Seller Key (hidden): ").strip())
    raise prerequisites.seller_required(args.environment, user)


def save_seller_key(args, user):
    path = args.seller_key_file
    if path is None or not path.is_absolute():
        raise common.Failure("Use --save-seller-key with an absolute --seller-key-file path in a private directory.")
    if os.geteuid() != user['uid']:
        raise common.Failure("Save the key as the selected Hermes account, without sudo changing the owner.")
    if path.exists() or path.is_symlink():
        raise common.Failure("A Seller Key file already exists; reuse it. It will not be overwritten or rotated.")
    if not sys.stdin.isatty():
        raise prerequisites.seller_required(args.environment, user)
    parent = path.parent
    if parent.is_symlink():
        raise common.Failure("Use a private directory, not a symlink.")
    if parent.exists():
        info = parent.stat()
        if info.st_uid != user['uid'] or info.st_mode & 0o077:
            raise common.Failure("The key directory must belong to the Hermes account with mode 0700; no directory permissions were changed.")
    else:
        parent.mkdir(mode=0o700, parents=True)
    key = setup_user.validate_seller(getpass.getpass("OpenAny production Seller Key (hidden): " if args.environment == 'prod' else "OpenAny Dev Seller Key (hidden): ").strip())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w') as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(key + '\n')
    return {'ok': True, 'state': 'seller_key_saved', 'seller_key_file': str(path), 'environment': args.environment, 'remote_verified': False, 'setup_started': False}


def install_bundle(base=None):
    source = Path(__file__).resolve().parent
    files = sorted(source.glob("*.py"))
    digest = hashlib.sha256(b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in files)).hexdigest()[:16]
    base = Path(base) if base is not None else Path("/opt/openany-hermes-onboarding")
    owner = os.geteuid()
    if base.is_symlink() or (base.exists() and (base.stat().st_uid != owner or base.stat().st_mode & 0o022)):
        raise common.Failure("Installed tool directory must be root-owned and not writable by others.")
    base.mkdir(parents=True, exist_ok=True, mode=0o755)
    base.chmod(0o755)
    target = base / (VERSION + "-" + digest)
    if target.exists():
        directory = target / "scripts"
        if any(p.is_symlink() or not p.is_dir() or p.stat().st_uid != owner or p.stat().st_mode & 0o022 for p in (target, directory)) or any((directory / p.name).is_symlink() or not (directory / p.name).is_file() or (directory / p.name).stat().st_uid != owner or (directory / p.name).stat().st_mode & 0o022 or (directory / p.name).read_bytes() != p.read_bytes() for p in files):
            raise common.Failure("Existing installer bundle does not match the selected release.")
        target.chmod(0o755)
        directory.chmod(0o755)
        return target
    target.mkdir(mode=0o755)
    (target / "scripts").mkdir(mode=0o755)
    target.chmod(0o755)
    (target / "scripts").chmod(0o755)
    for path in files:
        common.atomic_write(target / "scripts" / path.name, path.read_bytes(), 0o644)
    return target


def result_path(user, job="latest"):
    if job != "latest" and not re.fullmatch(r"[a-f0-9]{24}", job):
        raise common.Failure("Invalid job ID.")
    return Path(user["home"]) / ".openany-provider/setup-jobs" / (job + ".json")


def read_status(user, job, wait=False):
    deadline = time.monotonic() + 1830
    while True:
        path = common.regular_file(result_path(user, job))
        state = json.loads(path.read_text())
        if not wait or state.get("state") in ("completed", "failed"):
            return state
        if time.monotonic() >= deadline:
            return {**state, "wait_timed_out": True, "completion_confirmed": False}
        time.sleep(2)


def queue(args):
    https_proxy.check_os()
    user = setup_runtime.identity(args.user, args.home)
    if args.status:
        return read_status(user, args.status, args.wait)
    if args.save_seller_key:
        return save_seller_key(args, user)
    setup_user.check_environment(user['home'], args.environment)
    if args.new_site and (not args.domain or not args.email or not args.public_ip):
        raise common.Failure("Only --new-site requires --domain, --public-ip and --email. The default reuses an existing HTTPS site.")
    domain, port = common.domain_name(args.domain) if args.domain else None, common.port_number(args.port)
    relay_port = common.port_number(args.relay_port or (8791 if args.environment == "dev" else 8790))
    if port in (80, 443) or relay_port in (80, 443) or port == relay_port:
        raise common.Failure("Use distinct non-public Hermes and CLI ports.")
    if args.new_site and (not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.email) or args.email.startswith("-")):
        raise common.Failure("Provide a valid certificate contact email.")
    if args.user_path and any(x in args.user_path for x in ("\n", "\x00")):
        raise common.Failure("Invalid user executable search path.")
    config = {"identity": user, "domain": domain, "public_ips": args.public_ip, "email": args.email, "port": port, "relay_port": relay_port, "new_site": args.new_site, "nginx_site": str(args.nginx_site) if args.nginx_site else None, "cli_bin": args.cli_bin, "node_bin": args.node_bin, "skills": args.skills, "environment": args.environment, "gateway_scope": args.gateway_scope, "hermes_bin": args.hermes_bin, "user_path": args.user_path}
    runtime = setup_runtime.Runtime(config, Path(__file__).resolve().parents[1], STATE)
    runtime.discover_hermes()
    runtime.discover_cli()
    if args.new_site:
        https_proxy.check_dns(domain, args.public_ip)
        runtime.inspect_new_site()
    else:
        selected = runtime.inspect_site()
        config.update(domain=selected["domain"], nginx_site=selected["path"], site_sha256=selected["sha256"])
        domain = selected["domain"]
        runtime.verify_existing_tls()
    config['files_required'] = args.files
    cli_delivery.listing(domain, args.environment, args.skills, args.files)
    if args.check:
        seller_input(args, user, interactive=False)
        return {"ok": True, "state": "checked", "hermes_home": user["home"], "hermes_user": user["username"], "hermes_executable": runtime.hermes, "environment": args.environment, "domain": domain, "runs_url": "https://" + domain + "/openany/v1/runs", "cli": runtime.cli, "system_changes": False, "seller_key_required": "private file or existing selected-environment configuration", "seller_key_remote_verified": False}
    if os.geteuid() != 0:
        raise common.Failure("Run the installer with sudo; user configuration will still run as the Hermes account.")
    key = seller_input(args, user)
    bundle = install_bundle()
    common.private_directory(STATE)
    common.atomic_write(STATE / "current-bundle", (str(bundle) + "\n").encode())
    job = uuid.uuid4().hex[:24]
    directory = common.private_directory(STATE / job)
    secret_file = directory / "seller.key"
    common.atomic_write(secret_file, (key + "\n").encode())
    config.update(job_id=job, seller_input=str(secret_file), bundle=str(bundle))
    common.atomic_write(directory / "config.json", json.dumps(config).encode())
    runtime = setup_runtime.Runtime(config, bundle, STATE)
    state = {"ok": True, "job_id": job, "state": "queued", "stage": "queued", "base_url": "https://" + domain, "hermes_home": user["home"], "environment": args.environment, "events": [], "status_file": str(result_path(user, job))}
    runtime.helper("progress", state)
    try:
        runtime.command(systemd_job_command(bundle, job))
    except BaseException:
        state.update(ok=False, state="failed", error="Independent setup job could not be submitted.")
        runtime.helper("progress", state)
        secret_file.unlink(missing_ok=True)
        raise
    if args.wait:
        return read_status(user, job, True)
    return {**state, "status_command": "sudo /usr/bin/python3 " + str(bundle / "scripts/setup.py") + " --user " + user["username"] + " --home " + __import__("shlex").quote(user["home"]) + " --status " + job, "note": "The independent job may restart Gateway. Queued is not completed; inspect the status file or use --status."}


def worker(job):
    if os.geteuid() != 0 or not re.fullmatch(r"[a-f0-9]{24}", job):
        raise common.Failure("Invalid privileged worker invocation.")
    directory = STATE / job
    config = json.loads(common.regular_file(directory / "config.json").read_text())
    runtime = setup_runtime.Runtime(config, config["bundle"], STATE)
    state = {"ok": True, "job_id": job, "state": "running", "stage": "starting", "environment": config["environment"], "base_url": "https://" + config["domain"], "hermes_home": config["identity"]["home"], "events": []}
    def progress(stage, outcome):
        state.update(stage=stage)
        state["events"].append({"stage": stage, "state": outcome, "at": int(time.time())})
        common.atomic_write(directory / "result.json", json.dumps(state, indent=2).encode())
        runtime.helper("progress", state)
    def interrupted(*args):
        raise common.Failure("Setup worker was interrupted; rerun the installer to verify and resume the remaining stages.")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with common.exclusive_lock(STATE / "setup.lock"):
            result = run_steps(runtime, progress)
        state.update(state="completed", stage="completed", result=result)
    except Exception as error:
        state.update(ok=False, state="failed", error=str(error) if isinstance(error, common.Failure) else "Setup failed; inspect service state and private backups. Secrets were suppressed.")
    finally:
        common.regular_file(config["seller_input"]).unlink(missing_ok=True)
        common.atomic_write(directory / "result.json", json.dumps(state, indent=2).encode())
        runtime.helper("progress", state)
    return state


def main():
    args = parser().parse_args()
    try:
        value = worker(args.worker) if args.worker else queue(args)
    except common.Failure as error:
        value = {"ok": False, "error": str(error)}
        if isinstance(error, prerequisites.Required):
            value.update(code=error.code, next_steps=error.next_steps, setup_started=False)
        print(json.dumps(value), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({"ok": False, "error": "Setup could not complete. Check paths, account permissions and the private job status; no secrets were printed."}), file=sys.stderr)
        return 1
    print(json.dumps(value, indent=2))
    return 1 if value.get("state") == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
