# ServerSense v1 living checklist

Last updated: 2026-09-02

## Foundation

- [x] FastAPI modular backend and OpenAPI routes
- [x] React strict-TypeScript frontend with responsive dark UI
- [x] SQLite data model for required core entities
- [x] Alembic initial migration
- [x] local administrator, Argon2 password hashing, cookie session, logout
- [x] encrypted AI credential storage
- [x] realistic isolated demo mode
- [x] multi-stage single-container Docker build and Compose
- [x] persistent `/config` layout
- [x] guided welcome, administrator, monitoring mode, and optional-AI setup flow
- [x] live-mode first-launch default with a browser-verified, non-bypassable monitoring selection step
- [x] functional Monitoring and Integrations settings navigation with mode status and webhook controls
- [x] consistent full-width responsive Settings layout with grouped AI controls, sticky section navigation, and inline summary timing guidance

## Monitoring and intelligence

- [x] Linux system and capacity collector abstraction
- [x] Unraid detection and `disks.ini` adapter
- [x] fixed-argument JSON `smartctl` collection
- [x] non-privileged SMART device rules/SYS_RAWIO, SAT retry, and JSON field fallbacks
- [x] conservative known-vendor manufacturer normalization with unknown fallback
- [x] restricted Docker inventory collector
- [x] scheduled collection and retention cleanup
- [x] deterministic 7/30/90-day robust forecasting
- [x] dashboard with timezone-aware overall/source update times, storage chart/ranges, disk cards, Docker table
- [x] metric-driven storage, forecast, SMART, temperature, and container rules with a 10-minute stopped-container grace period
- [x] Unraid array/parity state, disk/cache inventory, and available APC UPS data
- [x] combined array capacity from assigned data disks only, with explicit SENSE scope and incompatible legacy-source isolation
- [x] persisted per-container CPU, memory, health, restarts, and uptime
- [x] configurable alert thresholds, persistent acknowledgement/dismissal, and per-category encrypted generic webhook, Discord, Pushover, and SMTP email delivery
- [x] daily retention cleanup and old storage sample downsampling
- [x] normalized Unraid pool detail and reset-safe calculated network transfer rates
- [x] authenticated visible-viewer lease with 5-second CPU/network sampling, visibility-aware refresh across monitoring pages, source sample timestamps, and independent Docker, SMART, and storage cadences
- [x] container `TZ` precedence with validated Settings fallback and shared timezone-aware frontend formatting

## SENSE

- [x] read-only allowlisted structured tool registry
- [x] built-in deterministic no-model mode
- [x] OpenAI-compatible tool-calling provider
- [x] Ollama-compatible `/v1` endpoint configuration with bounded low-reasoning interactive responses, a no-reasoning empty-response retry, and suppressed reasoning for short background responses
- [x] encrypted provider key, health test, enforced token-window prompt budget, context/temperature/timeout/tool settings UI
- [x] explicit removal of a previously saved AI provider API key without resetting other AI settings
- [x] conversation/message/tool-call persistence
- [x] user-initiated conversation deletion and current-turn-grounded follow-ups that avoid prior-answer repetition
- [x] prompt-injection boundary and no arbitrary shell/action tools
- [x] SSE streaming, user-facing activity, and restored conversation selection UI
- [x] token-level provider streaming, authenticated request cancellation, bounded recent context, output limits, and 30-day conversation cleanup
- [x] opt-in proactive LLM explanations layered on deterministic events with safe fallback
- [x] AI-only, multi-instance Sonarr/Radarr history collection with encrypted keys and normalized records
- [x] bounded media summaries and title follow-ups filterable by configurable instance name
- [x] provider-confirmed quality-upgrade pairing and normalized upcoming calendars using Sonarr episode air times and Radarr's selected calendar release date
- [x] additive opt-in cached AI dashboard summary with isolated scheduling and database transactions, SQLite WAL reader/writer concurrency, configured runtime/output bounds, suppressed Ollama reasoning traces, safe failure diagnostics, deterministic fallback, 12-hour local times, and validated storage/media claims
- [x] deterministic intent router with explicit ServerSense/SENSE AI provenance, configuration-aware sidebar status, and no-model factual telemetry responses
- [x] persistent bounded FIFO AI job queue with immutable model snapshots, explicit cancel/retry, uninterrupted post-threshold streaming, and restart interruption recovery
- [x] provider model discovery, minimal-generation health test, native-tool capability hints, and curated-context fallback
- [x] conversation summaries, structured references, model/provider message provenance, rename, search, configurable retention, and cascade deletion
- [x] separate background and hard-runtime settings, total context/telemetry budgets, retention/tool compatibility, timing diagnostics, and Ask SENSE job UI
- [x] partial-response preservation for timeout/cancel/failure/interruption plus deduplicated global/per-job long-running notifications, dismissible in-app notices, and category-controlled delivery of 200-character plain-text result summaries through configured notification providers
- [x] responsive Ask SENSE conversation history and message layout with a single notification control per active job
- [x] direct read-only telemetry access while a long SENSE AI analysis remains active
- [ ] embedded model download/runtime workflow (external Ollama-compatible local endpoints work)

## Production readiness

- [x] backend unit/API/persistence/forecast/policy/alert tests
- [x] backend Ruff and strict mypy clean
- [x] frontend lint and production TypeScript build clean
- [x] README, architecture, agent guidance, Unraid guide/template draft
- [x] frontend component tests
- [x] checked-in fresh-container Chromium E2E for setup, routes, SENSE, and mobile navigation
- [x] sanitized diagnostics bundle, SQLite backup endpoint/UI, and individually testable alert integrations with visible progress and results
- [x] production Docker build, health, setup, route, stream, and recreation verification
- [x] quiet production access logging that suppresses successful polling while retaining failed requests and operational warnings/errors
- [x] GitHub Actions verification gate and GitHub Container Registry publishing on push
- [x] migration upgrade/downgrade/upgrade round trip
- [x] Python and npm dependency audits report no known vulnerabilities
- [x] browser visual QA at desktop/mobile sizes and axe scans for setup and dashboard
- [x] security review of secrets, tools, subprocesses, sessions, rate limits, and prompt data
- [x] final criterion-by-criterion audit
