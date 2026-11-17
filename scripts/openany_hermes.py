#!/usr/bin/env python3
"""OpenAny Hermes HTTPS onboarding, version 0.1.1."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import common
import hermes_api
import https_proxy


def api_port(value):
    try:
        return common.port_number(value)
    except common.Failure as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def parser():
    cli = argparse.ArgumentParser(description="Expose an existing Hermes API through HTTPS on Ubuntu/Debian. Preview by default; no model tasks are started.")
    cli.add_argument("--version", action="version", version="OpenAny Hermes onboarding 0.1.1")
    sub = cli.add_subparsers(dest="command", required=True)
    api = sub.add_parser("api-config", help="Enable API Server and reuse/generate its private Bearer key (preview by default)")
    api.add_argument("--home", type=Path, required=True, help="Existing Hermes home, owned by the current account")
    api.add_argument("--port", type=api_port, default=8642)
    api.add_argument("--require-existing-key", action="store_true", help="Dashboard handoff: require a saved key and never generate a replacement")
    api.add_argument("--apply", action="store_true", help="Back up/update API settings and explicit YAML disable flags, and write private key file; does not restart Gateway")
    for name, description in (("check", "Read-only DNS, listener, auth and Nginx checks; use sudo for full listener visibility"), ("https", "Configure Nginx, certificate and renewal (preview by default)")):
        command = sub.add_parser(name, help=description)
        command.add_argument("--domain", required=True)
        command.add_argument("--public-ip", action="append", required=True, help="Repeat for EVERY expected A/AAAA address")
        command.add_argument("--port", type=api_port, default=8642)
        command.add_argument("--key-file", type=Path, help="Private file containing the Hermes API_SERVER_KEY")
        if name == "https":
            command.add_argument("--email", required=True, help="Contact email for the certificate authority")
            command.add_argument("--install-deps", action="store_true", help="Allow apt installation of missing nginx/certbot packages")
            command.add_argument("--apply", action="store_true", help="Apply system changes and accept Let's Encrypt terms via Certbot")
    verify = sub.add_parser("verify", help="Verify TLS, auth, capabilities and skills using only GET requests")
    verify.add_argument("--base-url", required=True, help="HTTPS origin, or http://127.0.0.1:8642 for local checks")
    verify.add_argument("--key-file", type=Path, required=True)
    restore = sub.add_parser("restore", help="Restore a private configuration backup (preview by default)")
    restore.add_argument("--backup", type=Path, required=True)
    scope = restore.add_mutually_exclusive_group(required=True)
    scope.add_argument("--home", type=Path, help="Restore managed .env/config.yaml/openany-api.key changes in this Hermes home")
    scope.add_argument("--domain", help="Restore only this domain's managed Nginx site and renewal hook")
    restore.add_argument("--apply", action="store_true")
    return cli


def restore(args):
    if args.home:
        home = hermes_api.validate_home(args.home)
        allowed, backup_root = {home / ".env", home / "config.yaml", home / "openany-api.key"}, home / ".openany-backups"
    else:
        domain = common.domain_name(args.domain)
        paths = https_proxy.Paths()
        allowed, backup_root = {paths.site(domain), paths.hook(domain)}, paths.backups
        for path in allowed:
            https_proxy.require_managed(path)
        if args.apply:
            https_proxy.check_os()
            if os.geteuid() != 0:
                raise common.Failure("Domain restoration requires sudo.")
    if args.backup.absolute().parent.resolve() != backup_root.resolve():
        raise common.Failure("Backup must be a direct child of the matching managed backup directory.")
    entries = common.load_snapshot(args.backup, allowed)
    if not args.apply:
        return {"mode": "preview", "restore_targets": [entry["path"] for entry in entries], "system_changes": False}
    lock = https_proxy.operation_lock(paths) if args.domain else common.exclusive_lock(home / ".openany-config.lock")
    with lock:
        return apply_restore(args, entries, backup_root)


def apply_restore(args, entries, backup_root):
    try:
        with common.Transaction(backup_root) as tx:
            for entry in reversed(entries):
                if entry["existed"]:
                    tx.write(Path(entry["path"]), (args.backup / entry["data"]).read_bytes(), entry["mode"])
                else:
                    tx.remove(Path(entry["path"]))
            if args.domain:
                https_proxy.reload_nginx(https_proxy.run_command)
    except BaseException:
        if args.domain:
            https_proxy.reload_nginx(https_proxy.run_command)
        raise
    return {"mode": "apply", "restored": True, "undo_backup": str(tx.directory), "gateway_restart_required": bool(args.home)}


def execute(args):
    if args.command == "api-config":
        return hermes_api.configure_api(args.home, args.port, args.apply, args.require_existing_key)
    if args.command == "https":
        return https_proxy.configure_proxy(args.domain, args.port, args.email, args.public_ip, args.key_file, args.apply, args.install_deps)
    if args.command == "verify":
        result = common.verify_api(args.base_url, common.read_key(args.key_file))
        result["key_file"] = str(args.key_file.expanduser().absolute())
        return result
    if args.command == "check":
        domain = common.domain_name(args.domain)
        actual, _, verified = https_proxy.check_environment(domain, args.port, args.public_ip, args.key_file)
        dependencies = [tool for tool in ("nginx", "certbot") if not https_proxy.shutil.which(tool)]
        if "nginx" not in dependencies:
            https_proxy.check_nginx(domain, https_proxy.Paths())
        return {"mode": "read_only", "dns_addresses": sorted(actual), "local_api": verified, "missing_dependencies": dependencies, "public_reachability": "not_proven"}
    return restore(args)


def main():
    args = parser().parse_args()
    try:
        result = execute(args)
    except common.Failure as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"ok": False, "error": "Operation failed while reading or writing configuration. Check file existence, ownership, permissions and the private backup manifest; secrets were not printed."}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "Interrupted. Review the managed backup directory before retrying."}), file=sys.stderr)
        return 130
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
