# ServerSense v1 living checklist

Last updated: 2026-08-15

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

## Monitoring and intelligence

- [x] Linux system and capacity collector abstraction
- [x] Unraid detection and `disks.ini` adapter
- [x] fixed-argument JSON `smartctl` collection
- [x] non-privileged SMART device rules/SYS_RAWIO, SAT retry, and JSON field fallbacks
- [x] conservative known-vendor manufacturer normalization with unknown fallback
- [x] restricted Docker inventory collector
- [x] scheduled collection and retention cleanup
- [x] deterministic 7/30/90-day robust forecasting
- [x] dashboard, storage chart/ranges, disk cards, Docker table
- [x] metric-driven storage, forecast, SMART, temperature, and container rules
- [x] Unraid array/parity state, disk/cache inventory, and available APC UPS data
- [x] persisted per-container CPU, memory, health, restarts, and uptime
- [x] configurable alert thresholds and encrypted generic webhook, Discord, Pushover, and SMTP email delivery
- [x] daily retention cleanup and old storage sample downsampling
- [x] normalized Unraid pool detail and reset-safe calculated network transfer rates

## SENSE

- [x] read-only allowlisted structured tool registry
- [x] built-in deterministic no-model mode
- [x] OpenAI-compatible tool-calling provider
- [x] Ollama-compatible `/v1` endpoint configuration
- [x] encrypted provider key, health test, context/temperature/timeout/tool settings UI
- [x] conversation/message/tool-call persistence
- [x] prompt-injection boundary and no arbitrary shell/action tools
- [x] SSE streaming, user-facing activity, and restored conversation selection UI
- [x] opt-in proactive LLM explanations layered on deterministic events with safe fallback
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
- [x] GitHub Actions verification gate and GitHub Container Registry publishing on push
- [x] migration upgrade/downgrade/upgrade round trip
- [x] Python and npm dependency audits report no known vulnerabilities
- [x] browser visual QA at desktop/mobile sizes and axe scans for setup and dashboard
- [x] security review of secrets, tools, subprocesses, sessions, rate limits, and prompt data
- [x] final criterion-by-criterion audit
