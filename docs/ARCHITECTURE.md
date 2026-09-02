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

Interactive requests first pass through a deterministic intent router. High-confidence current telemetry and status questions execute a narrow service directly and persist a `serversense` response with structured references; action requests receive a read-only refusal. Historical synthesis, explanation, troubleshooting, recommendations, and general conversation become persistent `AIJob` rows. A dispatcher runs one model job at a time by default, and each job snapshots its provider, model, endpoint, and bounded options without copying credentials. Changing settings therefore affects new jobs only. The browser streams by polling durable job state, so disconnecting or navigating does not own or cancel provider work. The background threshold is presentation-only; a separate hard runtime bounds execution and closes the provider stream on expiry. Explicit cancellation is authenticated and scoped to the job owner. Process restart marks stale in-flight streams interrupted and preserves partial output; queued work is not silently discarded or duplicated.

Provider context is bounded before transmission: a persisted rolling conversation summary, recent messages within the active window, prior structured entity references, and request-relevant normalized telemetry all share one enforced character budget. Native tool-capable providers retain the allowlisted loop. In auto mode, a provider rejection of tool fields triggers one retry using the already gathered context without tool schemas. Output, telemetry serialization, queue depth, hard runtime, provider timeouts, and tool loops remain bounded. Successful output becomes a normal assistant message; cancellation, timeout, interruption, and failure preserve generated text as a labeled incomplete assistant entry. Notifications are emitted only for long-running completions or important terminal outcomes, are deduplicated on the job row, and honor global and per-job preferences. Configurable daily retention deletes old conversations together with messages, jobs, notifications, and tool-call records.

Sonarr and Radarr are configured as independently named integration rows, allowing multiple instances of either provider. API keys are encrypted and never returned by the API. When AI is enabled, a five-minute bounded poll reads only fixed v3 status, history, and calendar endpoints with redirects disabled. The collector retains normalized event type, title, series/episode identity, quality, known size, provider upgrade reason, upcoming monitored date, and source instance; raw payloads and filesystem paths are discarded. Quality replacements use an explicit provider deletion reason of `Upgrade` as authoritative evidence and pair it with a nearby import when available. Calendar data is represented as upcoming monitored air/release eligibility, never as a guaranteed download. Each instance fails independently, history deduplication is idempotent, and calendar rows are replaced as a bounded snapshot. SENSE receives only these normalized records and a warning that gross import sizes do not establish net storage causation.

Optional AI dashboard summaries are additive cached events, never replacements for deterministic dashboard insights. A dedicated background loop—not the monitoring loop or dashboard route—builds a bounded snapshot from forecasts, current resources, active alerts, disk/container state, and seven-day normalized media aggregates. Refreshes are limited to six-hour cadence or meaningful alert/media changes after a 15-minute floor. Calls expose no tools, return at most 240 tokens, time out within 90 seconds, retain the last successful result on failure, and are hidden after 12 hours.

Password hashes use Argon2. Session cookies are HttpOnly and SameSite=Strict. Enable secure cookies when serving behind HTTPS. Provider keys are encrypted with Fernet using a key derived from the installation secret; losing or changing that secret makes stored provider keys unreadable.

The Docker socket is mounted read-only, but access to the socket is intrinsically sensitive. Only the collector uses it for inventory. SENSE never receives the client or socket path.

Private Unraid WebGUI files stay inside the collector boundary. The collector groups user-named pool devices into a version-tolerant normalized record; authenticated APIs and SENSE receive only that record. Network receive/send rates are calculated in one deterministic service from the latest two persisted counters. Missing samples, counter resets, and non-positive intervals produce an unavailable rate.

## Forecasting

The model never computes capacity exhaustion. ServerSense takes the median of all pairwise slopes inside 7, 30, and 90-day windows (a Theil–Sen-style robust trend), then divides current free bytes by positive consumption rate. Short history is marked insufficient and coverage/sample count determine confidence.

## Persistence and upgrades

All persistent data is below `/config`. Container startup runs `alembic upgrade head` before the API. Metrics and Docker samples follow configured retention; hourly storage history is kept longer for capacity intelligence.
