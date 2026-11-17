"""Shared validation, private snapshots and read-only API verification."""
import datetime
import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Failure(Exception):
    """An operator-facing error that must not contain credentials or response bodies."""


@contextlib.contextmanager
def exclusive_lock(path):
    path = regular_file(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        info = os.fstat(handle.fileno())
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise Failure("Operation lock must be owned by this account with private permissions.")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("Another configuration operation is running. Wait for it to finish.") from None
        yield


def domain_name(value):
    value = value.lower()
    labels = value.split(".")
    if len(value) > 253 or len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ) or labels[-1].isdigit():
        raise Failure("Use a DNS hostname such as hermes.example.com, without a URL, wildcard or trailing dot.")
    return value


def port_number(value):
    try:
        port = int(value)
    except (ValueError, TypeError):
        raise Failure("API port must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise Failure("API port must be an integer from 1 to 65535.")
    return port


def regular_file(path):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise Failure("Refusing a symbolic link or non-regular configuration file: " + str(path))
    return path


def private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise Failure("Refusing a symbolic link for a private directory.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.geteuid():
        raise Failure("Private directory must belong to the account running this command.")
    path.chmod(0o700)
    return path


def atomic_write(path, data, mode=0o600, owner=None):
    path = regular_file(path)
    fd, temporary = tempfile.mkstemp(prefix=".openany-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), mode)
            if owner is not None and os.geteuid() == 0:
                os.fchown(output.fileno(), *owner)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Transaction:
    """Snapshots managed files before changes; restores them on exceptions."""
    def __init__(self, backup_root):
        self.backup_root = Path(backup_root)
        self.directory = None
        self.entries = []

    def __enter__(self):
        return self

    def _snapshot(self, path):
        path = regular_file(Path(path).absolute())
        if any(entry["path"] == str(path) for entry in self.entries):
            return
        if self.directory is None:
            private_directory(self.backup_root)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.directory = private_directory(self.backup_root / (stamp + "-" + uuid.uuid4().hex[:10]))
        entry = {"path": str(path), "existed": path.exists()}
        if path.exists():
            info = path.stat()
            entry.update(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid, data=str(len(self.entries)) + ".bak")
            atomic_write(self.directory / entry["data"], path.read_bytes())
        self.entries.append(entry)
        atomic_write(self.directory / "manifest.json", json.dumps({"version": 1, "files": self.entries}, indent=2).encode())

    def write(self, path, data, mode=0o600):
        path = Path(path)
        self._snapshot(path)
        owner = (path.stat().st_uid, path.stat().st_gid) if path.exists() else None
        atomic_write(path, data, mode, owner)

    def remove(self, path):
        path = regular_file(path)
        self._snapshot(path)
        path.unlink(missing_ok=True)

    def rollback(self):
        for entry in reversed(self.entries):
            restore_entry(self.directory, entry)

    def __exit__(self, kind, value, traceback):
        if kind is not None:
            self.rollback()
        return False


def restore_entry(directory, entry):
    path = regular_file(entry["path"])
    if entry["existed"]:
        source = regular_file(Path(directory) / entry["data"])
        atomic_write(path, source.read_bytes(), entry["mode"], (entry["uid"], entry["gid"]))
    else:
        path.unlink(missing_ok=True)


def load_snapshot(directory, allowed):
    directory = Path(directory).absolute()
    info = directory.stat()
    if directory.is_symlink() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise Failure("Backup directory must be owned by this account with mode 0700.")
    manifest_path = regular_file(directory / "manifest.json")
    manifest = json.loads(manifest_path.read_text())
    allowed = {str(Path(p).absolute()) for p in allowed}
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise Failure("Unrecognized backup manifest.")
    for entry in manifest["files"]:
        if entry.get("path") not in allowed:
            raise Failure("Backup targets do not match the selected domain or Hermes home.")
        regular_file(entry["path"])
        if entry.get("existed"):
            if not re.fullmatch(r"[0-9]+\.bak", entry.get("data", "")):
                raise Failure("Invalid backup data filename.")
            source = regular_file(directory / entry["data"])
            if source.stat().st_uid != os.geteuid() or stat.S_IMODE(source.stat().st_mode) & 0o077:
                raise Failure("Backup data permissions are not private.")
            if not 0 <= entry["mode"] <= 0o777:
                raise Failure("Invalid backup file mode.")
    return manifest["files"]


def restore_snapshot(directory, allowed):
    entries = load_snapshot(directory, allowed)
    for entry in reversed(entries):
        restore_entry(directory, entry)


def validate_key(key):
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{16,512}", key):
        raise Failure("Hermes API key must be a literal 16-512 character token using letters, digits or . _ ~ + / = -. Existing key was not rotated; correct it in Dashboard and save again.")
    lowered = key.lower()
    tail = re.sub(r"^(?:sk-|ghp_|hf_)", "", lowered)
    shape = lowered.replace("-", "").replace("_", "")
    if (lowered.startswith("your_") and lowered.endswith("_here")) or set(tail) == {"x"} or set(shape) == {"x"}:
        raise Failure("Hermes rejects placeholder API keys. Configure a real random key in Dashboard; it will not be replaced automatically.")
    return key


def read_key(path):
    path = regular_file(Path(path).expanduser())
    info = path.stat()
    if info.st_uid not in (os.geteuid(), int(os.environ.get("SUDO_UID", os.geteuid()))) or stat.S_IMODE(info.st_mode) & 0o077:
        raise Failure("Key file must be private (chmod 600) and owned by the operator.")
    key = path.read_text().strip()
    return validate_key(key)


def validate_base_url(base):
    parts = urllib.parse.urlsplit(base)
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise Failure("Base URL must contain only scheme, host and optional port; never put a key in the URL.")
    try:
        host, port = parts.hostname, parts.port
    except ValueError:
        raise Failure("Invalid base URL.") from None
    if not host:
        raise Failure("Base URL requires a hostname.")
    if parts.scheme == "http":
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise Failure("Plain HTTP is permitted only for a loopback IP address. Use HTTPS for public access.")
    elif parts.scheme != "https":
        raise Failure("Use an HTTPS base URL.")
    if port is not None:
        port_number(port)
    return base.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(base, path, key=None):
    base = validate_base_url(base)
    headers = {"Accept": "application/json"}
    if key is not None:
        headers["Authorization"] = "Bearer " + key
    # Never forward a credential through an environment-configured proxy or redirect.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(urllib.request.Request(base + path, headers=headers), timeout=15)
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Failure("Connection or TLS verification failed. Check DNS, certificate, firewall and API listener.") from None
    with response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise Failure("API discovery response exceeds the 4 MiB safety limit.")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise Failure("API discovery returned invalid JSON.") from None
        return response.status, body


def verify_api(base, key, fetch=request_json):
    base = validate_base_url(base)
    for path in ("/v1/capabilities", "/v1/skills"):
        status, _ = fetch(base, path)
        if status not in (401, 403):
            raise Failure("Unauthenticated discovery was not rejected with 401/403. Check API_SERVER_KEY and proxy routing.")
    status, caps = fetch(base, "/v1/capabilities", key)
    if status != 200 or not isinstance(caps, dict) or caps.get("object") != "hermes.api_server.capabilities":
        raise Failure("Authenticated capabilities discovery failed; check the key and Hermes API version.")
    features = caps.get("features", {})
    if not isinstance(features, dict) or features.get("run_submission") is not True or features.get("run_status") is not True:
        raise Failure("Hermes does not advertise both run_submission and run_status. Upgrade or check the selected profile.")
    status, skills = fetch(base, "/v1/skills", key)
    if isinstance(skills, dict) and skills.get("object") == "list":
        skills = skills.get("data")
    if status != 200 or not isinstance(skills, list) or any(not isinstance(s, dict) or not isinstance(s.get("name"), str) for s in skills):
        raise Failure("Authenticated Skill discovery failed or returned an unexpected schema.")
    return {"base_url": base, "authenticated": True, "skill_count": len(skills), "runs_url": base + "/v1/runs", "skills_url": base + "/v1/skills", "model_tasks_started": 0}
