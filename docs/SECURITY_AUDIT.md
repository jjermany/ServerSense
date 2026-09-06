# Security and performance audit - September 6, 2026


Scope: local ServerSense worktree, backend and frontend dependency inventories, production container build, authentication/API boundaries, SENSE tools and provider handling, settings/secrets/logging, collectors, telemetry queries, and persistence/network boundaries. This is a source review and automated validation, not a penetration test of the user's running Unraid server.

## Application findings and fixes

| Finding | Remediation | Evidence |
| --- | --- | --- |
| Concurrent first-run requests could create multiple administrators | Hash before acquiring a SQLite writer lock; recheck setup and insert atomically | Concurrent requests against an isolated database produce one 201, one 409, and one account |
| Login attempts could consume unbounded limiter storage and bypass a per-IP/username combination by rotating names or addresses | Independent account/source limits, expiring ordered key storage, hard key cap, credential length validation | Limiter saturation/expiry and rotating-name regression tests |
| Unknown usernames bypassed password hashing | Verify a dummy Argon2 hash on unknown-user requests | Shared verification path; case-insensitive login, invalid-user/password and session tests |
| Browser forms could submit bodyless mutations; production granted localhost development CORS access | Require `X-ServerSense-Request: 1` on every API mutation; use the existing Vite same-origin proxy | Missing-header mutation rejection, failed cross-origin preflight, authorized mutations and browser setup |
| API body reads and default validation errors were insufficiently bounded/redacted | 128 KiB and 15-second body limits; validation responses omit rejected inputs and context | Oversized-body and rejected-password tests |
| Shared fallback encryption key and secret-bearing HTTP client logs | Require an explicit secret; suppress HTTPX/httpcore info/debug and SQL bind values; deduplicate file handlers | Missing-secret and logging regression tests |
| Endpoint syntax accepted malformed hosts/ports or embedded credentials | Shared HTTP(S) validation; keep intentionally configured LAN endpoints available | Invalid endpoint and permitted LAN/query tests |
| External response bodies, streamed events and tool argument accumulation could grow without a hard cap | Bound synchronous responses to 8 MiB, model streams to 2 MiB, individual events to 256 KiB, visible text to 65,536 characters and tool arguments to 16 KiB; reject redirects | Stream flood, decompressed-response, decoding and redirect tests |
| Tool-result turns could exceed the original prompt budget or execute more tools than configured | Recheck serialized messages on every turn; mark truncated tool facts; enforce total unique tool-call limit and place current instructions after tool results | Provider integration tests check outgoing budget and message order |
| Database transactions extended across external calls | Finish read/normalization phases before model, notification and integration I/O; persist media only after all fetches succeed | Existing model transaction tests plus media fetch transaction assertions |
| Browser request timeout stopped after response headers | Await JSON body completion before releasing the abort timer | Delayed-response-body regression test |
| Expired sessions and duplicate file handlers accumulated | Delete expired sessions during daily cleanup; replace duplicate handlers | Code review and logging test |

## Performance review

The robust forecast previously constructed every pairwise slope for each window on repeated dashboard and alert evaluations. A 2,161-sample hourly window produced 2,333,880 slopes. The estimator now uses at most 256 evenly spaced samples, including endpoints, so at most 32,640 slopes are constructed; the latest 12 input results are cached. Confidence still uses the full sample count and time coverage. Dense-window estimates can differ from the full pairwise median; this is a deliberate bounded approximation.

A local synthetic steady-growth benchmark measured 0.917692 seconds for the previous full median, 0.011165 seconds for the bounded median, and 0.000003 seconds for a cache hit. Both estimated exactly 100,000 bytes/day. These figures describe this dataset and machine, not a production latency guarantee. Regression tests also cover dense declining/flat data, outliers, insufficient history and exhaustion-date overflow.

Forecast database queries now load only the required 30/90-day window relative to the newest compatible sample. Latest-sample queries use SQL LIMIT 1. Disk temperature history returns the latest 500 rows in chronological order, correcting the previous oldest-500 selection. Media updates query existing rows only for fetched external IDs instead of loading all retained integration history.

## Preserved boundaries

- ServerSense remains a single-administrator application. Sessions use random tokens, hashed database storage, HttpOnly/SameSite cookies, and expiration. Passwords remain case-sensitive; username capitalization is preserved.
- SENSE retains its read-only allowlist and normalized database-only telemetry access. No arbitrary commands, host filesystem tool, or Docker mutation was added.
- Collector hardware commands still use fixed argument arrays. The Docker socket remains in the collector boundary. Private LAN endpoints are an intentional administrator-configured feature, so blanket private-address blocking would break supported integrations.
- Docker startup still upgrades the schema through Alembic. This audit adds no schema migration. Existing database upgrade tests are part of the backend suite.
- Credentials remain encrypted under the installation key; saved credentials are preserved for blank inputs. No production credentials or live user data were used for the tests.

## Container remediation

The original Debian image retained 173 package findings (3 critical, 51 high, 56 medium, 57 low and 6 unknown) with no fixed versions listed for that distribution. Those findings are preserved as historical evidence in [SECURITY_IMAGE_FINDINGS.csv](SECURITY_IMAGE_FINDINGS.csv); they do not describe the final Alpine image.

The runtime now uses Python 3.12 on supported Alpine 3.24 and applies available distribution updates before installing SMART tools and timezone data. This replaces the affected Debian inventory, including its older SQLite, with current Alpine packages. Temporary compiler dependencies are removed after installation. Pip is upgraded before installing the application and removed afterward because its vendored packages were unnecessary runtime exposure. The fixed localhost health check uses Python, avoiding curl. No vulnerability ignore rules or severity filters are used.

The initial Alpine candidate exposed seven fixable findings in its inherited libuuid package. Applying package updates resolved all seven. The final image contains SQLite 3.53.4 and passed native-module checks for Argon2 password hashing, cryptography encryption/decryption, psutil, uvloop, UUID generation, timezone loading and smartctl execution. The existing /config volume layout, installation secret, Alembic startup and narrow hardware permissions remain unchanged. Production publishing targets linux/amd64; other architectures were not tested.

Final Trivy scan: **zero known vulnerabilities**, covering 41 Alpine packages and 42 Python packages. The full inventory and scanner output are retained in [SECURITY_IMAGE_SCAN.json](SECURITY_IMAGE_SCAN.json). Docker image ID: `sha256:ba9067d11df99da19c71131009c7c50995e14a2e572d77ee2b9207e2094dc779`. Scanned configuration digest: `sha256:452e2d0dac430370cdff8cca94514734726794938e86a0b35e8641e0e486f4ac`. Trivy scanned the exported production image without access to the Docker socket. A clean advisory scan is time-specific evidence, not a guarantee against unknown vulnerabilities; rebuild and rescan for updates.

## Compressed response expansion

HTTPX decoded compressed bodies before the previous length check, so a small compressed input could allocate an oversized expanded buffer. Synchronous HTTP requests and asynchronous model streams now consume raw wire bytes through a shared bounded decoder. Wire bytes and expanded bytes are independently capped, and zlib receives an explicit maximum output length. Gzip (including concatenated members), zlib-wrapped deflate, and identity encoding are supported; unsupported encoding stacks, corrupt data and incomplete compressed responses are rejected.

A regression test limits responses to 64 KiB, supplies a gzip payload that expands to 8 MiB, and verifies rejection with less than 1 MiB of traced allocation. Chunk-boundary tests feed one byte at a time through gzip/deflate/identity decoding.

## Validation

- All 129 backend tests passed in a disposable test image derived from the final Alpine runtime, including migration upgrades, permissions, forecasts, persistence, setup and security regressions.
- All 51 frontend tests across 15 files passed; ESLint and the production TypeScript/Vite build passed.
- Ruff lint/formatting and strict mypy passed; whitespace checks passed.
- The final production Dockerfile and Docker Compose build passed. Compose validation used an ephemeral build-only secret, without starting the application. Playwright's fresh-container setup/application test passed against the scanned image.
- Final runtime native-module and smartctl checks passed.
- npm audit reported zero advisories. The final Trivy scan reported zero OS or Python package findings at every severity.

## Completion and limits

The source audit, identified vulnerability remediations, performance changes, documentation and automated validation are complete locally. No production deployment or commit was performed. The live Unraid host, reverse proxy, TLS configuration, device passthrough and real external providers were not penetration-tested. Hardware collector access remains read-only and uses fixed command arguments; private LAN endpoints remain an intentional administrator-configured feature. No production credentials or user data were used for validation.

## References

- [OWASP authentication guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [OWASP CSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [Official Python container image](https://hub.docker.com/_/python)
- [Trivy image/archive scanning](https://trivy.dev/docs/dev/references/configuration/cli/trivy_image/)
