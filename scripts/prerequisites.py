"""Actionable prerequisite errors. Never include a credential value."""
from pathlib import Path
import shlex
import common

INSTALL_GUIDE = "https://hermes-agent.nousresearch.com/docs/getting-started/installation"
SELLER_SETTINGS = "https://openany.si/seller-console/settings#seller-key"


class Required(common.Failure):
    def __init__(self, code, message, next_steps):
        self.code, self.next_steps = code, next_steps
        super().__init__(message + " Next: " + " ".join(next_steps))


def hermes_required():
    return Required("HERMES_REQUIRED", "An existing, working Hermes installation is required.", [
        "Read " + INSTALL_GUIDE + ". Obtain approval before installing Hermes separately as its intended Linux account.",
        "On a headless VPS, download the official installer and use --non-interactive --skip-browser --skip-computer-use; wait for its final exit status. Do not start another installer while it is running.",
        "Verify the installed wrapper with hermes --version and hermes doctor. Configure the model with hermes model; a non-interactive install does not complete model setup.",
        "Confirm the account, HERMES_HOME/profile and Gateway state, then rerun this installer. A launcher file or DNS record alone does not prove readiness.",
    ])


def seller_required(environment="prod", user=None):
    origin = "https://dev.openany.si" if environment == "dev" else "https://openany.si"
    settings = origin + "/seller-console/settings#seller-key"
    command = "bash install.sh --save-seller-key --environment " + environment
    if user:
        command += " --user " + shlex.quote(user["username"]) + " --home " + shlex.quote(user["home"])
    command += " --seller-key-file /ABSOLUTE/PRIVATE/seller.key"
    return Required("SELLER_KEY_REQUIRED", "The selected environment's Seller Key is missing.", [
        "Open " + settings + " and select the intended seller. Generate only if no key exists; otherwise recover your saved private copy. Do not rotate an existing key automatically.",
        "In an independent SSH terminal as the Hermes account, use hidden input: " + command + ". Replace the private absolute file path. The command refuses to overwrite an existing file.",
        "Give the Agent only the saved absolute path, then rerun the same command with --seller-key-file /ABSOLUTE/PRIVATE/seller.key. Never paste the key into chat, argv or logs.",
    ])


def https_required():
    return Required("HTTPS_BRANCH_REQUIRED", "No usable existing HTTPS site was confirmed.", [
        "For reuse, restore the intended site's certificate/listener and confirm a valid HTTPS handshake, then rerun with --domain and optionally --nginx-site.",
        "For a new site, obtain approval and rerun with --new-site --domain YOUR_DOMAIN --public-ip YOUR_PUBLIC_IP --email YOUR_CERTIFICATE_EMAIL. DNS must match all declared A/AAAA addresses; inbound 80/443 must be reachable.",
        "An existing conflicting HTTP/TLS server_name requires reviewing that site first. Do not delete it or switch branches automatically. DNS resolution alone is not HTTPS readiness.",
    ])
