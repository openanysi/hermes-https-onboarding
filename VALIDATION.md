# Local validation report

Version **0.3.1** · 2026-11-18 · regression and provider-side validation

## v0.3.1 Linux systemd correction

A provider-side installation exposed an error in the earlier `EnvironmentFile` rendering: surrounding quotes were treated as part of the pathname and systemd ignored the directive as non-absolute. The authenticated native API stayed healthy, while the installer correctly refused to proceed without loaded credentials. Both the Gateway drop-in and CLI unit now emit a literal absolute path, escaping percent specifiers without adding surrounding quotes. Timeout diagnostics distinguish missing credentials from API unavailability.

A second real-host issue reproduced Nginx reload timing: the first public request still reached the old Dashboard route (302), while a request one second later reached the new CLI route (401 without credentials, 200 with credentials). Routing verification now retries a bounded number of read-only checks before committing or restoring the saved configuration; all original authentication checks remain required.

Three real Linux `systemd-analyze verify` tests reproduce the old warning and accept the corrected Gateway and CLI units, including spaces, percent characters and UTF-8 paths. They create temporary unit files and do not register or start test services. Host warnings for unrelated installed units are excluded from each fixture's diagnostics. No OpenAny business code was changed.

## Results

| Check | Result |
| --- | --- |
| Python unit / CLI regression suite | 77 passed |
| Real Linux systemd parser regression | 3 passed |
| Existing real Nginx integration | 9 passed |
| New real shared-site Nginx integration | 4 passed |
| Installed published production CLI 0.3.12 | 4 integration tests passed |
| Installed published Dev CLI 0.1.5 | 4 integration tests passed |
| English manual command examples | 11 parsed |
| One-command installer examples | 3 parsed |
| Bundled Dashboard screenshots | 2 local PNG references verified |
| Bash / Python / JavaScript syntax | Checked locally |

All tests use private temporary files and fixture credentials. Nginx binds only to loopback on unprivileged ports and uses a temporary self-signed certificate trusted only by the test process. The local suites do not use system Nginx files, public model endpoints or wallets. The separate provider-side parser and connectivity checks use the explicitly authorized VPS and do not execute a model task.

## What changed and what was exercised

The installer now defaults to reusing a loaded HTTPS site. It identifies the selected exact hostname/TLS block, preserves the Dashboard and certificate directives, and inserts only the managed `/openany/` location. Unit tests cover quoted braces/comments, include inspection, existing-path conflicts, ambiguous domains, server-level redirects, edit races, idempotent patching and restoration after syntax or authenticated-verification failure.

The real shared-site Nginx tests verify the existing Dashboard response, Bearer authentication despite inherited Dashboard Basic authentication, status polling and exact task-body/header forwarding after prefix removal. A reproduced server-wide `proxy_intercept_errors` login-page rule initially swallowed API authentication failures; the managed API location now disables interception, and all four shared-site checks pass. Existing API, TLS, SSE, ACME fixture and routing tests still pass.

The new CLI setup configures a separate user service and private environment-specific files. Tests verify Node/capability checks, prod/Dev credential separation, existing-key preservation, private task/state permissions, service quoting, no secret in argv/unit/results, repeated setup, prevention of silent environment migration, JSON-only versus mandatory-file listing contracts, independent setup-job orchestration, preflight without new DNS/email inputs, and stopping before HTTPS when the collector fails.

`tests/cli_relay_integration.mjs` imports the actual installed published CLI modules without copying or modifying their code. Real loopback HTTP fixtures provide the Hermes and platform endpoints. The CLI's actual HTTP client, native Runs relay, artifact collector, stable snapshot, multipart uploader and receipt validator execute. Both published packages pass:

1. Authenticated task admission, automatic file upload, fixture buyer-authenticated download with byte comparison, rejected anonymous download, and exact request replay without another Hermes execution or upload.
2. Temporary upload failure followed by retry of the same frozen bytes, even after the original file changes; no extra Hermes execution.
3. A missing declared file never becomes `stored`, performs no upload, and expires.
4. The actual `seller hermes serve -j` command starts with an isolated HOME and seller-only fixture credentials, forwards authenticated discovery and exits cleanly on SIGTERM without printing secrets.

The upload destination is checked against each package's fixed production/Dev origin, then redirected **inside the test fixture's injected fetch** to a loopback server. Installed packages and deployed platform endpoints are not modified. Fixture download authorization is not evidence of OpenAny's real buyer-download authorization or OSS operation.

## Authorized provider production connectivity acceptance

On 2026-11-18 an explicitly authorized Ubuntu provider VPS completed production setup with Node.js 22.23.5 and published production CLI 0.3.12. The existing domain, certificate paths, Dashboard routes, API key and non-API environment assignments were preserved. Gateway and CLI process-environment checks confirmed credential loading; private environment files were mode 0600, the CLI user unit was active/enabled and user lingering was enabled.

The final installer returned `completed`. A separate repeat also completed with unchanged Gateway, CLI and Dashboard PIDs. An external client validated the real certificate chain and hostname: valid credentials returned 200 for capabilities/Skills, missing/wrong credentials returned 401, an unauthenticated task POST was rejected, an unknown run query returned 404, and the API namespace did not expose Dashboard configuration. Existing Dashboard login still worked. No authenticated task-creation request was sent; no Skill execution, actual file upload, buyer artifact download, remote Seller Key validity check or billing/settlement was tested.

These are connectivity/configuration results from one provider environment. They do not replace file-delivery or commercial acceptance, or prove behavior on every Linux distribution/custom proxy. No OpenAny frontend, backend, gateway or CLI business source was modified or deployed.

## Current compatibility contract

- Published npm metadata and installed help were checked: `openany-cli@0.3.12` / `openany`, `openany-cli-dev@0.1.5` / `openany-dev`, Node.js >=22. The installer verifies selected-environment help/capabilities before using an existing CLI; missing packages install privately with fixed versions and lifecycle scripts disabled.
- Read-only inspection of current OpenAny frontend, backend and gateway source confirms `http_json + poll`, recognition of an upstream path ending in `/v1/runs`, generic `fixed_body`, `auto_cli` task headers and the artifact-upload flow. No OpenAny business source was changed, built, deployed or published.
- The supported integration here uses `/v1/agent-api-tasks/{task_id}/artifacts`. No native poll task is converted into a result callback, and no `/result` request is sent by this installer.
- Setup enables local connectivity and credential loading; it does not publish products, set prices or submit verification tasks. Seller credential loading is distinct from remote Seller Key validation.

## Reproduce

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py'
python3 -B tests/check_docs.py
bash -n install.sh
NGINX_BINARY=/absolute/path/to/nginx python3 -B tests/nginx_integration.py
NGINX_BINARY=/absolute/path/to/nginx python3 -B tests/shared_nginx_integration.py
node tests/cli_relay_integration.mjs /absolute/path/to/node_modules/openany-cli
node tests/cli_relay_integration.mjs /absolute/path/to/node_modules/openany-cli-dev
python3 -B tests/systemd_environment_integration.py
```

The last four checks require permission to bind loopback sockets. The package contains no OpenAny CLI source or credentials. For the Node integration checks, install the corresponding published package normally and pass its package directory.

## Acceptance still required on a provider VPS

The macOS fixtures do not execute Linux services. The separate authorized VPS acceptance above covers real systemd setup, Gateway restart, an independent setup worker, private npm installation, service credential loading and repeat-run behavior. A full server reboot was not performed. A real Hermes version must advertise durable Runs idempotency, and its tools must write to the same filesystem/task directory the collector reads.

Existing public DNS/TLS reachability, certificate renewal under the site's existing manager, the new-site live ACME flow, target OpenAny runtime support, Seller Key validity, real Skill file contents, hosted buyer download and actual debit/settlement require separate acceptance. Byte completeness does not prove business-content correctness. No paid call or actual balance change is part of setup or connectivity verification. Provider-side configuration and acceptance are recorded separately from the local fixture suites.

Routing failures restore the selected site's saved bytes and attempt to reload the previous configuration. Backups do not uninstall npm/system packages, undo user lingering or service enablement, revoke certificates, reconcile active tasks or revert arbitrary operator edits. A CLI-service failure stops setup before routing changes, but previously enabled API/Gateway settings can remain; the private result and backups identify the stages to review. Nonstandard proxies, unsupported Nginx syntax, ambiguous routing or a mismatched delivery environment require operator review rather than automatic takeover.

Evidence is under `validation/`: Python, original/shared Nginx, production/Dev CLI, documentation, installer help and local environment logs.
