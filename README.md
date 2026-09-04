# ServerSense

ServerSense is a private, self-hosted monitoring and storage-intelligence application designed for Unraid. It combines deterministic capacity forecasts, array and pool visibility, disk/SMART and Docker monitoring, measured network transfer rates, rule-based alerts, and **SENSE**, a read-only assistant powered by your choice of local or OpenAI-compatible model.

> Current status: ServerSense v1.0.0. See [docs/COMPLETION_AUDIT.md](docs/COMPLETION_AUDIT.md) for verified completion evidence and known limitations.

## Quick start with Docker Compose

Requirements: Docker Engine with Compose v2 and an x86-64 Linux or Unraid host.

```bash
cp .env.example .env
# Replace SERVERSENSE_SECRET_KEY with: openssl rand -hex 32
docker compose up --build -d
```

Open `http://YOUR-SERVER-IP:8080`, create the local administrator, and follow first-launch setup. Data, encrypted provider credentials, logs, backups, and the SQLite database live under `./config`, mounted as `/config` in the container.

Container output omits successful HTTP access entries, including UI polling and health checks, to keep routine logs quiet. Failed HTTP requests and application warnings/errors remain visible; `SERVERSENSE_LOG_LEVEL` controls application log verbosity.

Set the standard container `TZ` variable to an IANA timezone such as `America/Chicago`. It is the authoritative timezone for all displayed dates, chart labels, SENSE relative-date interpretation, and overview update times. When `TZ` is absent, a validated timezone fallback can be saved under **Settings → Monitoring**; UTC is used until one is selected.

Live monitoring is the first-launch default. For a demo on non-Unraid hardware, explicitly select demo data during setup or set `SERVERSENSE_DEMO_MODE=true`. Demo telemetry is labeled and is never mixed into live collection.

Network rates are calculated from consecutive persisted byte counters, so a new live installation shows **Learning** until two valid samples exist. Counter resets are treated as unavailable data rather than traffic spikes. Unraid pool capacity is normalized from the read-only WebGUI metadata mount and appears on the Storage page and in SENSE's read-only pool tool.

After live setup, ServerSense polls for the first collector run and the Disks page reports that telemetry is being collected instead of showing a misleading zero-device total. Empty Unraid disk slots are ignored. When direct `smartctl` access is unavailable, ServerSense uses Unraid's normalized temperature and device-status metadata as a conservative fallback; unavailable values remain explicitly unknown. Manufacturer names are accepted only from a conservative known-vendor mapping rather than guessed from arbitrary model or serial text. The Unraid template grants read-only SATA/SAS and NVMe block-device access through narrow Docker device cgroup rules plus the `SYS_RAWIO` capability needed for SAT passthrough, without privileged mode.

Combined array capacity is calculated from the assigned Unraid data-disk filesystem totals in read-only runtime metadata. Parity, boot, individual-device totals, and named pools such as cache are excluded; pools are reported separately. ServerSense never falls back to the container root filesystem when capacity metadata is unavailable. Forecasts, history, alerts, dashboards, and SENSE use only samples from the newest compatible measurement source, so older `/mnt/user` or fallback samples cannot be mixed into normalized array trends. Signed dashboard trend rates use human-readable decimal units, such as `-846.7 GB/day`, instead of raw bytes. The 24-hour storage chart labels its horizontal axis with configured-timezone 12-hour times; longer ranges use dates, and hover labels include the full local date, 12-hour time, and timezone.

Live CPU, memory, and network telemetry is collected every 30 seconds by default, then temporarily every 5 seconds while an authenticated ServerSense UI is visible. The visible UI renews a process-local 45-second activity lease; hidden or closed tabs stop renewing it and collection automatically returns to the background cadence. Docker is collected every 15 seconds while active and every 30 seconds otherwise. Container state-change timestamps are carried forward during collection, keeping Overview and Docker reads independent of retained Docker-history size. While visible, Overview and Alerts refresh every 5 seconds, Docker every 15 seconds, and Storage, Disks, and disk details every 60 seconds; every monitoring page also refreshes immediately when the browser tab becomes visible again. SMART inventory remains independent at a conservative 15-minute cadence to avoid excessive hardware polling; storage history remains hourly. These intervals can be overridden with the corresponding `SERVERSENSE_*_INTERVAL_SECONDS` environment variables shown in `.env.example`.

Authenticated API responses are explicitly non-cacheable. Overview displays the freshest page telemetry time; Storage, Disks, disk details, and Docker display their source sample times; and each deterministic or cached SENSE insight displays its own measurement or generation time in the configured timezone. The global status says monitoring is active instead of implying that every slow-cadence measurement changes continuously. This makes stale cached summaries, older alert explanations, unchanged measurements, and stalled collectors distinguishable from current telemetry.

User-facing UI and SENSE summary times use a 12-hour clock with AM/PM in the configured timezone.

## Published Docker image

Every push builds and publishes an x86-64 image to GitHub Container Registry. Pull the latest default-branch build with:

```bash
docker pull ghcr.io/jjermany/serversense:latest
```

Branch pushes also receive a branch tag and an immutable `sha-<commit>` tag. Version tags such as `v1.1.0` additionally publish `1.1.0` and `1.1`. The package must be made public in the repository's GitHub package settings before unauthenticated hosts can pull it.

## Unraid installation

Use `ghcr.io/jjermany/serversense:latest`, build on a Docker-capable machine, or use Compose directly. The intended container configuration is documented in [docs/UNRAID.md](docs/UNRAID.md); an importable template draft is at [unraid/serversense.xml](unraid/serversense.xml).

Required mounts:

| Container path         | Host path                       | Mode       | Purpose              |
| ---------------------- | ------------------------------- | ---------- | -------------------- |
| `/config`              | `/mnt/user/appdata/serversense` | read/write | All persistent state |
| `/var/local/emhttp`    | `/var/local/emhttp`             | read-only  | Unraid disk metadata |
| `/etc/unraid-version`  | `/etc/unraid-version`           | read-only  | Platform detection   |
| `/dev`                 | `/dev`                          | read-only  | SMART device queries |
| `/var/run/docker.sock` | `/var/run/docker.sock`          | read-only  | Docker inventory     |

The Docker API is powerful even through a read-only bind. ServerSense uses it only in a restricted collector and never exposes it to SENSE. Do not publish ServerSense directly to the public internet; put it behind a trusted TLS reverse proxy if remote access is required.

## SENSE model setup

Monitoring works when AI is disabled. In **Settings → AI**, select:

- **Built-in deterministic mode** for safe answers assembled directly from telemetry.
- **Ollama-compatible** for a local endpoint exposing the OpenAI-compatible `/v1` API.
- **OpenAI-compatible API** for another compatible local or remote provider.

ServerSense routes clear current-fact questions—such as combined-array free space, disk temperature, container state, CPU/memory, uptime, parity state, and named-pool capacity—to deterministic read-only services without calling a model. These responses are labeled **ServerSense · live telemetry**. Explanation, diagnosis, historical synthesis, recommendations, and general conversation are labeled **SENSE AI** and use the model selected when the request is submitted. The sidebar reports the configured AI provider and model when enabled, or clearly states that no AI model is configured. Interactive Ollama analysis uses low reasoning, then automatically retries once without a separate reasoning trace if the model produces no visible content. A provider completion that remains empty is recorded as a failed job rather than a successful empty message.

Configure the endpoint without `/v1`, discover or enter a model name, optional API key, provider inactivity timeout, temperature, tool compatibility, queue concurrency, background threshold, hard maximum runtime, context/telemetry budgets, retention, notification default, and output limits. The configured token context window bounds the prompt after reserving maximum response tokens and conservative request/tool-schema overhead; the character context limit remains a secondary safety cap. A previously saved API key can be explicitly cleared without changing the remaining AI configuration. The background threshold only changes the UI state; the independently configured maximum runtime is the hard overall execution limit for interactive inference and cached dashboard-summary generation. The provider inactivity timeout limits how long an interactive request may wait between response data, and connection setup remains narrowly bounded, so either can stop a stalled request before the overall maximum runtime. Job errors identify which limit fired and show both relevant configured values. The connection test verifies both model discovery and a minimal generation. `Auto fallback` first allows native read-only tool calls and retries with bounded pre-gathered telemetry when a compatible provider rejects tool fields; `Curated context only` is available for models with no native tool support. API keys are encrypted at rest using a key derived from `SERVERSENSE_SECRET_KEY` and are never copied into a job snapshot.

AI requests are persistent jobs with a default concurrency of one and a bounded FIFO queue. They expose queued, context-gathering, analyzing, streaming, completed, failed, cancelled, timed-out, and interrupted states, plus queue wait, first-token, and inference timing. Active SENSE messages show a live elapsed inference time, interpreting offset-free persisted timestamps as UTC, and completed AI messages retain their final elapsed time after reload. After 30 seconds by default, the UI explains that the request is still running and keeps streaming; navigating away, reloading, or disconnecting the stream does not cancel it. If the live browser stream is interrupted, the UI follows the durable job through polling and loads its eventual result instead of presenting the transport failure as a ServerSense answer. Direct ServerSense telemetry remains available from the long-running panel. A restart marks an in-flight provider stream interrupted rather than silently replaying it; queued work remains queued, and retries are explicit. Once a retry is accepted, its new attempt replaces the older failed attempt as the actionable job so the stale Retry control is not shown beside an active replacement. Broad “what changed” requests pre-gather bounded storage history, alerts, media activity, current container state-change timestamps, and overview telemetry so SENSE synthesizes available evidence immediately instead of offering to check it later. Long-running completion notifications are deduplicated, linked to the conversation, dismissible individually or as a group, and controlled globally and per job. Eligible job results are also delivered through enabled webhook, Discord, Pushover, and email providers when the SENSE job notification category is enabled. Notification bodies contain a plain-text summary of the result capped at 200 characters, allowing the result to be checked without opening ServerSense. Cancellation, timeout, interruption, and provider failure preserve any generated text as a clearly labeled partial assistant entry. SENSE enforces one total prompt budget across instructions, normalized telemetry, summary, references, and recent active messages. Conversations can be searched, renamed, deleted, and retained for the configured number of days (30 by default).

For AI-assisted media explanations, add one or more named **Sonarr** or **Radarr** instances in **Settings → Integrations**. Names are fully configurable, so separate instances such as `Movies` and `Anime` remain distinguishable in summaries and follow-up lists. ServerSense encrypts each API key, polls only fixed read-only v3 status, history, and calendar endpoints while AI is enabled, and stores bounded normalized activity rather than raw responses or filesystem paths. Quality upgrades are confirmed from the provider's explicit `Upgrade` deletion reason and paired with a nearby import when available; general activity lists expose that pair as one `quality_upgraded` event rather than an unrelated import and deletion. When both provider-reported sizes are present, SENSE can report the old size, replacement size, and logical net change (`replacement - old`) for one upgrade or a bounded set; missing sizes are never estimated, and this calculation remains distinct from measured array growth. Upcoming Sonarr entries use the episode `airDateUtc`; upcoming Radarr entries prefer Radarr's provider-selected calendar `releaseDate` and retain its matching theatrical, digital, or physical event type. These entries may be grabbed when eligible—they are not guaranteed scheduled downloads. SENSE receives explicit configured-timezone, 12-hour display values alongside internal UTC timestamps and must use only the local form in user-facing answers. Import totals are gross event evidence and remain separate from measured net array change; correlation does not prove the media activity caused that change.

**Add a cached AI dashboard summary** is a separate opt-in for the existing SENSE Insight card. It adds one short model-generated overview above the unchanged deterministic storage and temperature insights. The isolated background worker starts 30 seconds after ServerSense and checks every five minutes, so the first attempt after enabling normally occurs within five minutes. It releases its database read transaction before contacting the model, and SQLite uses WAL mode so a slow model cannot block monitoring commits or Overview readers. It requires a configured provider, model, valid endpoint, and at least one current storage sample. One day of history does not block a summary: the deterministic forecast remains in its learning state until it has at least three samples spanning two days, and the model summary must describe that uncertainty. Generation runs outside telemetry collection and dashboard requests, uses the configured **Maximum runtime** and **Maximum response tokens** values, refreshes no more than every six hours (or after a meaningful alert/media change with a 15-minute minimum interval), and uses bounded normalized facts with no tools. Ollama summary requests disable a separate reasoning trace so thinking-capable models reserve their output budget for the short final answer. Local calendar times are supplied in 12-hour AM/PM form, calendar entries must remain upcoming/eligible rather than guaranteed imports or downloads, and exhaustion-risk claims require a deterministic forecast. Summaries that violate those rules are discarded with a safe reason in the application log. A measurement-source change invalidates an older summary immediately. A successful summary is cached; model errors leave the existing card untouched, and summaries older than 12 hours are hidden.

**Explain new alerts with SENSE** is an explicit opt-in. When enabled, each newly detected deterministic alert batch is sent to the configured model once for a concise explanation. These background requests expose no tools, their output is stored with model provenance, and collection/alert delivery continues if the provider is unavailable.

Stopped containers must remain continuously non-running for at least 10 minutes before ServerSense creates an alert, avoiding transient stops during appdata backups and updates. Alert acknowledgements are persisted and shown in the alert history, while dismissed alerts are retained in storage but hidden from the alert history, dashboard, and SENSE context. Alert settings configure free-space percentage, projected exhaustion lead time in days, and disk temperature. Delivery categories can independently include or exclude low space, forecast, SMART, temperature, stopped-container alerts, and long-running SENSE job results while still recording every monitoring alert in ServerSense. Each selected new alert or eligible SENSE result is dispatched to every enabled notification provider; the per-provider test controls in Settings can verify live credentials and connectivity.

Settings includes dedicated Monitoring and Integrations sections. Monitoring reports the live or demo mode chosen during first-launch setup; that mode remains locked to prevent demo and live telemetry from sharing a database. The Integrations section configures and individually tests generic webhooks, Discord webhooks, Pushover, SMTP email, and any number of named Sonarr/Radarr instances. Save and test controls show in-progress state and nearby success or error feedback. Credentials are encrypted at rest. Alert delivery does not require AI; media history collection is AI-only.

SENSE can call only the read-only functions in `services/tools.py`. It has no shell, file, Docker-control, or Unraid mutation tool.

## Local development

Backend (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
$env:SERVERSENSE_CONFIG_DIR="./config"
$env:SERVERSENSE_SECRET_KEY="development-secret-change-this"
.\.venv\Scripts\python.exe -m uvicorn serversense.main:app --reload
```

Frontend:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Verification:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format --check backend
.\.venv\Scripts\python.exe -m mypy backend/src
cd frontend
npm.cmd test
npm.cmd run lint
npm.cmd run build
npm.cmd run test:e2e:install  # first run only
npm.cmd run test:e2e
cd ..
docker compose build
```

The end-to-end command builds the production image, starts it with an isolated temporary `/config`, verifies setup and the primary application flows in Chromium, and removes the test container afterward.

OpenAPI documentation is available at `/docs` in development and production.

The web client bounds API requests and automatically retries its startup authentication check, so a temporary database lock or backend stall cannot leave the app permanently stuck on the connecting screen. Monitoring pages retain their most recent data across sidebar navigation and refresh it in the background instead of returning to an empty loading state.

If a browser tab stays open across a ServerSense update, its cached bundle can reference a page chunk that no longer exists on the server. The app detects that failure and reloads once automatically to pick up the new build; if the page still won't load after that, it shows a manual reload prompt instead of leaving the content area blank.

## Architecture

ServerSense is a modular monolith in one container:

- `backend/src/serversense/api` — authenticated FastAPI routes
- `backend/src/serversense/services` — collection, forecasting, alerting, SENSE, tool policy
- `backend/src/serversense/models.py` — SQLAlchemy persistence model
- `backend/migrations` — Alembic schema migrations
- `frontend/src/pages` — React/TypeScript product screens
- `/config/serversense.db` — persistent SQLite database

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for security boundaries and collection flow.

## License

No license has been selected yet. All rights reserved until one is added.
