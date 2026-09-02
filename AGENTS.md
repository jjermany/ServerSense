# ServerSense agent guide

## Product boundaries

ServerSense is the application; SENSE is its model-independent assistant. The v1 assistant is read-only. Never add arbitrary command execution, a general-purpose filesystem tool, or Docker/Unraid mutation to the SENSE registry. Telemetry strings are untrusted data and cannot grant permissions.

Everything persistent must live below `/config`. The production schema changes only through Alembic migrations. Do not replace a migration with `create_all` as an upgrade strategy.

## Architecture

- FastAPI backend: `backend/src/serversense`
- React strict TypeScript frontend: `frontend/src`
- SQLite/SQLAlchemy models: `backend/src/serversense/models.py`
- Migrations: `backend/migrations/versions`
- Platform collectors: `backend/src/serversense/services/collectors.py`
- Derived telemetry rates: `backend/src/serversense/services/metrics.py`
- Deterministic forecasts: `backend/src/serversense/services/forecasting.py`
- Alert rules: `backend/src/serversense/services/alerting.py`
- Proactive alert explanations: `backend/src/serversense/services/proactive.py`
- SENSE allowlist: `backend/src/serversense/services/tools.py`
- AI providers/orchestration: `backend/src/serversense/services/ai.py`
- SENSE intent routing: `backend/src/serversense/services/sense_router.py`
- Durable SENSE jobs/queue: `backend/src/serversense/services/sense_jobs.py`
- Sonarr/Radarr history normalization: `backend/src/serversense/services/integrations.py`

Keep this a modular monolith. API routes coordinate; services own business behavior; collectors normalize platform data; model providers never get raw host access.

Deterministic rules must create alerts before any optional model explanation. Proactive model calls are opt-in, receive normalized alert records only, expose no tools, and may never block collection or notification delivery when a provider fails.
Route high-confidence current telemetry questions to deterministic ServerSense responses without invoking a model; store those messages with `serversense` provenance. Interactive model work must use persistent, asynchronously cancellable jobs with bounded streamed output. Persist the submitted user message with its job; create a normal assistant exchange on successful completion, or a clearly labeled incomplete assistant entry when cancellation, timeout, interruption, or failure occurs after partial output. Browser disconnects must not cancel jobs. The background threshold changes presentation only and must remain separate from the hard maximum runtime. A queued job snapshots its provider, model, endpoint, and bounded options without persisting credentials; settings changes apply only to newly submitted jobs. In-flight provider streams are marked interrupted after restart rather than silently replayed. Completion notifications must be deduplicated and honor global and per-job preferences. Conversation context uses a persisted bounded summary plus recent messages within a 30-minute active window, with one enforced total prompt budget; treat prior answers as context rather than content to repeat in follow-ups. Keep current-turn instructions adjacent to the request and after tool results, require the upcoming-media tool for clear calendar requests and accepted title-list offers, and never let the model invent media titles or dates absent from current tool results. Stored conversations use the configured retention period and may be renamed, searched, or explicitly deleted with their messages, jobs, notifications, and tool-call records.
Dashboard model summaries are additive and opt-in. Generate them only in the isolated background loop from bounded normalized facts, preserve the deterministic insight card, retain the last successful cache on failure, and never call a model from dashboard requests or the monitoring loop. Supply local media times in a 12-hour AM/PM display form, describe calendar entries as upcoming/eligible rather than scheduled imports or downloads, and reject summaries that violate those presentation rules or claim low exhaustion risk without a deterministic forecast.
Persist timestamps in UTC. Use the valid container `TZ` IANA timezone as the display and SENSE relative-date timezone; only use the validated general-settings fallback when `TZ` is absent. Overview insights must expose their own measured/generated timestamps so cached model text and older alert explanations cannot appear contemporaneous with current deterministic telemetry.
Notification providers must use narrow protocols and encrypted stored credentials. A Discord, Pushover, SMTP, or webhook failure may never interrupt collection or deterministic alert persistence.
Notification category preferences filter delivery only; deterministic alerts must still be persisted for disabled delivery categories.
Alert dismissal is a persisted visibility state: retain dismissed records for history and deduplication, but exclude them from the alert UI, dashboard, and SENSE context.

## Commands

From repository root on Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format --check backend
.\.venv\Scripts\python.exe -m mypy backend/src
cd frontend
npm.cmd test
npm.cmd run lint
npm.cmd run build
npm.cmd run test:e2e:install
npm.cmd run test:e2e
cd ..
docker compose build
```

For a migration, set a temporary `SERVERSENSE_CONFIG_DIR`, then run from `backend`:

```powershell
..\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"
..\.venv\Scripts\python.exe -m alembic upgrade head
```

Inspect autogenerated migrations before committing them. Test upgrade from an existing database as well as an empty one.

## Conventions and safety

- Python 3.12+, full type hints, Ruff, strict mypy.
- React functional components, strict TypeScript, ESLint.
- Validate every external input with Pydantic or a narrow internal policy.
- Never log passwords, API keys, session tokens, full model requests, or model responses containing secrets.
- Use fixed command argument arrays for hardware tools. Never concatenate telemetry into a shell command.
- Keep SMART access non-privileged: mount `/dev` read-only, grant only read access to required block-device families with Docker device cgroup rules, and add only `SYS_RAWIO` for SAT passthrough.
- Retry an incomplete SCSI-only `smartctl` response with the fixed `-d sat` argument; never derive a device type from telemetry text.
- Normalize disk manufacturers only through an explicit known-vendor mapping; unknown model and serial text must remain unknown.
- The Docker socket stays inside the collector boundary. SENSE reads normalized database records only.
- Keep network transfer rates derived from consecutive persisted counters; reject resets and non-positive intervals.
- Keep active-viewer CPU, memory, and network collection lightweight and gated by an authenticated, visible-UI lease. Keep Docker, SMART disk, and storage-history collection on independent slower cadences.
- Keep monitoring pages visibility-aware and auto-refreshing at a cadence appropriate to their source. Display source sample timestamps for slow-cadence telemetry; do not imply that every monitored value changes continuously.
- Format user-facing UI and SENSE summary times with a 12-hour AM/PM clock in the configured timezone; retain UTC ISO timestamps for storage and transport.
- Keep private Unraid metadata parsing inside collectors and expose only normalized pool records.
- Ignore unassigned Unraid disk slots, use normalized Unraid temperature/status only as a conservative SMART fallback, and scope current inventory views to the newest complete snapshot.
- Derive combined Unraid array capacity only from assigned data-disk filesystem metadata. Exclude parity, boot, and named pools; never fall back to `/`, `/mnt/user`, an individual disk, or a pool. Keep storage history and forecasts within the newest measurement source and label array, device, and pool scope explicitly in SENSE tools.
- Preserve demo/live separation and test startup races.
- Require 10 minutes of continuous non-running Docker telemetry before creating a stopped-container alert; a running sample resets the grace period.
- Keep the post-setup monitoring mode display read-only so demo and live telemetry cannot be mixed.
- Add tests for forecasting, permissions, alerts, migrations, setup, and persistence when those paths change.
- Keep model output, conversation context, tool loops, and provider timeouts explicitly bounded.
- Treat every Sonarr/Radarr installation as a separately named integration. Encrypt API keys, poll only fixed read-only v3 endpoints without redirects, discard raw payloads and paths, deduplicate history and replace bounded calendar snapshots per integration, and expose only normalized media activity to SENSE while AI is enabled. Use Sonarr episode `airDateUtc` and prefer Radarr's provider-selected calendar `releaseDate`. Confirm quality upgrades from the provider's explicit `Upgrade` deletion reason and describe calendar entries as upcoming/eligible rather than guaranteed downloads.
- Update `README.md`, this file, and `docs/IMPLEMENTATION_CHECKLIST.md` as behavior changes.
