from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from serversense.models import Alert, DiskSample, DockerSample, StorageSample
from serversense.services.forecasting import calculate_forecast

CONTAINER_STOPPED_GRACE_PERIOD = timedelta(minutes=10)
RUNNING_CONTAINER_STATES = {"running", "created"}


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _containers_stopped_beyond_grace_period(
    samples: list[DockerSample],
) -> list[DockerSample]:
    """Return current containers continuously non-running for at least the grace period."""
    if not samples:
        return []
    newest_timestamp = samples[0].timestamp
    current = {
        sample.container_id: sample for sample in samples if sample.timestamp == newest_timestamp
    }
    history: dict[str, list[DockerSample]] = {}
    for sample in samples:
        if sample.container_id in current:
            history.setdefault(sample.container_id, []).append(sample)

    stopped: list[DockerSample] = []
    for container_id, latest in current.items():
        if latest.status.lower() in RUNNING_CONTAINER_STATES:
            continue
        continuous_samples = history[container_id]
        stopped_since = latest.timestamp
        for sample in continuous_samples[1:]:
            if sample.status.lower() in RUNNING_CONTAINER_STATES:
                break
            stopped_since = sample.timestamp
        if _as_utc(latest.timestamp) - _as_utc(stopped_since) >= CONTAINER_STOPPED_GRACE_PERIOD:
            stopped.append(latest)
    return stopped


def _upsert(
    db: Session,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    fingerprint: str,
    data: dict[str, Any],
) -> Alert | None:
    existing = db.scalar(
        select(Alert).where(Alert.fingerprint == fingerprint, Alert.active.is_(True))
    )
    if existing:
        existing.message = message
        existing.severity = severity
        existing.data = data
        return None
    alert = Alert(
        alert_type=alert_type,
        severity=severity,
        title=title,
        message=message,
        fingerprint=fingerprint,
        data=data,
    )
    db.add(alert)
    return alert


def evaluate_alerts(
    db: Session,
    free_percent_threshold: float = 10,
    temperature_threshold: float = 50,
    forecast_days_threshold: int = 90,
) -> list[Alert]:
    created: list[Alert] = []
    storage = list(db.scalars(select(StorageSample).order_by(StorageSample.timestamp)))
    if storage:
        latest = storage[-1]
        free_percent = latest.free_bytes / latest.total_bytes * 100 if latest.total_bytes else 0
        if free_percent < free_percent_threshold:
            alert = _upsert(
                db,
                "storage_low",
                "warning",
                "Array free space is low",
                f"Only {free_percent:.1f}% of array capacity remains free.",
                "storage-low",
                {"free_percent": free_percent},
            )
            if alert:
                created.append(alert)
        forecast = calculate_forecast(storage, 30)
        if (
            forecast.days_remaining is not None
            and forecast.days_remaining < forecast_days_threshold
        ):
            alert = _upsert(
                db,
                "forecast_low",
                "warning",
                "Storage exhaustion approaching",
                f"The measured 30-day trend projects {forecast.days_remaining:.0f} days remaining.",
                "forecast-low",
                {"days_remaining": forecast.days_remaining},
            )
            if alert:
                created.append(alert)
    latest_disks: dict[str, DiskSample] = {}
    for disk in db.scalars(select(DiskSample).order_by(DiskSample.timestamp.desc())):
        latest_disks.setdefault(disk.disk_id, disk)
    for disk in latest_disks.values():
        if disk.smart_status in ("warning", "critical"):
            alert = _upsert(
                db,
                "disk_smart",
                disk.smart_status,
                f"{disk.name} SMART {disk.smart_status}",
                "SMART reports a condition that needs attention.",
                f"smart-{disk.disk_id}",
                {"disk_id": disk.disk_id},
            )
            if alert:
                created.append(alert)
        if disk.temperature_c is not None and disk.temperature_c >= temperature_threshold:
            alert = _upsert(
                db,
                "disk_temperature",
                "warning",
                f"{disk.name} is hot",
                f"Disk temperature is {disk.temperature_c:.0f}°C.",
                f"temperature-{disk.disk_id}",
                {"temperature_c": disk.temperature_c},
            )
            if alert:
                created.append(alert)
    container_samples = list(
        db.scalars(select(DockerSample).order_by(DockerSample.timestamp.desc()))
    )
    for container in _containers_stopped_beyond_grace_period(container_samples):
        alert = _upsert(
            db,
            "container_stopped",
            "warning",
            f"{container.name} is stopped",
            f"Container state has been {container.status} for at least 10 minutes.",
            f"container-{container.container_id}",
            {"container_id": container.container_id},
        )
        if alert:
            created.append(alert)
    db.commit()
    return created
