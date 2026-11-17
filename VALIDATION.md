# Local validation report

Release candidate: 0.2.0 · 2026-11-17

## Verified locally

The toolkit was developed in an isolated new directory on macOS. No existing OpenAny business project or server configuration was modified. Tests use temporary directories, placeholder fixture credentials and a loopback-only mock API.

| Check | Result |
| --- | --- |
| Unit/CLI regression suite | 55 tests passed |
| Real Nginx integration suite | 9 tests passed |
| Packaged guide command examples | Parsed against the actual CLI; see the documentation-check log |
| Generated bootstrap, TLS and IPv6 site variants | Passed real Nginx syntax validation |
| Provider release contents | English documentation and tools; Chinese internal guide excluded |
| Illustrated setup instructions | Both supplied v0.23.1 screenshots embedded in each guide; local PNG references and bundled image bytes checked |

The unit suite covers preview without writes, reuse of existing credentials, preservation of model-provider settings, duplicate assignment cleanup, private file permissions, rejection of symlinks/interpolated keys, YAML-key protection, domain/port validation, dual-stack DNS mismatch, existing-site conflict, exclusive locking, partial-write recovery, certificate/TLS validation failure recovery, Nginx validation failure recovery, backup target restrictions, discovery schemas, and an actual CLI API-configuration/restore round trip.

Version 0.1.1 adds Dashboard disable-flag synchronization in all three supported block-style YAML locations, refusal to invent a key during a Dashboard handoff, early rejection of short/placeholder keys, preservation of multiline non-API dotenv values, rejection of ambiguous YAML mappings/anchors, preservation of a model alias during key synchronization, recovery of YAML and key settings together, and public ACME-directory permissions under a restrictive umask.

## Version 0.2.0 one-command setup

The new `install.sh` entry point targets an existing Hermes installation only. It downloads and verifies the fixed release when run standalone, and submits an independent systemd job. The worker composes the existing API and HTTPS tools, preserves private API keys, configures an environment-specific OpenAny Seller Key, binds its private environment file to the selected Gateway unit, and records progress outside the initiating Gateway.

The 21 additional tests cover:

- Prod/Dev Seller Key isolation, literal metacharacters, private file permissions, task-root permissions, preserved Hermes/model settings and repeated credential setup.
- Rejection of missing/private-file misuse without generating a Seller Key or modifying API settings.
- Matching Gateway units by home/account and refusal of ambiguous units.
- Stripped user command environments, secret transmission by stdin rather than argv, safe drop-in quoting, and matching selected loaded-process credentials.
- Independent job command construction, ordered stages, verified skip paths, stale runtime fingerprints, stopped-on-failure behavior, installer help and CLI parsing.
- UTF-8 systemd environment paths without JSON-style Unicode escapes that systemd would interpret differently.
- Runtime-bundle readability under a private umask and persistent boot configuration for an existing user Gateway.

Systemd commands, service discovery/restarts and `/proc` credential matching are tested with fixtures or mocked command boundaries. No actual root installer, systemd transient service or live Gateway restart was performed on the macOS test host. The independent-job design is based on [systemd-run service semantics](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html) and [Hermes service management](https://hermes-agent.nousresearch.com/docs/user-guide/messaging); real VPS restart survival still needs acceptance. Bootstrap Bash syntax and its no-side-effect help path are checked locally.

Seller Key handling follows the OpenAny CLI's separate `OPENANY_SELLER_KEY` / `OPENANY_DEV_SELLER_KEY` contract. The installer reports `remote_verified: false`; it does not perform a real callback, artifact upload, purchase, model run or settlement test, and it does not install unpublished CLI/automatic-delivery features.

The integration suite runs **real Nginx 1.26.3**, built from the official source archive, against a local Python HTTP fixture and a temporary self-signed test certificate. The test process trusts that certificate only for the local test. It verifies:

- Accepted authenticated discovery and rejected invalid/absent credentials.
- Native request paths, query strings, JSON body, Authorization, Idempotency-Key, Content-Type and session headers reach the fixture unchanged.
- An SSE first frame reaches the client before the fixture is allowed to complete.
- Non-allowlisted routes do not reach the upstream.
- The ACME webroot serves its token and other HTTP routes redirect.
- An untrusted TLS certificate is rejected.
- A Dashboard-shaped saved configuration passes through the actual CLI's `--require-existing-key` synchronization, a simulated API reload and local HTTP/real Nginx HTTPS verification. A later saved key change fails with the stale key file and succeeds after resynchronization. The fixture models saved-state loading; it is not a real Hermes Gateway or browser session.

## Reproduce

From the toolkit directory:

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tests/check_docs.py
NGINX_BINARY=/absolute/path/to/nginx python3 -B tests/nginx_integration.py
```

Use a local Nginx executable with TLS and PCRE support. The integration suite binds only to loopback on temporary unprivileged ports and requires permission to create local listening sockets. It does not require system Nginx configuration or root. `openssl` must support `req -addext`.

## Not validated by these tests

This is a **local-tested release candidate, not a production VPS acceptance result**. The following still require an authorized pilot on the provider's own supported VPS:

- Ubuntu/Debian package installation and actual systemd service lifecycle.
- Real public DNS, cloud firewall rules, inbound reachability and IPv6 routing.
- A publicly trusted certificate for an actual domain, CA registration and live renewal/deploy-hook execution.
- A real Hermes Gateway loading the configuration, consuming its Seller Key, surviving the new installer worker's restart and executing the intended Skill.
- OpenAny environment-specific form persistence, Hermes async adapter deployment, hosted-file delivery and buyer billing.

Mocked certificate failures exercise file recovery; they are not evidence of live ACME issuance. The local API fixture is not a Hermes installation. No real model task, purchase, balance change or production deployment was performed.

Configuration rollback restores managed files and reloads Nginx when possible. It does not uninstall packages, undo service enablement, deregister CA accounts, revoke certificates or roll back arbitrary operator edits. Keep the reported private backup and your existing SSH access available during a real installation.

The toolkit relies on standard Nginx `conf.d` inclusion and distribution Certbot timer paths. Snap/custom Certbot, containers, CDN termination and multiplexed profile URL prefixes are intentionally rejected or left for manual integration.

## Illustrated guide update

The documentation now includes the two supplied Dashboard screenshots and the complete GUI sequence: open Channels, configure API server, set enable/key/port/host, Save & Enable, synchronize the toolkit key file, restart Gateway and inspect the channel state. It also explains loopback Dashboard access through SSH forwarding. The provider archive contains the original PNGs in `assets/`, so image viewing does not depend on an external image host.

The earlier screenshot revision changed documentation only. The subsequent 0.1.1 review corrected runtime handoff issues and re-ran all tests, with the counts reported above. The screenshots remain user-supplied reference evidence, not proof that we operated a live Dashboard or performed a VPS deployment.

## Configuration-contract review

The current upstream configuration loader was inspected to confirm that an explicit YAML platform disable takes precedence over automatic environment-based enablement, and that API keys must meet the adapter's usable-secret guard. This is source-contract evidence, not a live v0.23.1 run. The screenshots depict v0.23.1; the inspected upstream source is the current main branch and may evolve. References: [environment enablement gate](https://github.com/NousResearch/hermes-agent/blob/main/gateway/config_env.py), [API usable-key check](https://github.com/NousResearch/hermes-agent/blob/main/gateway/config.py), [secret validation](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/auth.py), [Dashboard configuration metadata](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/web_server_messaging.py).

## Evidence files

The source directory contains `validation/unit-tests.txt`, `validation/nginx-integration.txt`, `validation/documentation.txt`, and `validation/environment.txt`. They record the final local runs without any real credentials.

The official API documentation's Skill example is an array; current Hermes source returns a list envelope. Both formats are accepted. Sources: [API guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server), [official API source](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/api_server.py), [official Nginx archive](https://nginx.org/download/nginx-1.26.3.tar.gz).
