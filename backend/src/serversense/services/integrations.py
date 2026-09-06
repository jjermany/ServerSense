import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from serversense.models import Integration, MediaActivity, MediaSchedule
from serversense.services import http_requests
from serversense.services.secrets import decrypt_secret
from serversense.services.urls import validate_http_url

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
    parts = urlsplit(validate_http_url(value))
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
    response = http_requests.get(
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
        title = _text(episode.get("title"), 300)
        parent_title = _text(series.get("title"), 300) or None
        season = _integer(episode.get("seasonNumber"))
        number = _integer(episode.get("episodeNumber"))
        size = _integer(data.get("size")) or _integer(episode_file.get("size"))
        media_type = "episode"
    else:
        movie = _mapping(record.get("movie"))
        movie_file = _mapping(movie.get("movieFile"))
        title = _text(movie.get("title"), 300)
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


def _schedule(
    integration: Integration,
    record: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
) -> MediaSchedule | None:
    external_id = _text(record.get("id"), 80)
    if not external_id:
        return None
    if integration.provider == "sonarr":
        series = _mapping(record.get("series"))
        scheduled_at = _date(record.get("airDateUtc"))
        if scheduled_at is None:
            return None
        title = _text(record.get("title"), 300)
        parent_title = _text(series.get("title"), 300) or None
        season = _integer(record.get("seasonNumber"))
        episode = _integer(record.get("episodeNumber"))
        release_type = "airing"
        monitored = record.get("monitored") is not False and series.get("monitored") is not False
        has_file = record.get("hasFile") is True
        media_type = "episode"
        external_id = f"episode:{external_id}"
    else:
        candidates = [
            (_date(record.get("digitalRelease")), "digital_release"),
            (_date(record.get("physicalRelease")), "physical_release"),
            (_date(record.get("inCinemas")), "cinema_release"),
        ]
        # Radarr selects releaseDate for the calendar entry. Prefer that over
        # independently choosing the earliest metadata date, which can expose a
        # theatrical date while Radarr's calendar shows a later home release.
        calendar_date = _date(record.get("releaseDate"))
        if calendar_date is not None and window_start <= calendar_date <= window_end:
            scheduled_at = calendar_date
            release_type = next(
                (kind for value, kind in candidates if value == calendar_date),
                "calendar_release",
            )
        else:
            in_window = [
                (value, kind)
                for value, kind in candidates
                if value is not None and window_start <= value <= window_end
            ]
            if not in_window:
                return None
            scheduled_at, release_type = min(in_window, key=lambda item: item[0])
        statistics = _mapping(record.get("statistics"))
        title = _text(record.get("title"), 300)
        parent_title = None
        season = episode = None
        monitored = record.get("monitored") is not False
        has_file = (
            record.get("hasFile") is True or (_integer(statistics.get("movieFileCount")) or 0) > 0
        )
        media_type = "movie"
        external_id = f"movie:{external_id}"
    return MediaSchedule(
        integration_id=integration.id,
        external_id=external_id,
        scheduled_at=scheduled_at,
        provider=integration.provider,
        instance_name=integration.name,
        media_type=media_type,
        title=title or "Unknown title",
        parent_title=parent_title,
        season_number=season,
        episode_number=episode,
        release_type=release_type,
        monitored=monitored,
        has_file=has_file,
    )


def _fetch_calendar(integration: Integration, now: datetime) -> list[MediaSchedule]:
    window_start = now - timedelta(days=1)
    window_end = now + timedelta(days=31)
    params: dict[str, Any] = {
        "start": window_start.isoformat(),
        "end": window_end.isoformat(),
        "unmonitored": False,
    }
    if integration.provider == "sonarr":
        params["includeSeries"] = True
    payload = _request(integration, "calendar", params)
    if not isinstance(payload, list):
        raise ValueError("The server returned an unexpected calendar response")
    return [
        schedule
        for raw in payload[:1000]
        if isinstance(raw, dict)
        if (schedule := _schedule(integration, raw, window_start, window_end)) is not None
    ]


def collect_integration(db: Session, integration: Integration, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    config = dict(integration.config)
    last = _date(config.get("last_collected_at"))
    if last and now - last < POLL_INTERVAL:
        return 0
    cutoff = last or now - INITIAL_LOOKBACK
    # Finish all read-only network work before starting the update transaction.
    # Only normalized records survive the fetch phase.
    db.commit()
    activities: dict[str, MediaActivity] = {}
    for page in range(1, 5):
        params: dict[str, Any] = {
            "page": page,
            "pageSize": 250,
            "sortKey": "date",
            "sortDirection": "descending",
            "includeSeries" if integration.provider == "sonarr" else "includeMovie": True,
        }
        if integration.provider == "sonarr":
            params["includeEpisode"] = True
        payload = _request(integration, "history", params)
        records = payload.get("records", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise ValueError("The server returned an unexpected history response")
        reached_cutoff = False
        for raw in records[:250]:
            if not isinstance(raw, dict):
                continue
            activity = _activity(integration, raw)
            if activity and activity.occurred_at < cutoff:
                reached_cutoff = True
                continue
            if activity:
                activities.setdefault(activity.external_id, activity)
        if reached_cutoff or len(records) < 250:
            break
    schedules = _fetch_calendar(integration, now)
    existing = {
        row.external_id: row
        for row in db.scalars(
            select(MediaActivity).where(
                MediaActivity.integration_id == integration.id,
                MediaActivity.external_id.in_(activities),
            )
        )
    }
    added = 0
    for activity in activities.values():
        current = existing.get(activity.external_id)
        if current is None:
            db.add(activity)
            added += 1
        else:
            if activity.title != "Unknown title":
                current.title = activity.title
            for field in ("parent_title", "season_number", "episode_number", "quality", "bytes"):
                value = getattr(activity, field)
                if value is not None:
                    setattr(current, field, value)
            current.is_upgrade = current.is_upgrade or activity.is_upgrade
    db.execute(delete(MediaSchedule).where(MediaSchedule.integration_id == integration.id))
    db.add_all(schedules)
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
