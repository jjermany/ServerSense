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
Route high-confidence current telemetry questions to deterministic ServerSense responses without invoking a model; store those messages with `serversense` provenance. Interactive model work must use persistent, asynchronously cancellable jobs with bounded streamed output. Enforce the configured context window as a conservative total prompt budget after reserving response tokens and request/tool-schema overhead, with the character limit as a secondary cap. Use low reasoning for Ollama interactive work and retry once without a separate reasoning trace if it returns no visible content; treat a completion that remains empty as a failure rather than a successful empty answer. Persist the submitted user message with its job; create a normal assistant exchange on successful completion, or a clearly labeled incomplete assistant entry when cancellation, timeout, interruption, or failure occurs after partial output. Browser disconnects must not cancel jobs; after live-stream loss, keep following the durable job and load its terminal result without creating a false ServerSense response. The background threshold changes presentation only and must remain separate from the hard maximum runtime. A queued job snapshots its provider, model, endpoint, and bounded options without persisting credentials; settings changes apply only to newly submitted jobs. In-flight provider streams are marked interrupted after restart rather than silently replayed. Show live elapsed inference time on active SENSE messages, interpreting offset-free persisted timestamps as UTC, and derive the persisted final elapsed time for completed messages from durable job timestamps. Once a retry is accepted, show its newest attempt as actionable and suppress the stale Retry control from the superseded attempt. Broad change-summary requests must pre-gather bounded storage history, recent alerts, media activity, container state-change timestamps, and current overview facts rather than asking the user for permission to inspect already available read-only sources. Completion notifications must be deduplicated, dismissible, honor global and per-job preferences, use enabled notification providers only when the SENSE job delivery category is enabled, and summarize the result as plain text within 200 characters. Conversation context uses a persisted bounded summary plus recent messages within a 30-minute active window, with one enforced total prompt budget; treat prior answers as context rather than content to repeat in follow-ups. Keep current-turn instructions adjacent to the request and after tool results, require the upcoming-media tool for clear calendar requests and accepted title-list offers, require quality-upgrade evidence for upgrade challenges, and never let the model invent media titles or dates absent from current tool results. Stored conversations use the configured retention period and may be renamed, searched, or explicitly deleted with their messages, jobs, notifications, and tool-call records.
Dashboard model summaries are additive and opt-in. Generate them only in the isolated background loop from bounded normalized facts, use the configured hard maximum runtime and response-token limit within their validated ranges, disable separate reasoning output for Ollama summary requests, preserve the deterministic insight card, retain the last successful cache on failure, and never call a model from dashboard requests or the monitoring loop. Supply local media times in a 12-hour AM/PM display form, describe calendar entries as upcoming/eligible rather than scheduled imports or downloads, and reject summaries that violate those presentation rules or claim low exhaustion risk without a deterministic forecast. Log only a safe internal rejection reason, never model content.
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
- Keep Settings forms grouped by purpose with consistent control dimensions, full-width cards, and responsive grids.
- Validate every external input with Pydantic or a narrow internal policy.
- Never log passwords, API keys, session tokens, full model requests, or model responses containing secrets.
- Preserve saved secrets when credential inputs are left blank, and provide an explicit narrow action when a saved credential can be cleared.
- Keep successful HTTP polling and health-check access entries suppressed while retaining failed requests and operational warnings/errors.
- Use fixed command argument arrays for hardware tools. Never concatenate telemetry into a shell command.
- Keep SMART access non-privileged: mount `/dev` read-only, grant only read access to required block-device families with Docker device cgroup rules, and add only `SYS_RAWIO` for SAT passthrough.
- Retry an incomplete SCSI-only `smartctl` response with the fixed `-d sat` argument; never derive a device type from telemetry text.
- Normalize disk manufacturers only through an explicit known-vendor mapping; unknown model and serial text must remain unknown.
- The Docker socket stays inside the collector boundary. SENSE reads normalized database records only.
- Keep network transfer rates derived from consecutive persisted counters; reject resets and non-positive intervals.
- Keep active-viewer CPU, memory, and network collection lightweight and gated by an authenticated, visible-UI lease. Keep Docker, SMART disk, and storage-history collection on independent slower cadences.
- Keep monitoring pages visibility-aware and auto-refreshing at a cadence appropriate to their source. Display source sample timestamps for slow-cadence telemetry; do not imply that every monitored value changes continuously.
- Keep browser API requests bounded and make the initial authentication bootstrap recover automatically from temporary backend stalls.
- Preserve the latest monitoring data across sidebar navigation, refresh it in the background, and clear the browser-side live-data cache on logout.
- Wrap the authenticated route tree in a boundary that recovers a stale-build lazy chunk load failure with a single automatic reload, falling back to a manual reload prompt rather than looping if the failure repeats.
- Keep the shared SENSE status configuration-aware: show the provider and model when AI is configured, and do not label an AI-enabled installation as deterministic-only.
- Format user-facing UI and SENSE summary times with a 12-hour AM/PM clock in the configured timezone; retain UTC ISO timestamps for storage and transport. Supply explicit local-display fields beside raw tool timestamps and require interactive answers to use only the local-display form. Format signed user-facing storage values by magnitude with human-readable units; never expose raw byte counts or contradictory signs/arrows in trend labels.
- Keep private Unraid metadata parsing inside collectors and expose only normalized pool records.
- Ignore unassigned Unraid disk slots, use normalized Unraid temperature/status only as a conservative SMART fallback, and scope current inventory views to the newest complete snapshot.
- Derive combined Unraid array capacity only from assigned data-disk filesystem metadata. Exclude parity, boot, and named pools; never fall back to `/`, `/mnt/user`, an individual disk, or a pool. Keep storage history and forecasts within the newest measurement source and label array, device, and pool scope explicitly in SENSE tools.
- Preserve demo/live separation and test startup races.
- Require 10 minutes of continuous non-running Docker telemetry before creating a stopped-container alert; a running sample resets the grace period.
- Persist Docker state-change timestamps during collection; Overview and Docker requests must not scan retained container history.
- Keep the post-setup monitoring mode display read-only so demo and live telemetry cannot be mixed.
- Add tests for forecasting, permissions, alerts, migrations, setup, and persistence when those paths change.
- Keep model output, conversation context, tool loops, and provider timeouts explicitly bounded. Distinguish provider inactivity and connection timeouts from the overall maximum runtime in user-facing job errors.
- Never hold a SQLite transaction open across model or other external network I/O; keep WAL enabled so dashboard readers remain available during monitoring writes.
- Treat every Sonarr/Radarr installation as a separately named integration. Encrypt API keys, poll only fixed read-only v3 endpoints without redirects, discard raw payloads and paths, deduplicate history and replace bounded calendar snapshots per integration, and expose only normalized media activity to SENSE while AI is enabled. Use Sonarr episode `airDateUtc` and prefer Radarr's provider-selected calendar `releaseDate`. Confirm quality upgrades from the provider's explicit `Upgrade` deletion reason, represent a paired upgrade deletion/import as one normalized upgrade item in general activity lists, calculate logical upgrade net bytes only when both provider-reported file sizes are present, and describe calendar entries as upcoming/eligible rather than guaranteed downloads.
- Update `README.md`, this file, and `docs/IMPLEMENTATION_CHECKLIST.md` as behavior changes.
