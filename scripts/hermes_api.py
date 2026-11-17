"""Configure an existing Hermes home without evaluating its environment file."""
import os
from pathlib import Path
import re
import secrets
import shlex

import common
from common import Failure
from yaml_flags import enable_api_flags

ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
MANAGED = {"API_SERVER_ENABLED", "API_SERVER_HOST", "API_SERVER_PORT", "API_SERVER_KEY"}


def has_closing_quote(text, quote):
    escaped = False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return True
    return False


def yaml_may_supply_key(path):
    """Conservative detection only: never parse/execute arbitrary YAML to extract a secret."""
    if not path.exists():
        return False
    text = path.read_text()
    if re.search(r"api_server\s*:\s*\{[^}\n]*\bkey\s*:", text):
        return True
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)api_server\s*:\s*([^#]*)", line)
        if not match:
            continue
        if match.group(2).strip():
            return True  # A flow mapping, anchor or alias requires explicit operator review.
        indent = len(match.group(1))
        for child in lines[index + 1:]:
            if not child.strip() or child.lstrip().startswith("#"):
                continue
            if len(child) - len(child.lstrip()) <= indent:
                break
            if re.match(r"\s*(?:key|<<)\s*:", child):
                return True
    return False


def validate_home(home):
    home = Path(home).expanduser().absolute()
    if home.is_symlink() or not home.is_dir() or home.stat().st_uid != os.geteuid():
        raise Failure("Hermes home must be an existing directory owned by the current account. Run api-config as the Hermes user.")
    for name in (".env", "openany-api.key", "config.yaml"):
        path = common.regular_file(home / name)
        if path.exists() and path.stat().st_uid != os.geteuid():
            raise Failure("Hermes configuration must belong to the current account.")
    return home


def configure_api(home, port, apply=False, require_existing=False):
    home = validate_home(home)
    if apply:
        with common.exclusive_lock(home / ".openany-config.lock"):
            return _configure_api(home, port, apply, require_existing)
    return _configure_api(home, port, apply, require_existing)


def _configure_api(home, port, apply, require_existing):
    home, port = validate_home(home), common.port_number(port)
    env_path, key_path = home / ".env", home / "openany-api.key"
    yaml_path = home / "config.yaml"
    original_yaml = yaml_path.read_text() if yaml_path.exists() else ""
    desired_yaml, yaml_changes = enable_api_flags(original_yaml)
    original = env_path.read_text() if env_path.exists() else ""
    preserved, existing, multiline_quote = [], None, None
    for line in original.splitlines(keepends=True):
        if multiline_quote is not None:
            preserved.append(line)
            if has_closing_quote(line, multiline_quote):
                multiline_quote = None
            continue
        match = ASSIGNMENT.match(line.rstrip("\r\n"))
        if match:
            raw_value = match.group(2).lstrip()
            if raw_value[:1] in ("'", '"') and not has_closing_quote(raw_value[1:], raw_value[0]):
                if match.group(1) in MANAGED:
                    raise Failure("Managed API settings must be single-line literal values; configuration was not changed.")
                multiline_quote = raw_value[0]
                preserved.append(line)
                continue
        if match and match.group(1) in MANAGED:
            if match.group(1) == "API_SERVER_KEY":
                try:
                    tokens = shlex.split(match.group(2), comments=True)
                except ValueError:
                    raise Failure("Existing API_SERVER_KEY cannot be parsed safely; use a literal token in .env.") from None
                existing = tokens[0] if len(tokens) == 1 else ""
                if existing and not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,512}", existing):
                    raise Failure("Existing API_SERVER_KEY is not a literal supported token. Resolve expressions manually; it will not be replaced.")
        else:
            preserved.append(line)
    if multiline_quote is not None:
        raise Failure("Existing .env contains an unterminated quoted value; correct it before API setup.")
    if not existing and yaml_may_supply_key(home / "config.yaml"):
        raise Failure("config.yaml may already supply an API Server key. Put that same literal key in API_SERVER_KEY in this home's .env, then rerun; no new key was generated.")
    if existing:
        common.validate_key(existing)
    elif require_existing:
        raise Failure("Save the API auth key in Dashboard first and confirm the selected profile/home. No existing key was found; no key was generated and configuration was not changed.")
    result = {"mode": "apply" if apply else "preview", "hermes_home": str(home), "bind": "127.0.0.1", "port": port, "key_file": str(key_path), "key_action": "reuse" if existing else "generate", "restart_required": True}
    result["yaml_enable_changes"] = yaml_changes
    if not apply:
        return result
    key = existing or secrets.token_urlsafe(32)
    content = "".join(preserved)
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"API_SERVER_ENABLED=true\nAPI_SERVER_HOST=127.0.0.1\nAPI_SERVER_PORT={port}\nAPI_SERVER_KEY={key}\n"
    desired_key = (key + "\n").encode()
    needs_env = not env_path.exists() or content != original or env_path.stat().st_mode & 0o077
    needs_key = not key_path.exists() or key_path.read_bytes() != desired_key or key_path.stat().st_mode & 0o077
    result["changed"] = bool(needs_env or needs_key or yaml_changes)
    result["restart_required"] = bool(needs_env or yaml_changes)
    with common.Transaction(home / ".openany-backups") as tx:
        if yaml_changes:
            tx.write(yaml_path, desired_yaml.encode(), 0o600)
        if needs_env:
            tx.write(env_path, content.encode(), 0o600)
        if needs_key:
            tx.write(key_path, desired_key, 0o600)
    result["backup"] = str(tx.directory) if tx.directory else None
    result["weak_existing_key"] = bool(existing and len(existing) < 32)
    return result
