# ServerSense

ServerSense is a private, self-hosted monitoring and storage-intelligence application designed for Unraid. It combines deterministic capacity forecasts, disk/SMART and Docker visibility, rule-based alerts, and **SENSE**, a read-only assistant powered by your choice of local or OpenAI-compatible model.

> Current status: ServerSense v1.0.0. See [docs/COMPLETION_AUDIT.md](docs/COMPLETION_AUDIT.md) for verified completion evidence and known limitations.

## Quick start with Docker Compose

Requirements: Docker Engine with Compose v2 and an x86-64 Linux or Unraid host.

```bash
cp .env.example .env
# Replace SERVERSENSE_SECRET_KEY with: openssl rand -hex 32
docker compose up --build -d
```

Open `http://YOUR-SERVER-IP:8080`, create the local administrator, and follow first-launch setup. Data, encrypted provider credentials, logs, backups, and the SQLite database live under `./config`, mounted as `/config` in the container.

For a demo on non-Unraid hardware, set `SERVERSENSE_DEMO_MODE=true`. Demo telemetry is labeled and is never mixed into live collection.

## Unraid installation

Until the image is published to a registry, build it on a Docker-capable machine or use Compose directly. The intended container configuration is documented in [docs/UNRAID.md](docs/UNRAID.md); an importable template draft is at [unraid/serversense.xml](unraid/serversense.xml).

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
.\.venv\Scripts\python.exe -m mypy backend/src
cd frontend
npm.cmd run lint
npm.cmd run build
docker compose build
```

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
