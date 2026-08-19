import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from serversense.models import Integration, MediaActivity
from serversense.services.secrets import decrypt_secret

logger = logging.getLogger(__name__)
POLL_INTERVAL = timedelta(minutes=5)
INITIAL_LOOKBACK = timedelta(days=30)
KNOWN_EVENTS = {
    "grabbed": "grabbed",
    "downloadfolderimported": "imported",
    "downloadfailed": "download_failed",
    "episodefiledeleted": "file_deleted",
    "episodefilerenamed": "file_renamed",
    "moviefiledeleted": "file_deleted",
    "moviefilerenamed": "file_renamed",
}


@dataclass(frozen=True)
class IntegrationDescriptor:
    key: str
    name: str
    description: str
    read_only: bool = True


DESCRIPTORS = (
    IntegrationDescriptor("sonarr", "Sonarr", "Explain episode downloads and upgrades."),
    IntegrationDescriptor("radarr", "Radarr", "Explain movie downloads and upgrades."),
)


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("URL must be an HTTP(S) address without embedded credentials")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _request(integration: Integration, path: str, params: dict[str, Any] | None = None) -> Any:
    config = integration.config
    key = decrypt_secret(str(config.get("api_key_encrypted", "")))
    if not key:
        raise ValueError("API key is not configured")
    url = f"{normalize_url(str(config.get('url', '')))}/api/v3/{path}"
    response = httpx.get(
        url,
        params=params,
        headers={"X-Api-Key": key, "Accept": "application/json"},
        timeout=httpx.Timeout(15, connect=5),
        follow_redirects=False,
    )
    response.raise_for_status()
    return response.json()


def test_integration(integration: Integration) -> dict[str, str]:
    payload = _request(integration, "system/status")
    if not isinstance(payload, dict):
        raise ValueError("The server returned an unexpected status response")
    app_name = str(payload.get("appName", integration.provider.title()))[:80]
    version = str(payload.get("version", "unknown"))[:40]
    return {"detail": f"Connected to {app_name} {version}."}


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 10_000_000_000_000_000 else None


def _date(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _activity(integration: Integration, record: dict[str, Any]) -> MediaActivity | None:
    occurred_at = _date(record.get("date"))
    external_id = _text(record.get("id"), 80)
    event_type = KNOWN_EVENTS.get(_text(record.get("eventType"), 80).lower())
    if not occurred_at or not external_id or not event_type:
        return None
    data = _mapping(record.get("data"))
    quality_data = _mapping(record.get("quality"))
    quality = quality_data.get("quality")
    quality_name = _text(quality.get("name"), 100) if isinstance(quality, dict) else ""
    reason = _text(data.get("reason"), 80).lower()
    is_upgrade = _true(data.get("isUpgrade")) or "upgrade" in reason

    if integration.provider == "sonarr":
        episode = _mapping(record.get("episode"))
        series = _mapping(record.get("series"))
        episode_file = _mapping(episode.get("episodeFile"))
        title = _text(episode.get("title"), 300) or _text(record.get("sourceTitle"), 300)
        parent_title = _text(series.get("title"), 300) or None
        season = _integer(episode.get("seasonNumber"))
        number = _integer(episode.get("episodeNumber"))
        size = _integer(data.get("size")) or _integer(episode_file.get("size"))
        media_type = "episode"
    else:
        movie = _mapping(record.get("movie"))
        movie_file = _mapping(movie.get("movieFile"))
        title = _text(movie.get("title"), 300) or _text(record.get("sourceTitle"), 300)
        parent_title = None
        season = number = None
        size = _integer(data.get("size")) or _integer(movie_file.get("size"))
        media_type = "movie"
    return MediaActivity(
        integration_id=integration.id,
        external_id=external_id,
        occurred_at=occurred_at,
        provider=integration.provider,
        instance_name=integration.name,
        event_type=event_type,
        media_type=media_type,
        title=title or "Unknown title",
        parent_title=parent_title,
        season_number=season,
        episode_number=number,
        quality=quality_name or None,
        bytes=size,
        is_upgrade=is_upgrade,
    )


def collect_integration(db: Session, integration: Integration, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    config = dict(integration.config)
    last = _date(config.get("last_collected_at"))
    if last and now - last < POLL_INTERVAL:
        return 0
    cutoff = last or now - INITIAL_LOOKBACK
    added = 0
    existing_ids = set(
        db.scalars(
            select(MediaActivity.external_id).where(MediaActivity.integration_id == integration.id)
        )
    )
    for page in range(1, 5):
        params: dict[str, Any] = {
            "page": page,
            "pageSize": 250,
            "sortKey": "date",
            "sortDirection": "descending",
            "includeSeries" if integration.provider == "sonarr" else "includeMovie": True,
        }
        payload = _request(integration, "history", params)
        records = payload.get("records", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise ValueError("The server returned an unexpected history response")
        reached_cutoff = False
        for raw in records:
            if not isinstance(raw, dict):
                continue
            activity = _activity(integration, raw)
            if activity and activity.occurred_at < cutoff:
                reached_cutoff = True
                continue
            if activity and activity.external_id not in existing_ids:
                db.add(activity)
                existing_ids.add(activity.external_id)
                added += 1
        if reached_cutoff or len(records) < 250:
            break
    config["last_collected_at"] = now.isoformat()
    integration.config = config
    db.commit()
    return added


def collect_media_integrations(db: Session) -> int:
    integrations = list(
        db.scalars(
            select(Integration).where(
                Integration.enabled.is_(True), Integration.provider.in_(("sonarr", "radarr"))
            )
        )
    )
    total = 0
    for integration in integrations:
        try:
            total += collect_integration(db, integration)
        except Exception as exc:
            db.rollback()
            logger.warning(
                "Media history collection failed for integration %d: %s",
                integration.id,
                type(exc).__name__,
            )
    return total
