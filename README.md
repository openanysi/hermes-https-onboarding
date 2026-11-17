# OpenAny Hermes HTTPS — one-command setup

Version **0.2.0** · Ubuntu/Debian · existing Hermes required

Configure native Hermes API access, its private key, the OpenAny Seller Key, Nginx HTTPS and certificate renewal with one installer. Existing valid configuration is verified and skipped. Hermes itself is never installed.

## Run

Download [install.sh](https://github.com/openanysi/hermes-https-onboarding/releases/download/v0.2.0/install.sh), then run it on the VPS as the Hermes account:

```bash
curl --fail --location --silent --show-error \
  'https://github.com/openanysi/hermes-https-onboarding/releases/download/v0.2.0/install.sh' \
  --output install.sh
bash install.sh \
  --domain hermes.example.com \
  --public-ip 203.0.113.10 \
  --email ops@example.com \
  --environment prod \
  --seller-key-file /absolute/private/seller.key
```

Replace the example domain, IP, email and path. Use `--environment dev` for a Dev Seller Key. Include every intended A/AAAA address using repeated `--public-ip`. DNS and cloud firewall rules must already allow the domain and ports 80/443.

The Seller Key file must contain only the real token and have mode `0600`. The installer can reuse its private selected-environment configuration or an existing selected Seller Key in the Hermes home's `.env`; omit `--seller-key-file` in that case. From an interactive SSH terminal, missing credentials can be entered at a hidden prompt. From Hermes, provide a private file or existing configuration; never paste a key into chat or a command argument.

Root privileges are required for Nginx/systemd. The script uses `sudo -n`; if it is unavailable, run the same command with `sudo` from an authorized SSH session. A direct root invocation needs `--user YOUR_HERMES_USER`. Custom homes/executables can be provided with `--home` and `--hermes-bin`. The one-command worker requires the distribution `/usr/bin/python3` to be version 3.9 or later.

## Check the result

```bash
bash install.sh --status
```

Setup runs in an independent systemd service, outside Gateway's process group. It can restart the matching Gateway and continue even if the initiating Hermes session disconnects. The initial `queued` response is a receipt, not completion. The installer prints an exact status command and private result-file path; `--status` reports `completed` or `failed`. From SSH you may add `--wait` to the setup command. Use `--check` for a preflight without system configuration changes.

The result includes the HTTPS origin, `/v1/runs`, discovery addresses, private credential locations, backups, and external-verification status. No model task, file upload or buyer billing test is started automatically.

## Credentials

- Hermes `API_SERVER_KEY`: reused or generated, stored in `HERMES_HOME/openany-api.key`.
- OpenAny Seller Key: supplied by OpenAny, never generated here. Production uses `OPENANY_SELLER_KEY`; Dev uses `OPENANY_DEV_SELLER_KEY`.
- The selected Seller Key and `OPENANY_TASK_ROOT` are stored in `HERMES_HOME/.openany-provider/prod.env` or `dev.env`, mode `0600`, and loaded by the selected Gateway through a managed `EnvironmentFile` drop-in. The worker checks the selected service's loaded process environment without printing it.

These keys are distinct from model-provider keys and buyer API keys. Seller credential loading is checked locally; its remote validity, automatic file delivery and settlement are not claimed or tested by this installer. Existing OpenAny upload/callback tooling still needs to be available for those business features.

## Downloads and details

- [Latest release](https://github.com/openanysi/hermes-https-onboarding/releases/latest)
- [v0.2.0 ZIP](https://github.com/openanysi/hermes-https-onboarding/releases/download/v0.2.0/openany-hermes-https-onboarding-v0.2.0.zip)
- [ZIP SHA-256](https://github.com/openanysi/hermes-https-onboarding/releases/download/v0.2.0/openany-hermes-https-onboarding-v0.2.0.zip.sha256)
- [Advanced manual guide with screenshots](GUIDE.en.md)
- [Validation evidence and limitations](VALIDATION.md)

The standalone installer downloads and verifies its fixed release ZIP; the ZIP can also be unpacked and its bundled `install.sh` run locally. You can obtain the source directly:

```bash
git clone https://github.com/openanysi/hermes-https-onboarding.git
cd hermes-https-onboarding
```

User configuration is applied as the Hermes Linux account. System changes are limited to the selected Gateway, managed Nginx site, certificate tooling and required dependencies. Unknown/ambiguous services and conflicting sites cause an error rather than takeover. Existing plain user/system units named `hermes-gateway*.service` are detected by their pinned home; unmanaged listeners and complex service layouts require manual integration. The selected Gateway is enabled at boot; user services also have lingering enabled so they keep running after logout. Gateway restarts can interrupt in-flight work; the separate installer continues and records progress.

This is local-tested software, not a real VPS acceptance result. Actual systemd execution/restart survival, public certificate issuance/renewal and OpenAny's external reachability still need provider-side acceptance. The script does not change SSH, DNS, cloud firewall rules, model-provider settings or product prices.

## Tests

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tests/check_docs.py
NGINX_BINARY=/absolute/path/to/nginx python3 -B tests/nginx_integration.py
```
