from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from serversense.config import get_settings
from serversense.db import get_db
from serversense.models import (
    Alert,
    DiskSample,
    DockerSample,
    Event,
    MetricSample,
    Setting,
    StorageSample,
    User,
)
from serversense.schemas import DashboardResponse, ForecastResponse, ForecastWindow, StoragePoint
from serversense.security import current_user
from serversense.services.collectors import build_collector
from serversense.services.forecasting import calculate_all
from serversense.services.maintenance import create_backup, diagnostic_bundle
from serversense.services.metrics import calculate_network_rates

router = APIRouter(prefix="/api", tags=["monitoring"], dependencies=[Depends(current_user)])


@router.get("/system/detect")
def detect_system() -> dict[str, Any]:
    return build_collector(get_settings()).detect()


@router.get("/system/diagnostics")
def download_diagnostics(db: Session = Depends(get_db)) -> Response:
    return Response(
        diagnostic_bundle(db),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=serversense-diagnostics.zip"},
    )


@router.post("/system/backup")
def backup_database() -> dict[str, str]:
    path = create_backup()
    return {"ok": "true", "filename": path.name}


def latest_per_key(rows: list[Any], key: str) -> list[Any]:
    if not rows:
        return []
    latest_timestamp = rows[0].timestamp
    found: dict[str, Any] = {}
    for row in rows:
        if row.timestamp == latest_timestamp:
            found.setdefault(str(getattr(row, key)), row)
    return list(found.values())


def elapsed_since(value: datetime | None) -> int | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return max(0, int((datetime.now(UTC) - aware).total_seconds()))


def container_change_times(rows: list[DockerSample]) -> dict[str, datetime | None]:
    grouped: dict[str, list[DockerSample]] = {}
    for row in rows:
        grouped.setdefault(row.container_id, []).append(row)
    result: dict[str, datetime | None] = {}
    for container_id, samples in grouped.items():
        latest = samples[0]
        changed = next(
            (
                item
                for item in samples[1:]
                if (item.status, item.health, item.restart_count)
                != (latest.status, latest.health, latest.restart_count)
            ),
            None,
        )
        result[container_id] = latest.timestamp if changed else None
    return result


def get_storage_forecast(db: Session) -> ForecastResponse:
    samples = list(db.scalars(select(StorageSample).order_by(StorageSample.timestamp)))
    if not samples:
        raise HTTPException(404, "No storage samples are available")
    forecasts = calculate_all(samples)
    latest = samples[-1]
    valid = [item for item in forecasts if item.days_remaining is not None]
    preferred = next(
        (item for item in valid if item.window_days == 30), valid[0] if valid else None
    )
    return ForecastResponse(
        current_total_bytes=latest.total_bytes,
        current_used_bytes=latest.used_bytes,
        current_free_bytes=latest.free_bytes,
        forecasts=[ForecastWindow(**item.__dict__) for item in forecasts],
        recommended_window_days=preferred.window_days if preferred else None,
    )


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db)) -> DashboardResponse:
    storage = db.scalar(select(StorageSample).order_by(desc(StorageSample.timestamp)))
    metrics = list(db.scalars(select(MetricSample).order_by(desc(MetricSample.timestamp)).limit(2)))
    metric = metrics[0] if metrics else None
    network = calculate_network_rates(metrics[1] if len(metrics) > 1 else None, metric)
    disks = latest_per_key(
        list(db.scalars(select(DiskSample).order_by(desc(DiskSample.timestamp)))), "disk_id"
    )
    docker_rows = list(db.scalars(select(DockerSample).order_by(desc(DockerSample.timestamp))))
    containers = latest_per_key(docker_rows, "container_id")
    container_changes = container_change_times(docker_rows)
    alerts = list(
        db.scalars(
            select(Alert).where(Alert.active.is_(True)).order_by(desc(Alert.created_at)).limit(10)
        )
    )
    general = db.get(Setting, "general")
    general_value = general.value if general else {}
    monitoring = db.get(Setting, "monitoring_state")
    monitoring_value = monitoring.value if monitoring else {}
    forecast = get_storage_forecast(db) if storage else None
    selected = next(
        (item for item in (forecast.forecasts if forecast else []) if item.window_days == 30), None
    )
    insights: list[dict] = []
    active_alert_ids = {alert.id for alert in alerts}
    explanation = next(
        (
            event
            for event in db.scalars(
                select(Event)
                .where(Event.event_type == "sense_alert_explanation")
                .order_by(desc(Event.timestamp))
                .limit(20)
            )
            if active_alert_ids.intersection(event.data.get("alert_ids", []))
        ),
        None,
    )
    if explanation:
        insights.append(
            {
                "severity": explanation.severity,
                "title": explanation.title,
                "message": explanation.message,
                "source": "sense",
                "model": explanation.data.get("model"),
            }
        )
    if selected and selected.days_remaining is not None:
        insights.append(
            {
                "severity": "warning" if selected.days_remaining < 180 else "info",
                "title": "Storage trajectory",
                "message": f"At the measured 30-day growth rate, capacity is projected to last approximately {selected.days_remaining:.0f} days.",
                "source": "deterministic",
            }
        )
    hot = max(
        (item for item in disks if item.temperature_c is not None),
        key=lambda item: item.temperature_c or -1,
        default=None,
    )
    if hot:
        insights.append(
            {
                "severity": "warning" if (hot.temperature_c or 0) >= 45 else "info",
                "title": "Disk temperatures",
                "message": f"{hot.name} is currently the hottest drive at {hot.temperature_c:.0f}°C.",
                "source": "deterministic",
            }
        )
    return DashboardResponse(
        server={
            "name": general_value.get("server_name", "ServerSense Host"),
            "array_status": monitoring_value.get(
                "array_status", "started" if storage else "unknown"
            ),
            "uptime_seconds": metric.uptime_seconds if metric else None,
            "parity": {
                key: monitoring_value.get(key)
                for key in (
                    "parity_action",
                    "parity_position",
                    "parity_size",
                    "parity_errors",
                    "disabled_disks",
                )
            },
            "ups": monitoring_value.get("ups"),
            "pools": monitoring_value.get("pools", []),
        },
        storage={
            "total_bytes": storage.total_bytes if storage else 0,
            "used_bytes": storage.used_bytes if storage else 0,
            "free_bytes": storage.free_bytes if storage else 0,
            "days_remaining": selected.days_remaining if selected else None,
            "growth_bytes_per_day": selected.bytes_per_day if selected else None,
        },
        system={
            "cpu_percent": metric.cpu_percent if metric else None,
            "memory_percent": metric.memory_percent if metric else None,
            "load_1m": metric.load_1m if metric else None,
            "network_rx_bytes_per_second": network["rx_bytes_per_second"],
            "network_tx_bytes_per_second": network["tx_bytes_per_second"],
            "network_sample_interval_seconds": network["sample_interval_seconds"],
        },
        disks=[
            {
                "id": x.disk_id,
                "name": x.name,
                "role": x.role,
                "manufacturer": x.manufacturer,
                "model": x.model,
                "serial": x.serial,
                "interface": x.interface,
                "total_bytes": x.total_bytes,
                "used_bytes": x.used_bytes,
                "temperature_c": x.temperature_c,
                "smart_status": x.smart_status,
                "smart_attributes": x.smart_attributes,
            }
            for x in disks
        ],
        containers=[
            {
                "id": x.container_id,
                "name": x.name,
                "image": x.image,
                "status": x.status,
                "health": x.health,
                "started_at": x.started_at,
                "uptime_seconds": elapsed_since(x.started_at),
                "last_state_change": container_changes.get(x.container_id),
                "cpu_percent": x.cpu_percent,
                "memory_bytes": x.memory_bytes,
                "restart_count": x.restart_count,
            }
            for x in containers
        ],
        alerts=[
            {
                "id": x.id,
                "severity": x.severity,
                "type": x.alert_type,
                "title": x.title,
                "message": x.message,
                "created_at": x.created_at,
            }
            for x in alerts
        ],
        insights=insights,
        demo_mode=bool(general_value.get("demo_mode", False)),
    )


@router.get("/storage/history", response_model=list[StoragePoint])
def storage_history(
    range: str = Query(default="30d", pattern="^(24h|7d|30d|90d|1y|all)$"),
    db: Session = Depends(get_db),
) -> list[StoragePoint]:
    durations = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
        "1y": timedelta(days=365),
    }
    statement = select(StorageSample).order_by(StorageSample.timestamp)
    if range != "all":
        statement = statement.where(StorageSample.timestamp >= datetime.now(UTC) - durations[range])
    return [
        StoragePoint(
            timestamp=x.timestamp,
            total_bytes=x.total_bytes,
            used_bytes=x.used_bytes,
            free_bytes=x.free_bytes,
        )
        for x in db.scalars(statement)
    ]


@router.get("/storage/forecast", response_model=ForecastResponse)
def storage_forecast(db: Session = Depends(get_db)) -> ForecastResponse:
    return get_storage_forecast(db)


@router.get("/storage/pools")
def storage_pools(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    state = db.get(Setting, "monitoring_state")
    pools = state.value.get("pools", []) if state else []
    return pools if isinstance(pools, list) else []


@router.get("/disks")
def disk_list(db: Session = Depends(get_db)) -> list[dict]:
    rows = latest_per_key(
        list(db.scalars(select(DiskSample).order_by(desc(DiskSample.timestamp)))), "disk_id"
    )
    return [
        {
            "id": x.disk_id,
            "name": x.name,
            "role": x.role,
            "manufacturer": x.manufacturer,
            "model": x.model,
            "serial": x.serial,
            "interface": x.interface,
            "total_bytes": x.total_bytes,
            "used_bytes": x.used_bytes,
            "temperature_c": x.temperature_c,
            "smart_status": x.smart_status,
            "smart_attributes": x.smart_attributes,
        }
        for x in rows
    ]


@router.get("/disks/{disk_id}")
def disk_details(disk_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.scalar(
        select(DiskSample).where(DiskSample.disk_id == disk_id).order_by(desc(DiskSample.timestamp))
    )
    if not row:
        raise HTTPException(404, "Disk not found")
    history = list(
        db.scalars(
            select(DiskSample)
            .where(DiskSample.disk_id == disk_id)
            .order_by(DiskSample.timestamp)
            .limit(500)
        )
    )
    return {
        "id": row.disk_id,
        "name": row.name,
        "role": row.role,
        "manufacturer": row.manufacturer,
        "model": row.model,
        "serial": row.serial,
        "interface": row.interface,
        "total_bytes": row.total_bytes,
        "used_bytes": row.used_bytes,
        "temperature_c": row.temperature_c,
        "smart_status": row.smart_status,
        "smart_attributes": row.smart_attributes,
        "temperature_history": [
            {"timestamp": x.timestamp, "temperature_c": x.temperature_c} for x in history
        ],
    }


@router.get("/docker")
def docker_list(db: Session = Depends(get_db)) -> list[dict]:
    history = list(db.scalars(select(DockerSample).order_by(desc(DockerSample.timestamp))))
    rows = latest_per_key(history, "container_id")
    changes = container_change_times(history)
    return [
        {
            "id": x.container_id,
            "name": x.name,
            "image": x.image,
            "status": x.status,
            "health": x.health,
            "started_at": x.started_at,
            "uptime_seconds": elapsed_since(x.started_at),
            "last_state_change": changes.get(x.container_id),
            "cpu_percent": x.cpu_percent,
            "memory_bytes": x.memory_bytes,
            "restart_count": x.restart_count,
        }
        for x in rows
    ]


@router.get("/alerts")
def alert_list(active: bool | None = None, db: Session = Depends(get_db)) -> list[dict]:
    statement = select(Alert).order_by(desc(Alert.created_at)).limit(200)
    if active is not None:
        statement = statement.where(Alert.active == active)
    return [
        {
            "id": x.id,
            "type": x.alert_type,
            "severity": x.severity,
            "title": x.title,
            "message": x.message,
            "active": x.active,
            "acknowledged_at": x.acknowledged_at,
            "created_at": x.created_at,
            "data": x.data,
        }
        for x in db.scalars(statement)
    ]


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    alert.acknowledged_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}
