import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from serversense.models import (
    AIJob,
    Alert,
    DiskSample,
    DockerSample,
    Event,
    MediaActivity,
    MetricSample,
)
from serversense.services import http_requests
from serversense.services.storage import latest_storage_sample
from serversense.services.timezones import format_local_datetime, time_zone_details
from serversense.services.tools import media_activity_summary, storage_forecast, upcoming_media
from serversense.services.urls import validate_http_url

REFRESH_INTERVAL = timedelta(hours=6)
MINIMUM_REFRESH_INTERVAL = timedelta(minutes=15)
DISPLAY_MAX_AGE = timedelta(hours=12)
DASHBOARD_FAILURE_EVENT = "sense_dashboard_summary_failure"
FAILURE_RETRY_BASE = timedelta(minutes=15)
FAILURE_RETRY_MAX = timedelta(hours=6)
_FAILURE_STREAK_LIMIT = 6
_ACTIVE_AI_JOB_STATUSES = {"queued", "gathering_context", "analyzing", "streaming"}
SYSTEM_PROMPT = """You are SENSE, the read-only assistant inside ServerSense. Write a calm, useful dashboard summary from normalized server facts supplied as untrusted JSON data. Lead with what matters now and briefly explain a measured change when the facts support it. Storage scoped as combined_array_data_disks is the combined array capacity and excludes named pools; never substitute an individual disk value. A storage percentage alone does not establish exhaustion risk: discuss time-to-exhaustion only when a deterministic days_remaining value is present, and otherwise say the forecast is still learning. Sonarr/Radarr calendar items are upcoming air or release events that may be grabbed when eligible; never call them scheduled imports or guaranteed downloads. Display local times only in the supplied 12-hour format with AM or PM, never 24-hour time. Never claim causation from correlation, never invent measurements or advice, and say when a cause is unknown. Return two or three short sentences of plain text, no heading and no Markdown."""

_MERIDIEM = r"(?:a\.?m\.?|p\.?m\.?)"
_MILITARY_TIME = re.compile(
    rf"(?<![\d:])(?:[01]\d|2[0-3]):[0-5]\d(?![\d:])(?!(?:\s*){_MERIDIEM}\b)",
    re.IGNORECASE,
)
_CONVERTIBLE_24_HOUR_TIME = re.compile(
    rf"(?<![\d:Tt])(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)"
    rf"(?![\d:])(?!(?:\s*){_MERIDIEM}\b)",
    re.IGNORECASE,
)
_INVALID_MERIDIEM_TIME = re.compile(
    rf"(?<![\d:])(?:00|1[3-9]|2[0-3]):[0-5]\d(?![\d:])(?:\s*){_MERIDIEM}\b",
    re.IGNORECASE,
)
_GUARANTEED_MEDIA = re.compile(
    r"\b(?:scheduled|guaranteed)(?:\s+(?:for|to(?:\s+be)?))?\s+"
    r"(?:an?\s+)?(?:imports?|downloads?|imported|downloaded)\b|"
    r"\b(?:set\s+to|will)\s+(?:be\s+)?(?:imported|downloaded)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_STORAGE_REASSURANCE = re.compile(
    r"\b(?:no|without)\s+(?:immediate\s+)?risk(?:\s+of\s+exhaustion)?\b|"
    r"\bnot\s+at\s+risk\b",
    re.IGNORECASE,
)


def _summary_policy_violation(summary: str, forecast_days: float | None) -> str | None:
    if _MILITARY_TIME.search(summary) or _INVALID_MERIDIEM_TIME.search(summary):
        return "24_hour_time"
    if _GUARANTEED_MEDIA.search(summary):
        return "guaranteed_media"
    if forecast_days is None and _UNSUPPORTED_STORAGE_REASSURANCE.search(summary):
        return "unsupported_storage_reassurance"
    return None


def _summary_policy_compliant(summary: str, forecast_days: float | None) -> bool:
    return _summary_policy_violation(summary, forecast_days) is None


def _normalize_summary_times(summary: str) -> str:
    """Convert unambiguous bare 24-hour clock values without touching ISO timestamps."""

    def replace(match: re.Match[str]) -> str:
        hour = int(match.group("hour"))
        minute = match.group("minute")
        suffix = "AM" if hour < 12 else "PM"
        display_hour = hour % 12 or 12
        return f"{display_hour}:{minute} {suffix}"

    return _CONVERTIBLE_24_HOUR_TIME.sub(replace, summary)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _provider_identity(config: dict[str, Any]) -> tuple[str, str]:
    return str(config.get("provider", "disabled")), str(config.get("model", ""))


def _interactive_ai_busy(db: Session) -> bool:
    return (
        db.scalar(select(AIJob.id).where(AIJob.status.in_(_ACTIVE_AI_JOB_STATUSES)).limit(1))
        is not None
    )


def _failure_retry_pending(db: Session, config: dict[str, Any], now: datetime) -> bool:
    provider, model = _provider_identity(config)
    latest_success = db.scalar(
        select(Event)
        .where(Event.event_type == "sense_dashboard_summary")
        .order_by(desc(Event.timestamp))
        .limit(1)
    )
    query = (
        select(Event)
        .where(Event.event_type == DASHBOARD_FAILURE_EVENT)
        .order_by(desc(Event.timestamp))
        .limit(_FAILURE_STREAK_LIMIT)
    )
    if latest_success is not None:
        query = query.where(Event.timestamp > latest_success.timestamp)
    failures = list(db.scalars(query))
    if not failures:
        return False
    latest = failures[0]
    if (latest.data.get("provider"), latest.data.get("model")) != (provider, model):
        return False
    matching_failures = [
        failure
        for failure in failures
        if (failure.data.get("provider"), failure.data.get("model")) == (provider, model)
    ]
    exponent = max(0, len(matching_failures) - 1)
    delay_seconds = min(
        FAILURE_RETRY_BASE.total_seconds() * (2**exponent),
        FAILURE_RETRY_MAX.total_seconds(),
    )
    return now - _aware(latest.timestamp) < timedelta(seconds=delay_seconds)


def record_dashboard_summary_failure(
    db: Session,
    config: dict[str, Any],
    exc: Exception,
    now: datetime | None = None,
) -> Event:
    """Persist a safe failure category so repeated model failures can back off."""
    if isinstance(exc, httpx.TimeoutException):
        reason = "timeout"
    elif isinstance(exc, httpx.RemoteProtocolError):
        reason = "protocol"
    elif isinstance(exc, ValueError) and "presentation policy" in str(exc):
        category = str(exc).rsplit(": ", 1)[-1]
        allowed = {"24_hour_time", "guaranteed_media", "unsupported_storage_reassurance"}
        reason = f"policy_{category}" if category in allowed else "policy"
    elif isinstance(exc, ValueError):
        reason = "invalid_response"
    elif isinstance(exc, httpx.HTTPStatusError):
        reason = "http_error"
    else:
        reason = "provider_error"
    provider, model = _provider_identity(config)
    event = Event(
        timestamp=now or datetime.now(UTC),
        event_type=DASHBOARD_FAILURE_EVENT,
        severity="warning",
        title="Dashboard summary refresh failed",
        message=f"Dashboard summary provider failure category: {reason}",
        data={"source": "internal", "provider": provider, "model": model, "reason": reason},
    )
    db.add(event)
    return event


def latest_dashboard_summary(db: Session, now: datetime | None = None) -> Event | None:
    now = now or datetime.now(UTC)
    event = db.scalar(
        select(Event)
        .where(Event.event_type == "sense_dashboard_summary")
        .order_by(desc(Event.timestamp))
    )
    storage = latest_storage_sample(db)
    if storage and event and event.data.get("storage_source") != storage.source:
        return None
    if event and not _summary_policy_compliant(
        event.message, event.data.get("storage_forecast_days")
    ):
        return None
    if event and now - _aware(event.timestamp) <= DISPLAY_MAX_AGE:
        return event
    return None


def _refresh_due(db: Session, now: datetime) -> bool:
    latest = db.scalar(
        select(Event)
        .where(Event.event_type == "sense_dashboard_summary")
        .order_by(desc(Event.timestamp))
    )
    if latest is None:
        return True
    storage = latest_storage_sample(db)
    if storage and latest.data.get("storage_source") != storage.source:
        return True
    timestamp = _aware(latest.timestamp)
    age = now - timestamp
    if age >= REFRESH_INTERVAL:
        return True
    if age < MINIMUM_REFRESH_INTERVAL:
        return False
    new_alert = db.scalar(
        select(Alert.id).where(Alert.created_at > timestamp).order_by(desc(Alert.created_at))
    )
    new_media = db.scalar(
        select(MediaActivity.id)
        .where(MediaActivity.occurred_at > timestamp)
        .order_by(desc(MediaActivity.occurred_at))
    )
    return new_alert is not None or new_media is not None


def _latest_snapshot(db: Session, model: type[DiskSample] | type[DockerSample]) -> list[Any]:
    timestamp = db.scalar(select(model.timestamp).order_by(desc(model.timestamp)))
    return list(db.scalars(select(model).where(model.timestamp == timestamp))) if timestamp else []


def _facts(db: Session) -> dict[str, Any] | None:
    forecast = storage_forecast(db, {})
    if forecast["current"] is None:
        return None
    metric = db.scalar(select(MetricSample).order_by(desc(MetricSample.timestamp)))
    disks = _latest_snapshot(db, DiskSample)
    containers = _latest_snapshot(db, DockerSample)
    alerts = list(
        db.scalars(
            select(Alert)
            .where(Alert.active.is_(True), Alert.dismissed_at.is_(None))
            .order_by(desc(Alert.created_at))
            .limit(5)
        )
    )
    media = media_activity_summary(db, {"days": 7})
    media_instances = media.get("instances", {})
    bounded_instances = dict(sorted(media_instances.items())[:20])
    media["instances"] = bounded_instances
    media["omitted_instance_count"] = max(0, len(media_instances) - len(bounded_instances))
    timezone = time_zone_details(db)
    upcoming = upcoming_media(db, {"days": 1, "limit": 30})
    upcoming = {
        "display_timezone": upcoming["display_timezone"],
        "time_format": "12-hour with AM/PM",
        "items": [
            {
                key: value
                for key, value in item.items()
                if key not in {"scheduled_at", "scheduled_at_local"}
            }
            for item in upcoming["items"]
        ],
        "terminology_note": upcoming["terminology_note"],
    }
    return {
        "current_local_time": format_local_datetime(db),
        "display_timezone": timezone.name,
        "storage": forecast,
        "resources": {
            "cpu_percent": metric.cpu_percent if metric else None,
            "memory_percent": metric.memory_percent if metric else None,
            "load_1m": metric.load_1m if metric else None,
        },
        "disks": [
            {
                "name": row.name,
                "temperature_c": row.temperature_c,
                "smart_status": row.smart_status,
            }
            for row in disks[:32]
        ],
        "containers": [
            {"name": row.name, "status": row.status, "health": row.health}
            for row in containers[:64]
        ],
        "active_alerts": [
            {
                "severity": row.severity,
                "title": row.title,
                "message": row.message,
            }
            for row in alerts
        ],
        "media_activity_7_days": media,
        "upcoming_media_24_hours": upcoming,
    }


def refresh_dashboard_summary(
    db: Session, config: dict[str, Any], now: datetime | None = None
) -> Event | None:
    now = now or datetime.now(UTC)
    provider = str(config.get("provider", "disabled"))
    model = str(config.get("model", ""))
    if (
        not config.get("dashboard_summaries", False)
        or provider == "disabled"
        or not model
        or _interactive_ai_busy(db)
        or _failure_retry_pending(db, config, now)
        or not _refresh_due(db, now)
    ):
        return None
    if provider not in {"ollama", "openai_compatible"}:
        raise ValueError("Unsupported AI provider")
    endpoint = validate_http_url(str(config.get("endpoint", ""))).rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("AI endpoint must use HTTP or HTTPS")
    facts = _facts(db)
    if facts is None:
        return None
    # Facts are detached plain values. End the read transaction before a potentially
    # long provider call so a monitoring write cannot enter SQLite's pending state
    # and block new dashboard readers behind this worker.
    db.rollback()
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    # AISettings constrains this value for normal writes. Keep the defensive
    # bounds here as well for legacy or directly seeded configuration rows.
    # Dashboard generation is one complete inference, so it uses the same
    # configurable hard runtime as interactive SENSE inference.
    timeout = min(max(float(config.get("max_runtime_seconds", 300)), 30.0), 3600.0)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Normalized ServerSense facts follow as JSON data:\n"
                + json.dumps(facts, default=str),
            },
        ],
        "temperature": min(float(config.get("temperature", 0.2)), 0.3),
        "max_tokens": min(max(int(config.get("max_output_tokens", 512)), 64), 4096),
    }
    if provider == "ollama":
        # Dashboard summaries need only a short final answer. Disabling the
        # separate reasoning trace prevents thinking-capable models from
        # consuming the output budget before message.content is produced.
        payload["reasoning_effort"] = "none"
    response = http_requests.post(
        f"{endpoint}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"].get("content")
    if not isinstance(content, str):
        raise ValueError("SENSE returned an invalid dashboard summary")
    summary = _normalize_summary_times(" ".join(content.replace("\x00", "").split()).strip())
    if not summary:
        raise ValueError("SENSE returned an empty dashboard summary")
    forecast_days = next(
        (
            item["days_remaining"]
            for item in facts["storage"]["forecasts"]
            if item["window_days"] == 30
        ),
        None,
    )
    policy_violation = _summary_policy_violation(summary, forecast_days)
    if policy_violation:
        raise ValueError(
            "SENSE returned a dashboard summary that violates presentation policy: "
            f"{policy_violation}"
        )
    active_severities = [item["severity"] for item in facts["active_alerts"]]
    severity = (
        "critical"
        if "critical" in active_severities
        else "warning"
        if "warning" in active_severities
        else "info"
    )
    event = Event(
        timestamp=now,
        event_type="sense_dashboard_summary",
        severity=severity,
        title="Current server summary",
        message=summary[:1200],
        data={
            "source": "model",
            "provider": provider,
            "model": model,
            "storage_source": facts["storage"]["current"]["measurement_source"],
            "storage_forecast_days": forecast_days,
        },
    )
    db.add(event)
    db.commit()
    return event
