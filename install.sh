#!/usr/bin/env bash
set -euo pipefail
OPENANY_SETUP_VERSION=0.3.2
OPENANY_SETUP_STATE=published

for OPENANY_ARGUMENT in "$@"; do
    if [[ "$OPENANY_ARGUMENT" == --version ]]; then
        printf 'OpenAny Hermes one-click setup %s\n' "$OPENANY_SETUP_VERSION"
        exit 0
    fi
    if [[ "$OPENANY_ARGUMENT" == --help || "$OPENANY_ARGUMENT" == -h ]]; then
        cat <<'HELP'
OpenAny one-click setup for an existing Hermes on Ubuntu/Debian.
Hermes itself is not installed. Prepare it first using the official installation guide:
https://hermes-agent.nousresearch.com/docs/getting-started/installation
Verify model/Gateway readiness after non-interactive installation.
Node.js 22+ is required; the environment-specific CLI is reused or installed privately.

bash install.sh --domain YOUR_EXISTING_HERMES_DOMAIN \
  --environment prod --seller-key-file /private/seller.key

Reuse an existing valid HTTPS site (default); no new DNS or certificate is needed.
For a new site, add --new-site --public-ip YOUR_PUBLIC_IP --email YOUR_EMAIL.
Missing Seller Key: open https://openany.si/seller-console/settings#seller-key,
select the intended seller and save the token privately from an independent SSH terminal:
bash install.sh --save-seller-key --user YOUR_USER --home YOUR_HERMES_HOME \
  --seller-key-file /absolute/private/seller.key
Then rerun setup with only that file path; never paste the key into chat.

Options:
  --domain DOMAIN         Existing HTTPS hostname; auto-selected if exactly one exists.
  --nginx-site FILE       Select an existing loaded Nginx site when ambiguous.
  --cli-bin FILE          Existing openany/openany-dev executable; otherwise auto-detect.
  --node-bin FILE         Existing Node.js 22+ executable, if not discoverable.
  --skill SKILL           Generate a listing reference; repeat for multiple Skills.
  --files                 Generate auto_cli file contracts; default is JSON-only.
  --relay-port PORT       Private CLI port (prod 8790, Dev 8791).
  --new-site              Only for a new site; requires domain, public-ip and email.
  --seller-key-file FILE    Private 0600 file; never put a key in command arguments.
                           Existing selected-environment configuration is reused.
  --save-seller-key        Hidden input in an independent terminal; refuses overwrite.
  --environment prod|dev   Seller credential environment (default: prod).
  --user USER --home PATH  Override the detected Hermes account/home.
  --hermes-bin PATH        Existing Hermes executable, if not discoverable.
  --gateway-scope SCOPE   auto (default), user or system.
  --port PORT             Loopback API port (default: 8642).
  --check                 Preflight only; no system configuration changes.
  --wait                  Wait for the independent job (use from SSH).
  --status [JOB_ID]        Read latest or selected job result.

The job runs outside Gateway and may restart its matching service. Seller Keys
are provided by OpenAny and cannot be generated here. No model task is run.
HELP
        exit 0
    fi
done

[[ "$(uname -s)" == Linux && -f /etc/os-release && -d /run/systemd/system ]] || {
    echo 'Ubuntu/Debian with systemd is required.' >&2; exit 1;
}
OPENANY_DISTRO_ID=$(. /etc/os-release; printf '%s' "$ID")
[[ "$OPENANY_DISTRO_ID" == ubuntu || "$OPENANY_DISTRO_ID" == debian ]] || {
    echo 'Only Ubuntu and Debian are supported.' >&2; exit 1;
}
OPENANY_CHECK=0
OPENANY_HAS_HOME=0
OPENANY_HAS_BIN=0
OPENANY_HAS_PATH=0
OPENANY_STATUS=0
for OPENANY_ARGUMENT in "$@"; do
    case "$OPENANY_ARGUMENT" in
        --check|--dry-run|--save-seller-key) OPENANY_CHECK=1 ;;
        --home|--home=*) OPENANY_HAS_HOME=1 ;;
        --hermes-bin|--hermes-bin=*) OPENANY_HAS_BIN=1 ;;
        --user-path|--user-path=*) OPENANY_HAS_PATH=1 ;;
        --status) OPENANY_STATUS=1 ;;
    esac
done
if [[ $EUID -ne 0 && "$OPENANY_CHECK" == 0 ]]; then
    OPENANY_EXTRA=()
    if [[ "$OPENANY_HAS_HOME" == 0 && -n "${HERMES_HOME:-}" ]]; then
        OPENANY_EXTRA+=(--home "$HERMES_HOME")
    fi
    if [[ "$OPENANY_HAS_BIN" == 0 ]]; then
        OPENANY_HERMES_BIN=$(command -v hermes || true)
        if [[ "$OPENANY_HERMES_BIN" == /* && -x "$OPENANY_HERMES_BIN" ]]; then
            OPENANY_EXTRA+=(--hermes-bin "$OPENANY_HERMES_BIN")
        fi
    fi
    if [[ "$OPENANY_HAS_PATH" == 0 ]]; then
        OPENANY_EXTRA+=(--user-path "$PATH")
    fi
    exec sudo -n bash "$0" "$@" "${OPENANY_EXTRA[@]}"
fi
OPENANY_MISSING=()
for OPENANY_TOOL in python3 curl; do
    command -v "$OPENANY_TOOL" >/dev/null || OPENANY_MISSING+=("$OPENANY_TOOL")
done
if [[ ${#OPENANY_MISSING[@]} -gt 0 ]]; then
    if [[ $EUID -ne 0 || "$OPENANY_CHECK" == 1 ]]; then
        echo 'python3 and curl are required. Install them before --check.' >&2; exit 1
    fi
    apt-get update
    apt-get install -y "${OPENANY_MISSING[@]}"
fi
/usr/bin/python3 -c 'import sys; assert sys.version_info >= (3,9), "System Python 3.9+ is required"'
OPENANY_SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
if [[ -f "$OPENANY_SCRIPT_DIR/scripts/setup.py" ]]; then
    exec /usr/bin/python3 -B "$OPENANY_SCRIPT_DIR/scripts/setup.py" "$@"
fi
OPENANY_BUNDLE_POINTER=/var/lib/openany-hermes/setup/current-bundle
if [[ "$OPENANY_STATUS" == 1 && $EUID -eq 0 && -f "$OPENANY_BUNDLE_POINTER" && ! -L "$OPENANY_BUNDLE_POINTER" && -O "$OPENANY_BUNDLE_POINTER" ]]; then
    IFS= read -r OPENANY_INSTALLED_BUNDLE < "$OPENANY_BUNDLE_POINTER"
    if [[ "$OPENANY_INSTALLED_BUNDLE" == /opt/openany-hermes-onboarding/"$OPENANY_SETUP_VERSION"-* && -f "$OPENANY_INSTALLED_BUNDLE/scripts/setup.py" ]]; then
        exec /usr/bin/python3 -B "$OPENANY_INSTALLED_BUNDLE/scripts/setup.py" "$@"
    fi
fi
if [[ "$OPENANY_SETUP_STATE" == draft ]]; then
    echo 'Unpublished local draft: use install.sh from the extracted local ZIP beside its scripts/ directory. No released bundle was downloaded.' >&2
    exit 1
fi

OPENANY_TEMP=$(mktemp -d /tmp/openany-hermes-setup.XXXXXXXX)
trap 'rm -rf -- "$OPENANY_TEMP"' EXIT
OPENANY_ARCHIVE="openany-hermes-https-onboarding-v${OPENANY_SETUP_VERSION}.zip"
OPENANY_RELEASE="https://github.com/openanysi/hermes-https-onboarding/releases/download/v${OPENANY_SETUP_VERSION}"
curl --fail --location --silent --show-error --max-time 60 "$OPENANY_RELEASE/$OPENANY_ARCHIVE" --output "$OPENANY_TEMP/$OPENANY_ARCHIVE"
curl --fail --location --silent --show-error --max-time 60 "$OPENANY_RELEASE/$OPENANY_ARCHIVE.sha256" --output "$OPENANY_TEMP/$OPENANY_ARCHIVE.sha256"
python3 -B - "$OPENANY_TEMP/$OPENANY_ARCHIVE" "$OPENANY_TEMP" <<'PY'
from pathlib import Path, PurePosixPath
import hashlib, re, stat, sys, zipfile
archive=Path(sys.argv[1]); destination=Path(sys.argv[2])
parts=archive.with_suffix('.zip.sha256').read_text().split()
if len(parts)!=2 or not re.fullmatch('[a-f0-9]{64}',parts[0]) or parts[1]!=archive.name or hashlib.sha256(archive.read_bytes()).hexdigest()!=parts[0]:
    raise SystemExit('Release checksum validation failed; nothing was executed.')
with zipfile.ZipFile(archive) as release:
    for member in release.infolist():
        path=PurePosixPath(member.filename)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='hermes-https-onboarding' or stat.S_ISLNK(member.external_attr >> 16):
            raise SystemExit('Unsafe release member; nothing was executed.')
    release.extractall(destination)
PY
python3 -B "$OPENANY_TEMP/hermes-https-onboarding/scripts/setup.py" "$@"
