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

Live monitoring is the first-launch default. For a demo on non-Unraid hardware, explicitly select demo data during setup or set `SERVERSENSE_DEMO_MODE=true`. Demo telemetry is labeled and is never mixed into live collection.

Network rates are calculated from consecutive persisted byte counters, so a new live installation shows **Learning** until two valid samples exist. Counter resets are treated as unavailable data rather than traffic spikes. Unraid pool capacity is normalized from the read-only WebGUI metadata mount and appears on the Storage page and in SENSE's read-only pool tool.

After live setup, ServerSense polls for the first collector run and the Disks page reports that telemetry is being collected instead of showing a misleading zero-device total. Empty Unraid disk slots are ignored. When direct `smartctl` access is unavailable, ServerSense uses Unraid's normalized temperature and device-status metadata as a conservative fallback; unavailable values remain explicitly unknown. Manufacturer names are accepted only from a conservative known-vendor mapping rather than guessed from arbitrary model or serial text. The Unraid template grants read-only SATA/SAS and NVMe block-device access through narrow Docker device cgroup rules plus the `SYS_RAWIO` capability needed for SAT passthrough, without privileged mode.

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
| `/mnt/user`            | `/mnt/user`                     | read-only  | Array capacity       |
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

Configure the endpoint without `/v1`, model name, optional API key, timeout, temperature, and tool-call limit. API keys are encrypted at rest using a key derived from `SERVERSENSE_SECRET_KEY`. Keep that secret stable across upgrades.

**Explain new alerts with SENSE** is an explicit opt-in. When enabled, each newly detected deterministic alert batch is sent to the configured model once for a concise explanation. These background requests expose no tools, their output is stored with model provenance, and collection/alert delivery continues if the provider is unavailable.

Stopped containers must remain continuously non-running for at least 10 minutes before ServerSense creates an alert, avoiding transient stops during appdata backups and updates. Alert acknowledgements are persisted and shown in the alert history. Alert settings configure free-space percentage, projected exhaustion lead time in days, and disk temperature. Delivery categories can independently include or exclude low space, forecast, SMART, temperature, and stopped-container alerts while still recording every alert in ServerSense. Each selected new alert is dispatched to every enabled notification provider; the per-provider test controls in Settings can verify live credentials and connectivity.

Settings includes dedicated Monitoring and Integrations sections. Monitoring reports the live or demo mode chosen during first-launch setup; that mode remains locked to prevent demo and live telemetry from sharing a database. The Integrations section configures and individually tests generic webhooks, Discord webhooks, Pushover, and SMTP email. Save and test controls show in-progress state and nearby success or error feedback. Provider tokens, webhook URLs, and SMTP credentials are encrypted at rest. None of these features require an AI provider.

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
