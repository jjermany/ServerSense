# Architecture and security boundaries

ServerSense is a single-container modular monolith. The browser calls authenticated FastAPI routes; route handlers use typed services and repositories backed by SQLite. A scheduled collector normalizes Linux, Unraid pool/disk, SMART, Docker, and cumulative network observations into persistent samples. Deterministic telemetry, forecast, and alert services consume those samples.

```text
Browser → authenticated API → services → SQLite under /config
                                  ↑
Unraid/Linux/SMART/Docker → restricted collectors

Sonarr/Radarr read-only APIs → bounded normalizer → media activity records

SENSE provider ↔ orchestrator → allowlisted read-only tools → normalized database data

deterministic alerts → optional one-shot SENSE explanation → persisted event → dashboard
```

## Trust boundaries

Container names, images, SMART text, media metadata, alert messages, and integration responses are untrusted. SENSE’s system policy tells the model to treat them as data. More importantly, application policy is enforced in code: only names registered in `TOOLS` can execute, and those handlers query normalized records. There is no shell tool.

Proactive explanations preserve the deterministic-first boundary. A configured administrator must opt in; each new alert batch produces at most one model request, the request exposes no tools, and the model output is stored as a separate event with provider/model provenance. Provider failures are logged only by exception type and do not interrupt telemetry collection, deterministic alert persistence, or notification delivery.

Interactive SENSE requests use asynchronous provider streams. Each authenticated request can cancel only its own active generation; cancellation closes the provider stream and does not persist a partial exchange. Model output and tool loops are bounded. Only the six most recent messages from a conversation updated within the last 30 minutes are returned to the provider, and daily cleanup removes conversations older than 30 days.

Sonarr and Radarr are configured as independently named integration rows, allowing multiple instances of either provider. API keys are encrypted and never returned by the API. When AI is enabled, a five-minute bounded poll reads only v3 status/history endpoints with redirects disabled. The collector retains normalized event type, title, series/episode identity, quality, known size, explicit upgrade evidence, and source instance; raw payloads and filesystem paths are discarded. Each instance fails independently, and `(integration_id, external_id)` deduplication makes repeated polls idempotent. SENSE receives only these normalized rows and a warning that gross import sizes do not establish net storage causation.

Password hashes use Argon2. Session cookies are HttpOnly and SameSite=Strict. Enable secure cookies when serving behind HTTPS. Provider keys are encrypted with Fernet using a key derived from the installation secret; losing or changing that secret makes stored provider keys unreadable.

The Docker socket is mounted read-only, but access to the socket is intrinsically sensitive. Only the collector uses it for inventory. SENSE never receives the client or socket path.

Private Unraid WebGUI files stay inside the collector boundary. The collector groups user-named pool devices into a version-tolerant normalized record; authenticated APIs and SENSE receive only that record. Network receive/send rates are calculated in one deterministic service from the latest two persisted counters. Missing samples, counter resets, and non-positive intervals produce an unavailable rate.

## Forecasting

The model never computes capacity exhaustion. ServerSense takes the median of all pairwise slopes inside 7, 30, and 90-day windows (a Theil–Sen-style robust trend), then divides current free bytes by positive consumption rate. Short history is marked insufficient and coverage/sample count determine confidence.

## Persistence and upgrades

All persistent data is below `/config`. Container startup runs `alembic upgrade head` before the API. Metrics and Docker samples follow configured retention; hourly storage history is kept longer for capacity intelligence.
