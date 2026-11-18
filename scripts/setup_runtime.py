"""Root operations for a separate systemd installer job; user writes stay unprivileged."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import time

import common
import https_proxy
import setup_user
import cli_delivery
import nginx_shared


def read_secret(path, uid):
    path = common.regular_file(Path(path).expanduser())
    info = path.stat()
    if info.st_uid not in (0, uid) or stat.S_IMODE(info.st_mode) & 0o077:
        raise common.Failure("Seller Key input must be a private 0600 file owned by the selected user or root.")
    return setup_user.validate_seller(path.read_text().strip())


def identity(username=None, home=None):
    username = username or os.environ.get("SUDO_USER") or (pwd.getpwuid(os.geteuid()).pw_name if os.geteuid() != 0 else None)
    if not username:
        raise common.Failure("When running directly as root, specify --user for the account that owns Hermes.")
    try:
        user = pwd.getpwnam(username)
    except KeyError:
        raise common.Failure("Selected Linux account does not exist.") from None
    home = Path(home or str(Path(user.pw_dir) / ".hermes")).expanduser().absolute()
    if any(char in str(home) for char in ("\n", "\r", "\x00")):
        raise common.Failure("Hermes home cannot contain control characters.")
    if home.is_symlink() or not home.is_dir() or home.stat().st_uid != user.pw_uid:
        raise common.Failure("Existing Hermes home was not found or belongs to another account. This installer does not install Hermes itself.")
    return {"username": user.pw_name, "uid": user.pw_uid, "gid": user.pw_gid, "login_home": user.pw_dir, "home": str(home)}


def matching_home(environment, expected, login_home):
    try:
        values = dict(item.split("=", 1) for item in shlex.split(environment) if "=" in item)
    except ValueError:
        return False
    value = values.get("HERMES_HOME", "").replace("%h", login_home)
    return bool(value) and Path(value).expanduser().resolve() == Path(expected).resolve()


def process_credentials_match(raw, expected):
    values = dict(part.split(b"=", 1) for part in raw.split(b"\0") if b"=" in part)
    return bool(expected) and all(hmac.compare_digest(values.get(name.encode(), b""), value.encode()) for name, value in expected.items())


class Runtime:
    def __init__(self, config, bundle, state_root):
        self.cfg, self.bundle, self.state_root = config, Path(bundle), Path(state_root)
        self.user = config["identity"]
        self.home = Path(self.user["home"])
        self.scope, self.unit, self.user_config = None, None, None
        self.binding_changed = False
        # The existing verifier checks that root is reading the selected operator's key.
        os.environ["SUDO_UID"] = str(self.user["uid"])

    def command(self, args, payload=None, allow_failure=False, timeout=600):
        try:
            result = subprocess.run(args, input=payload, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise common.Failure("A setup command could not complete: " + Path(args[0]).name) from None
        if result.returncode and not allow_failure:
            raise common.Failure("Command failed: " + Path(args[0]).name + ". Inspect the selected service's journal; credential output was suppressed.")
        return result

    def as_user(self, args, payload=None, allow_failure=False):
        user = self.user
        path = self.cfg.get("user_path") or f"{user['login_home']}/.local/bin:{user['login_home']}/.hermes/bin:/usr/local/bin:/usr/bin:/bin"
        environment = ["/usr/bin/env", "-i", "HOME=" + user["login_home"], "USER=" + user["username"], "LOGNAME=" + user["username"], "HERMES_HOME=" + str(self.home), "PATH=" + path, "XDG_RUNTIME_DIR=/run/user/" + str(user["uid"]), "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/" + str(user["uid"]) + "/bus"]
        prefix = ["runuser", "-u", user["username"], "--"] if os.geteuid() == 0 and user["uid"] != 0 else []
        return self.command(prefix + environment + args, payload, allow_failure)

    def helper(self, action, payload, unit=None):
        args = ["/usr/bin/python3", "-B", str(self.bundle / "scripts/setup_user.py"), action, "--home", str(self.home), "--environment", self.cfg["environment"], "--port", str(self.cfg["port"])]
        if unit:
            args += ["--unit", unit]
        response = self.as_user(args, json.dumps(payload), allow_failure=True)
        try:
            value = json.loads(response.stdout)
        except ValueError:
            raise common.Failure("User configuration helper failed without a valid result.") from None
        if response.returncode or not value.get("ok"):
            raise common.Failure(value.get("error", "User configuration failed."))
        return value

    def discover_hermes(self):
        candidate = self.cfg.get("hermes_bin")
        if not candidate:
            code = "import shutil; from pathlib import Path; p=shutil.which('hermes'); print(p or next((str(x) for x in (Path.home()/'.local/bin/hermes',Path.home()/'.hermes/bin/hermes',Path.home()/'hermes/hermes-agent/.venv/bin/hermes') if x.is_file()),''))"
            candidate = self.as_user(["/usr/bin/python3", "-c", code]).stdout.strip()
        if not candidate or not Path(candidate).is_absolute():
            raise common.Failure("Hermes executable was not detected. Install Hermes separately or provide --hermes-bin; no Hermes installation was attempted.")
        self.as_user([candidate, "--version"])
        self.hermes = candidate
        return candidate

    def discover_cli(self):
        package, version, command, origin = cli_delivery.PACKAGES[self.cfg['environment']]
        private_cli = self.home / '.openany-provider/tools' / self.cfg['environment'] / 'node_modules' / package / 'bin/openany.js'
        code = """import json,shutil,sys,re
from pathlib import Path
node=sys.argv[4] or shutil.which('node')
if not node:
    candidates=[]
    for p in (Path.home()/'.nvm/versions/node').glob('v*/bin/node'):
        m=re.fullmatch(r'v(\\d+)\\.(\\d+)\\.(\\d+)',p.parents[1].name)
        if m and int(m[1])>=22: candidates.append((tuple(map(int,m.groups())),str(p)))
    node=max(candidates)[1] if candidates else None
binary=Path(node).parent if node else None
cli=sys.argv[1] or (str(Path(sys.argv[3])) if Path(sys.argv[3]).is_file() else shutil.which(sys.argv[2]))
if not cli and binary and (binary/sys.argv[2]).is_file(): cli=str(binary/sys.argv[2])
npm=str(binary/'npm') if binary and (binary/'npm').is_file() else shutil.which('npm')
print(json.dumps({name:str(Path(value).resolve()) if value else None for name,value in {'node':node,'npm':npm,'cli':cli}.items()}))
"""
        result = self.as_user(['/usr/bin/python3', '-c', code, self.cfg.get('cli_bin') or '', command, str(private_cli), self.cfg.get('node_bin') or ''])
        try:
            found = json.loads(result.stdout)
        except ValueError:
            raise common.Failure('Cannot discover Node.js and the selected OpenAny CLI.') from None
        if not found.get('node'):
            raise common.Failure('Node.js 22+ is required for CLI automatic delivery. Install it for the Hermes user and rerun; no API/HTTPS configuration has been changed.')
        node_version = self.as_user([found['node'], '--version']).stdout.strip()
        cli_delivery.validate_node(node_version)
        if found.get('cli'):
            help_text = self.as_user([found['node'], found['cli'], '--help']).stdout
            cli_delivery.validate_help(self.cfg['environment'], help_text, node_version)
        elif not found.get('npm'):
            raise common.Failure('The selected CLI is missing and npm is unavailable; install ' + package + '@' + version + ' for the Hermes account.')
        self.cli = {**found, 'command': command, 'package': package, 'version': version, 'install_required': not bool(found.get('cli')), 'node_version': node_version}
        return self.cli

    def inspect_site(self):
        self.command(['nginx', '-t'])
        sources = nginx_shared.read_sources(self.command(['nginx', '-T']).stdout)
        selected = nginx_shared.select_site(sources, self.cfg.get('domain'), self.cfg.get('nginx_site'))
        self.selected_site = selected
        return selected

    def preflight(self):
        https_proxy.check_os()
        setup_user.check_environment(self.home, self.cfg['environment'])
        if self.cfg.get('new_site'):
            self.addresses = https_proxy.check_dns(self.cfg["domain"], self.cfg["public_ips"])
        else:
            selected = self.inspect_site()
            if self.cfg.get('site_sha256') and self.cfg['site_sha256'] != selected['sha256']:
                raise common.Failure('Existing HTTPS site changed after setup was queued; rerun to inspect it again.')
            self.addresses = set()
        self.discover_hermes()
        self.discover_cli()
        if not shutil.which("ss"):
            self.command(["apt-get", "update"])
            self.command(["apt-get", "install", "-y", "iproute2"])
        output = self.command(["ss", "-H", "-ltnp"]).stdout
        for line in output.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[3].rsplit(":", 1)[-1] in ("80", "443") and '"nginx"' not in line:
                raise common.Failure("Port 80/443 belongs to another service; resolve the conflict before setup.")

    def control(self, scope, args, allow_failure=False):
        if scope == "user":
            return self.as_user(["systemctl", "--user", *args], allow_failure=allow_failure)
        return self.command(["systemctl", *args], allow_failure=allow_failure)

    def find_gateways(self):
        matches = []
        scope_filter = self.cfg.get("gateway_scope", "auto")
        for scope in ("system", "user"):
            if scope_filter != "auto" and scope != scope_filter:
                continue
            result = self.control(scope, ["list-unit-files", "--type=service", "--no-legend", "hermes-gateway*.service"], True)
            if result.returncode:
                continue
            for line in result.stdout.splitlines():
                name = line.split()[0] if line.split() else ""
                if not re.fullmatch(r"hermes-gateway(?:-[A-Za-z0-9_-]+)?\.service", name):
                    continue
                props = self.control(scope, ["show", name, "--property=Environment", "--property=User"], True)
                if props.returncode:
                    continue
                values = dict(row.split("=", 1) for row in props.stdout.splitlines() if "=" in row)
                owner = values.get("User", "")
                if scope == "system" and (owner or "root") not in (self.user["username"], str(self.user["uid"])):
                    continue
                if matching_home(values.get("Environment", ""), self.home, self.user["login_home"]):
                    matches.append((scope, name))
        return matches

    def prepare_gateway(self):
        found = self.find_gateways()
        if not found:
            output = self.command(["ss", "-H", "-ltnp"]).stdout
            if any(len(row.split()) >= 5 and row.split()[3].endswith(":" + str(self.cfg["port"])) for row in output.splitlines()):
                raise common.Failure("An API listener exists without an identifiable matching Gateway unit; automatic takeover was refused.")
            if self.cfg.get("gateway_scope") == "system" or self.user["uid"] == 0:
                self.command(["/usr/bin/env", "HERMES_HOME=" + str(self.home), "HOME=" + self.user["login_home"], self.hermes, "gateway", "install", "--system", "--run-as-user", self.user["username"]])
            else:
                self.command(["loginctl", "enable-linger", self.user["username"]])
                self.command(["systemctl", "start", "user@" + str(self.user["uid"]) + ".service"])
                self.as_user([self.hermes, "gateway", "install"])
            found = self.find_gateways()
        if len(found) != 1:
            raise common.Failure("A unique Gateway unit for the selected home could not be identified. Check --gateway-scope and installed services.")
        self.scope, self.unit = found[0]
        if self.scope == "user":
            linger = self.command(["loginctl", "show-user", self.user["username"], "--property=Linger", "--value"], allow_failure=True)
            if linger.returncode or linger.stdout.strip() != "yes":
                self.command(["loginctl", "enable-linger", self.user["username"]])
        if self.control(self.scope, ["is-enabled", "--quiet", self.unit], True).returncode:
            self.control(self.scope, ["enable", self.unit])

    def configure_user(self):
        seller = read_secret(Path(self.cfg["seller_input"]), self.user["uid"])
        self.user_config = self.helper("configure", {"seller_key": seller})
        return self.user_config

    def bind_gateway(self, config):
        if self.scope == "user":
            result = self.helper("dropin", {}, self.unit)
        else:
            directory = Path("/etc/systemd/system") / (self.unit + ".d")
            if directory.is_symlink():
                raise common.Failure("Refusing a symlinked Gateway drop-in directory.")
            directory.mkdir(parents=True, exist_ok=True, mode=0o755)
            path = common.regular_file(directory / "openany-provider.conf")
            if path.exists() and not path.read_text().startswith("# Managed by OpenAny Hermes one-click setup"):
                raise common.Failure("An unmanaged Gateway environment drop-in exists.")
            content = setup_user.dropin_text(config["seller_env_file"]).encode()
            changed = not path.exists() or path.read_bytes() != content
            with common.Transaction(self.state_root / "backups") as tx:
                if changed:
                    tx.write(path, content, 0o644)
            result = {"changed": changed, "path": str(path), "backup": str(tx.directory) if tx.directory else None}
        self.binding = result
        self.binding_changed = result["changed"]
        if self.binding_changed:
            self.control(self.scope, ["daemon-reload"])
        fingerprint = {"unit": self.unit, "scope": self.scope, "port": self.cfg["port"], "environment": self.cfg["environment"], "api": hashlib.sha256(common.read_key(config["api_key_file"]).encode()).hexdigest(), "seller": hashlib.sha256(Path(config["seller_env_file"]).read_bytes()).hexdigest()}
        self.fingerprint = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()

    def local_api(self):
        https_proxy.check_listeners(self.command(["ss", "-H", "-ltnp"]).stdout, self.cfg["port"])
        return common.verify_api("http://127.0.0.1:" + str(self.cfg["port"]), common.read_key(self.user_config["api_key_file"]))

    def runtime_ready(self):
        marker = self.home / ".openany-provider" / ("runtime-" + self.cfg["environment"] + ".json")
        try:
            if self.binding_changed or json.loads(common.regular_file(marker).read_text()).get("fingerprint") != self.fingerprint:
                return False
            if self.control(self.scope, ["is-active", "--quiet", self.unit], True).returncode:
                return False
            if not self.credentials_loaded():
                return False
            self.local_api()
            return True
        except (common.Failure, OSError, ValueError):
            return False

    def restart_gateway(self):
        if self.scope == "user":
            self.as_user([self.hermes, "gateway", "restart"])
        else:
            self.command(["/usr/bin/env", "HERMES_HOME=" + str(self.home), "HOME=" + self.user["login_home"], self.hermes, "gateway", "restart", "--system"])

    def credentials_loaded(self):
        result = self.control(self.scope, ["show", self.unit, "--property=MainPID", "--value"], True)
        pid = result.stdout.strip()
        if result.returncode or not pid.isdigit() or int(pid) < 1:
            return False
        try:
            expected = setup_user.read_environment(Path(self.user_config["seller_env_file"]))
            if not expected.get(setup_user.seller_variable(self.cfg["environment"])) or not expected.get("OPENANY_TASK_ROOT"):
                return False
            return process_credentials_match((Path("/proc") / pid / "environ").read_bytes(), expected)
        except OSError:
            return False

    def wait_api(self):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                self.local_api()
                if self.credentials_loaded():
                    return
                time.sleep(2)
            except common.Failure:
                time.sleep(2)
        raise common.Failure("Gateway did not provide the authenticated loopback API within 120 seconds.")

    def record_runtime(self):
        self.helper("marker", {"fingerprint": self.fingerprint})

    def prepare_delivery(self):
        key = common.read_key(self.user_config['api_key_file'])
        native = 'http://127.0.0.1:' + str(self.cfg['port'])
        status, caps = common.request_json(native, '/v1/capabilities', key)
        if status != 200: raise common.Failure('Native API is unavailable for automatic delivery.')
        self.retention_seconds = cli_delivery.check_capabilities(caps)
        if self.cli['install_required']:
            prefix = self.home / '.openany-provider/tools' / self.cfg['environment']
            self.as_user([self.cli['node'], self.cli['npm'], 'install', '--prefix', str(prefix), '--registry', 'https://registry.npmjs.org', '--ignore-scripts', '--no-audit', '--no-fund', self.cli['package'] + '@' + self.cli['version']])
            self.discover_cli()
            if self.cli['install_required']: raise common.Failure('The pinned CLI installation did not provide its expected executable.')
        self.command(['loginctl', 'enable-linger', self.user['username']])
        self.command(['systemctl', 'start', 'user@' + str(self.user['uid']) + '.service'])
        result = self.helper('delivery', {'relay_port': self.cfg['relay_port'], 'node_bin': self.cli['node'], 'cli_bin': self.cli['cli']})
        unit = result['unit']
        active = self.control('user', ['is-active', '--quiet', unit], True).returncode == 0
        listeners = self.command(['ss', '-H', '-ltnp']).stdout
        occupied = any(len(row.split()) >= 5 and row.split()[3].rsplit(':', 1)[-1] == str(self.cfg['relay_port']) for row in listeners.splitlines())
        if occupied and not active:
            raise common.Failure('The private CLI relay port belongs to an unknown process; select --relay-port or review the existing listener.')
        if result['changed']: self.control('user', ['daemon-reload'])
        if self.control('user', ['is-enabled', '--quiet', unit], True).returncode:
            self.control('user', ['enable', unit])
        expected = setup_user.read_environment(Path(result['environment_file']))
        def loaded():
            value = self.control('user', ['show', unit, '--property=MainPID', '--value'], True)
            pid = value.stdout.strip()
            if value.returncode or not pid.isdigit() or int(pid) < 1: return False
            try: return process_credentials_match((Path('/proc') / pid / 'environ').read_bytes(), expected)
            except OSError: return False
        if result['changed'] or not active or not loaded():
            self.control('user', ['restart', unit])
        relay = 'http://127.0.0.1:' + str(self.cfg['relay_port'])
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                common.verify_api(relay, key)
                if loaded():
                    self.delivery_config = result
                    return {**result, 'cli': self.cli['command'], 'durable_runs_retention_seconds': self.retention_seconds, 'local_service_verified': True, 'file_upload_and_billing': 'not_tested'}
            except common.Failure:
                pass
            time.sleep(2)
        raise common.Failure('The CLI relay did not provide authenticated discovery with its selected credentials within 90 seconds.')

    def listing_reference(self):
        key = common.read_key(self.user_config['api_key_file'])
        status, items = common.request_json('http://127.0.0.1:' + str(self.cfg['port']), '/v1/skills', key)
        if isinstance(items, dict): items = items.get('data')
        if status != 200 or not isinstance(items, list): raise common.Failure('Cannot enumerate installed Skills for the listing reference.')
        names = [item['name'] for item in items if isinstance(item, dict) and isinstance(item.get('name'), str)]
        skills = self.cfg.get('skills') or names
        if any(skill not in names for skill in skills): raise common.Failure('A requested Skill is not installed in the selected Hermes home; no product was created.')
        return self.helper('listing', {'domain': self.cfg['domain'], 'skills': skills, 'files_required': self.cfg.get('files_required', False)})

    def https_ready(self):
        try:
            selected = self.inspect_site()
            if nginx_shared.patch_site(selected['text'], selected, self.cfg['relay_port']) != selected['text']:
                return False
            if self.command(['systemctl', 'is-active', '--quiet', 'nginx'], allow_failure=True).returncode: return False
            self.verify_https()
            return True
        except (common.Failure, OSError):
            return False

    def configure_https(self):
        certificate = None
        if self.cfg.get('new_site'):
            certificate = https_proxy.configure_proxy(self.cfg["domain"], self.cfg["port"], self.cfg["email"], self.cfg["public_ips"], Path(self.user_config["api_key_file"]), True, True)
        selected = self.inspect_site()
        self.https_result = nginx_shared.apply_site(selected, self.cfg['relay_port'], self.state_root / 'backups', lambda args: self.command(args).stdout, self.verify_https)
        if certificate: self.https_result['certificate_setup'] = certificate

    def verify_https(self):
        return nginx_shared.verify_public(self.cfg['domain'], common.read_key(self.user_config['api_key_file']))
