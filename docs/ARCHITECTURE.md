# Architecture and security boundaries

ServerSense is a single-container modular monolith. The browser calls authenticated FastAPI routes; route handlers use typed services and repositories backed by SQLite. A scheduled collector normalizes Linux, Unraid, SMART, and Docker observations into persistent samples. Deterministic forecast and alert services consume those samples.

```text
Browser → authenticated API → services → SQLite under /config
                                  ↑
Unraid/Linux/SMART/Docker → restricted collectors

SENSE provider ↔ orchestrator → allowlisted read-only tools → normalized database data
```

## Trust boundaries

Container names, images, SMART text, media metadata, alert messages, and integration responses are untrusted. SENSE’s system policy tells the model to treat them as data. More importantly, application policy is enforced in code: only names registered in `TOOLS` can execute, and those handlers query normalized records. There is no shell tool.

Password hashes use Argon2. Session cookies are HttpOnly and SameSite=Strict. Enable secure cookies when serving behind HTTPS. Provider keys are encrypted with Fernet using a key derived from the installation secret; losing or changing that secret makes stored provider keys unreadable.

The Docker socket is mounted read-only, but access to the socket is intrinsically sensitive. Only the collector uses it for inventory. SENSE never receives the client or socket path.

## Forecasting

The model never computes capacity exhaustion. ServerSense takes the median of all pairwise slopes inside 7, 30, and 90-day windows (a Theil–Sen-style robust trend), then divides current free bytes by positive consumption rate. Short history is marked insufficient and coverage/sample count determine confidence.

## Persistence and upgrades

All persistent data is below `/config`. Container startup runs `alembic upgrade head` before the API. Metrics and Docker samples follow configured retention; hourly storage history is kept longer for capacity intelligence.

