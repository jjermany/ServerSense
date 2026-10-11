import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from serversense.models import (
    AIJob,
    Alert,
    DiskSample,
    DockerSample,
    Event,
    MediaActivity,
    MetricSample,
    Setting,
)
from serversense.services import codex, http_requests
from serversense.services.model_fallback import complete_with_fallback
from serversense.services.storage import latest_storage_sample
from serversense.services.timezones import format_local_datetime, time_zone_details
from serversense.services.tools import media_activity_summary, storage_forecast, upcoming_media
from serversense.services.urls import validate_http_url

REFRESH_INTERVAL = timedelta(hours=6)
MINIMUM_REFRESH_INTERVAL = timedelta(minutes=15)
DISPLAY_MAX_AGE = timedelta(hours=12)
DASHBOARD_FAILURE_EVENT = "sense_dashboard_summary_failure"
REFRESH_STATE_KEY = "dashboard_summary_refresh"
FAILURE_RETRY_BASE = timedelta(minutes=15)
FAILURE_RETRY_MAX = timedelta(hours=6)
_FAILURE_STREAK_LIMIT = 6
_ACTIVE_AI_JOB_STATUSES = {"queued", "gathering_context", "analyzing", "streaming"}
_FAILURE_GUIDANCE = {
    "provider_error": "The provider failed without a specific safe category. Check the saved model and account in SENSE AI, then retry.",
    "subscription_exhausted": "Codex reported exhausted subscription allowance. Check the provider-reported reset times in SENSE AI before retrying.",
    "rate_limited": "The provider temporarily rate limited this request. Retry later; this does not confirm exhausted subscription allowance.",
    "authentication": "The provider rejected authentication or model access. Check the account or saved credentials in SENSE AI.",
    "runtime_unavailable": "The packaged Codex runtime is unavailable. Rebuild or update the ServerSense image.",
    "connection": "The provider connection failed or disconnected. Check provider availability and retry.",
    "protocol": "The provider returned an invalid protocol response. Check provider compatibility and retry.",
    "context_limit": "Dashboard facts exceeded a context limit. Check both Context window and Maximum AI context in SENSE AI.",
    "output_limit": "Codex exceeded the visible response cap. Check Maximum response tokens in SENSE AI and retry.",
    "empty_response": "The provider completed without a visible summary. Test the saved model and retry.",
    "safety_limit": "A provider stream or capability exceeded a safety boundary. The request was stopped; SENSE remains read-only.",
    "timeout": "The provider timed out. Check the inactivity timeout and maximum runtime in SENSE AI.",
    "invalid_response": "The provider returned an empty or malformed summary. Test the saved model and retry.",
    "http_error": "The provider rejected the request. Check the saved model and endpoint in SENSE AI.",
    "policy_24_hour_time": "The summary was rejected because it used invalid display times. Retry to generate a new summary.",
    "policy_guaranteed_media": "The summary was rejected because it claimed guaranteed media downloads or imports. Retry to generate a new summary.",
    "policy_unsupported_storage_reassurance": "The summary was rejected because its storage-risk claim lacked forecast evidence. Retry to generate a new summary.",
    "policy": "The summary did not meet presentation requirements. Retry to generate a new summary.",
}
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


def _failure_retry_at(db: Session, config: dict[str, Any]) -> datetime | None:
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
        return None
    latest = failures[0]
    if (latest.data.get("provider"), latest.data.get("model")) != (provider, model):
        return None
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
    return _aware(latest.timestamp) + timedelta(seconds=delay_seconds)


def _failure_retry_pending(db: Session, config: dict[str, Any], now: datetime) -> bool:
    retry_at = _failure_retry_at(db, config)
    return retry_at is not None and now < retry_at


def set_dashboard_refresh_state(db: Session, state: str) -> None:
    row = db.get(Setting, REFRESH_STATE_KEY)
    value = dict(row.value) if row else {}
    value.update(state=state, updated_at=datetime.now(UTC).isoformat())
    if row is None:
        db.add(Setting(key=REFRESH_STATE_KEY, value=value))
    else:
        row.value = value


def dashboard_summary_status(db: Session, config: dict[str, Any]) -> dict[str, Any]:
    """Explain missing cached text without exposing provider errors or credentials."""
    now = datetime.now(UTC)
    enabled = bool(config.get("dashboard_summaries", False))
    configured = config.get("provider", "disabled") != "disabled" and bool(config.get("model"))
    request = db.get(Setting, REFRESH_STATE_KEY)
    state = request.value.get("state") if request else None
    retry_at = _failure_retry_at(db, config)
    failure = db.scalar(
        select(Event)
        .where(Event.event_type == DASHBOARD_FAILURE_EVENT)
        .order_by(desc(Event.timestamp))
        .limit(1)
    )
    cached = latest_dashboard_summary(db, now)
    if not enabled:
        status, message = (
            "disabled",
            "Dashboard summaries are off. Enable them in Settings → SENSE AI.",
        )
    elif not configured:
        status, message = (
            "unconfigured",
            "Choose and save an AI provider and model in Settings → SENSE AI.",
        )
    elif state == "running":
        status, message = "running", "Generating a fresh summary in the background."
    elif latest_storage_sample(db) is None:
        status, message = (
            "no_data",
            "Waiting for the first storage measurement before generating a summary.",
        )
    elif _interactive_ai_busy(db):
        status, message = (
            "waiting",
            "Summary generation is waiting for interactive SENSE requests to finish.",
        )
    elif state == "queued":
        status, message = (
            "queued",
            "Refresh requested. A fresh summary will appear here when ready.",
        )
    elif retry_at and retry_at > now:
        status, message = (
            "cooldown",
            "The last attempt failed. Automatic retries are paused; you can retry now.",
        )
    elif cached:
        status, message = (
            "ready",
            "Summaries refresh automatically every six hours and after new alerts or media activity.",
        )
    else:
        status, message = (
            "pending",
            "No current summary is available. Generate one now or wait for the next background check.",
        )
        previous = db.scalar(
            select(Event)
            .where(Event.event_type == "sense_dashboard_summary")
            .order_by(desc(Event.timestamp))
            .limit(1)
        )
        if previous and now - _aware(previous.timestamp) > DISPLAY_MAX_AGE:
            message = "The previous summary is over 12 hours old and has expired. Refresh to generate a current summary."
        elif previous:
            message = "The previous summary does not match current storage measurements or presentation requirements. Refresh to generate a current summary."
    return {
        "status": status,
        "message": message,
        "can_refresh": enabled
        and configured
        and status not in {"running", "queued", "no_data"}
        and state != "queued",
        "retry_at": retry_at.isoformat() if status == "cooldown" and retry_at else None,
        "last_failure_category": failure.data.get("reason")
        if status == "cooldown" and failure
        else None,
        "last_failure_at": failure.timestamp.isoformat()
        if status == "cooldown" and failure
        else None,
        "failure_guidance": _FAILURE_GUIDANCE.get(
            str(failure.data.get("reason")), _FAILURE_GUIDANCE["provider_error"]
        )
        if status == "cooldown" and failure
        else None,
        "generated_at": cached.timestamp.isoformat() if cached else None,
    }


def request_dashboard_summary(db: Session, config: dict[str, Any]) -> dict[str, Any]:
    # Serialize concurrent clicks into one durable request, including requests
    # from separate browsers. No inference runs in this request transaction.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    status = dashboard_summary_status(db, config)
    if status["can_refresh"]:
        set_dashboard_refresh_state(db, "queued")
    db.commit()
    return dashboard_summary_status(db, config)


def record_dashboard_summary_failure(
    db: Session,
    config: dict[str, Any],
    exc: Exception,
    now: datetime | None = None,
) -> Event:
    """Persist a safe failure category so repeated model failures can back off."""
    reason: str
    if isinstance(exc, codex.CodexError):
        reason = exc.category
    elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        reason = "timeout"
    elif isinstance(exc, httpx.RemoteProtocolError):
        reason = "protocol"
    elif isinstance(exc, httpx.TransportError):
        reason = "connection"
    elif isinstance(exc, ValueError) and "presentation policy" in str(exc):
        category = str(exc).rsplit(": ", 1)[-1]
        allowed = {"24_hour_time", "guaranteed_media", "unsupported_storage_reassurance"}
        reason = f"policy_{category}" if category in allowed else "policy"
    elif isinstance(exc, (ValueError, KeyError, IndexError, TypeError)):
        reason = "invalid_response"
    elif isinstance(exc, httpx.HTTPStatusError):
        reason = {401: "authentication", 403: "authentication", 429: "rate_limited"}.get(
            exc.response.status_code, "http_error"
        )
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
    # A two/three-sentence dashboard summary needs representative identities,
    # not the interactive change snapshot's full hundred-title evidence list.
    titles = media.get("confirmed_upgrade_titles", [])
    media["confirmed_upgrade_titles"] = titles[:10]
    media["upgrade_titles_truncated"] = (
        bool(media.get("upgrade_titles_truncated")) or len(titles) > 10
    )
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
    db: Session, config: dict[str, Any], now: datetime | None = None, *, force: bool = False
) -> Event | None:
    now = now or datetime.now(UTC)
    provider = str(config.get("provider", "disabled"))
    model = str(config.get("model", ""))
    if (
        not config.get("dashboard_summaries", False)
        or provider == "disabled"
        or not model
        or _interactive_ai_busy(db)
        or (not force and _failure_retry_pending(db, config, now))
        or (not force and not _refresh_due(db, now))
    ):
        return None
    if provider not in {"ollama", "openai_compatible", "codex"}:
        raise ValueError("Unsupported AI provider")
    facts = _facts(db)
    if facts is None:
        return None
    set_dashboard_refresh_state(db, "running")
    db.commit()
    # Facts are detached plain values. End the read transaction before a potentially
    # long provider call so a monitoring write cannot enter SQLite's pending state
    # and block new dashboard readers behind this worker.
    db.rollback()
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
                + json.dumps(facts, default=str, ensure_ascii=False, separators=(",", ":")),
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

    def request(selected: dict[str, Any]) -> Any:
        selected_provider = selected["provider"]
        if selected_provider == "codex":
            return codex.complete(selected, payload["messages"], payload["max_tokens"])
        selected_endpoint = validate_http_url(str(selected.get("endpoint", ""))).rstrip("/")
        selected_headers = {"Content-Type": "application/json"}
        if selected.get("api_key"):
            selected_headers["Authorization"] = f"Bearer {selected['api_key']}"
        selected_payload = payload | {
            "model": selected["model"],
            "temperature": min(float(selected.get("temperature", 0.2)), 0.3),
        }
        selected_payload.pop("reasoning_effort", None)
        if selected_provider == "ollama":
            selected_payload["reasoning_effort"] = "none"
        request_timeout = float(selected.get("max_runtime_seconds", 300))
        response = http_requests.post(
            f"{selected_endpoint}/v1/chat/completions",
            headers=selected_headers,
            json=selected_payload,
            timeout=httpx.Timeout(request_timeout, connect=min(10.0, request_timeout)),
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"].get("content")

    content, selected = complete_with_fallback(config | {"max_runtime_seconds": timeout}, request)
    provider, model = str(selected["provider"]), str(selected["model"])
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
    set_dashboard_refresh_state(db, "completed")
    db.commit()
    return event
