# OpenAny Hermes HTTPS Onboarding

Version 0.1.1 · 2026-11-17

Configure an **existing Hermes installation** on an Ubuntu/Debian VPS so OpenAny can call its native Runs API over HTTPS.

- **Provider documentation:** [English guide](GUIDE.en.md)
- **Verification evidence and limits:** [Validation report](VALIDATION.md)

Both guides include the supplied Hermes Dashboard screenshots, with steps for **Channels → API server → Configure → Save & Enable → Restart Gateway**. Keep the `assets/` directory beside the guide when sharing it; the provider ZIP includes those images for offline viewing.

After a Dashboard save, use `api-config --require-existing-key --apply` with the correct `--home` before the final Gateway restart. This exports the saved credential without generating a different one and synchronizes ordinary YAML API-disable flags. The illustrated guide distinguishes the Dashboard, SSH tunnel and working terminal so these foreground sessions do not block the next step.

The toolkit enables Hermes API Server, reuses or generates its Bearer key, configures an isolated Nginx virtual host, obtains a Let's Encrypt certificate, and configures automatic renewal. It also provides read-only checks and configuration backup/restore.

SSH is used to access the VPS. Nginx and TLS provide HTTPS. This toolkit does not change SSH daemon settings or require OpenAny to hold your SSH credentials.

## Download or clone

- [Latest release](https://github.com/openanysi/hermes-https-onboarding/releases/latest)
- [Download the v0.1.1 toolkit ZIP](https://github.com/openanysi/hermes-https-onboarding/releases/download/v0.1.1/openany-hermes-https-onboarding-v0.1.1.zip)
- [Download the ZIP SHA-256 checksum](https://github.com/openanysi/hermes-https-onboarding/releases/download/v0.1.1/openany-hermes-https-onboarding-v0.1.1.zip.sha256)

The release ZIP extracts into `hermes-https-onboarding/`, matching the directory names in the guide. Keep its `assets/` directory with the documentation for offline screenshots. GitHub's automatically generated source archives may use a version-suffixed directory name; rename it to `hermes-https-onboarding` or adjust the guide's paths.

To get the source directly:

```bash
git clone https://github.com/openanysi/hermes-https-onboarding.git
cd hermes-https-onboarding
```

## Start here

Read the English guide first. On the VPS, run configuration commands as the same Linux account that owns your Hermes home:

```bash
python3 -B scripts/openany_hermes.py --help
python3 -B scripts/openany_hermes.py api-config --home "$HOME/.hermes"
python3 -B scripts/openany_hermes.py api-config --home "$HOME/.hermes" --apply
```

Then start/restart the **existing Hermes Gateway** as described in the guide. API configuration does not automatically restart or install a Gateway service.

Preview is the default for `api-config`, `https`, and `restore`; changes require `--apply`. `check` and `verify` are read-only and never create model tasks. Public verification requires a valid, trusted HTTPS certificate; HTTP is permitted only for loopback checks.

Supported scope: Python 3.9+, Ubuntu/Debian with systemd, Nginx using `/etc/nginx/conf.d/*.conf`, distribution-packaged Certbot with `certbot.timer`, a directly reachable domain, and one Hermes API listener. Named-profile URL multiplexing, containers, CDN termination, DNS-provider automation and managed SSH operations require separate integration.

## Development checks

```bash
python3 -B -m unittest discover -s tests -v
NGINX_BINARY=/absolute/path/to/nginx python3 -B tests/nginx_integration.py
```

The second suite runs Nginx with a local HTTP fixture and temporary test certificate. It does not call a real Hermes instance, OpenAny or any model provider.

This package has no external Python dependencies. Its scripts are local tools; no production system is changed merely by downloading or unpacking the archive.
