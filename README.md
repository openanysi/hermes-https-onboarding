# Connect an existing Hermes to OpenAny production

**v0.3.2** · Ubuntu/Debian with systemd · production poll onboarding. Publishing this package does not deploy an OpenAny website or configure a server.

The installer enables/reuses Hermes API and private keys, runs production `openany-cli@0.3.12`, and uses **async / http_json / poll**. Both HTTPS branches expose **`/openany/v1/runs`**. File tasks use CLI upload to `/artifacts`; this setup does not configure `/result` callbacks.

## Before setup

Ubuntu/Debian with systemd, an existing Hermes account/home and working model configuration, Node.js 22+ and authorized sudo are required. If Hermes is absent, use the [official installation guide](https://hermes-agent.nousresearch.com/docs/getting-started/installation) first. For a headless VPS, its official installer supports `--non-interactive --skip-browser --skip-computer-use`. Wait for the final install status, check `hermes --version` and `hermes doctor`, configure `hermes model`, and confirm Gateway readiness. The OpenAny installer never installs/upgrades Hermes. Do not restart an unfinished installer merely because SSH is slow.

## Missing Seller Key

Open [production Settings → Seller key](https://openany.si/seller-console/settings#seller-key) for the intended seller. Generate only when none exists; otherwise reuse your saved private copy. A masked key cannot be recovered from the page. Do not rotate automatically.

From an independent SSH terminal as the Hermes account, replace the sample account/home/path and run hidden input:

```bash
bash install.sh --save-seller-key --user YOUR_HERMES_USER \
  --home /absolute/path/to/hermes-home --environment prod \
  --seller-key-file /absolute/private/seller.key
```

It creates a private token-only file with mode 0600, refuses overwrite and starts no setup job. Give the Agent only the file path. Existing saved production credentials can be reused. Never paste keys into chat or command arguments.

## Choose one HTTPS branch

Download and verify the published installer:

```bash
curl --fail --location --silent --show-error \
  https://github.com/openanysi/hermes-https-onboarding/releases/download/v0.3.2/install.sh --output install.sh
curl --fail --location --silent --show-error \
  https://github.com/openanysi/hermes-https-onboarding/releases/download/v0.3.2/install.sh.sha256 --output install.sh.sha256
sha256sum --check install.sh.sha256
```

Stop on a failed download or checksum. The standalone installer fetches the same version's ZIP and verifies it before execution. Alternatively, verify and extract the complete release ZIP, then keep `install.sh` beside `scripts/`. Both use the published [release manifest](https://github.com/openanysi/hermes-https-onboarding/releases/download/v0.3.2/hermes-release.json). The package guide and manifest are usable independently of the OpenAny website; website updates are deployed separately.

**Reuse** an already valid HTTPS certificate and listener; preserve Dashboard and domain:

```bash
bash install.sh --check --user YOUR_HERMES_USER \
  --home /absolute/path/to/hermes-home --environment prod \
  --domain hermes.example.com --seller-key-file /absolute/private/seller.key
```

After successful preflight and approval, remove `--check` to apply. Missing or invalid TLS provides next steps; DNS alone is insufficient. With exactly one supported site, `--domain` may be omitted; select `--nginx-site` if ambiguous.

**New site** when no usable HTTPS site exists, after explicit approval:

```bash
bash install.sh --check --new-site --user YOUR_HERMES_USER \
  --home /absolute/path/to/hermes-home --environment prod \
  --domain hermes.example.com --public-ip 192.0.2.10 --email ops@example.com \
  --seller-key-file /absolute/private/seller.key
```

Use your actual domain/IP/email, repeat `--public-ip` for all A/AAAA addresses, and confirm inbound ports 80/443. After preflight and approval, remove `--check`. Existing hostname conflicts require review; the installer does not overwrite another site, change firewall/SSH, or switch branches automatically. Issuance and renewal use Certbot; a failed certificate request is not success.

Shared connection setup does **not** require `--skill`. Add `--skill YOUR_INSTALLED_SKILL` only for a specific configuration reference; add `--files` for file products. Keep `--port` consistent to preserve a custom API port. Reconcile YAML host/port overrides and keys privately rather than resetting configuration.

## Completion and repeat runs

`queued` means submitted, not completed. Save the exact returned job ID/status command and resume it after interruption:

```bash
bash install.sh --user YOUR_HERMES_USER --home /absolute/path/to/hermes-home \
  --status YOUR_SETUP_JOB_ID
```

Do not queue another job while the original is running. Valid keys, CLI configuration, task/state directories, routes and certificates are reused; healthy matching services skip restart/reconfiguration. First-time credential loading or changed credentials can require a restart. Verify authenticated discovery and anonymous rejection at the actual HTTPS `/openany/v1/capabilities` and `/openany/v1/skills`, including access from another host.

Use the HTTPS origin as connection base URL, `/openany/v1/runs` as creation path, and `async / http_json / poll`. Refer to the private `HERMES_HOME/.openany-provider/listing-reference.json`; each Skill keeps its own `fixed_body.instructions` and price. It is not an import or a published product.

See [the full guide](GUIDE.en.md) for preparation, key handling, branches, delivery and screenshots. Real model execution, production Seller Key acceptance, file upload, buyer downloads and billing require separate authorized tests. Stop after connection setup.

## Version source and release packaging

`scripts/release.py` is the version/CLI source. The bootstrap must agree; the package builder refuses a mismatch. Build without publishing:

```bash
python3 -B scripts/build_release.py --date 2026-11-18 --output /absolute/local/release-directory
```

It produces a deterministic ZIP, installer, SHA-256 files and `hermes-release.json` with the source release state and recorded validation scope. Building does not publish. After GitHub publication, refresh the website documentation with its `--refresh-release` command. Do not substitute v0.3.1 hashes for changed source, and do not claim server or platform acceptance from local tests.
