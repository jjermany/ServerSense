from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from serversense.models import (
    Alert,
    DiskSample,
    DockerSample,
    MediaActivity,
    MetricSample,
    Setting,
    StorageSample,
)
from serversense.services.forecasting import calculate_all
from serversense.services.metrics import calculate_network_rates
from serversense.services.permissions import ActionRequest, ActionRisk, policy

ToolHandler = Callable[[Session, dict[str, Any]], dict[str, Any]]


def _latest_by(rows: list[Any], attribute: str) -> list[Any]:
    if not rows:
        return []
    latest_timestamp = rows[0].timestamp
    result: dict[str, Any] = {}
    for row in rows:
        if row.timestamp == latest_timestamp:
            result.setdefault(str(getattr(row, attribute)), row)
    return list(result.values())


def _elapsed_since(value: datetime | None) -> int | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return max(0, int((datetime.now(UTC) - aware).total_seconds()))


def server_overview(db: Session, _: dict[str, Any]) -> dict[str, Any]:
    storage = db.scalar(select(StorageSample).order_by(desc(StorageSample.timestamp)))
    metrics = list(db.scalars(select(MetricSample).order_by(desc(MetricSample.timestamp)).limit(2)))
    metric = metrics[0] if metrics else None
    network = calculate_network_rates(metrics[1] if len(metrics) > 1 else None, metric)
    state = db.get(Setting, "monitoring_state")
    return {
        "array_status": state.value.get("array_status")
        if state
        else "started"
        if storage
        else "unknown",
        "platform_state": state.value if state else {},
        "storage": {
            "total_bytes": storage.total_bytes,
            "used_bytes": storage.used_bytes,
            "free_bytes": storage.free_bytes,
        }
        if storage
        else None,
        "resources": {
            "cpu_percent": metric.cpu_percent,
            "memory_percent": metric.memory_percent,
            "load_1m": metric.load_1m,
            "network_rx_bytes_per_second": network["rx_bytes_per_second"],
            "network_tx_bytes_per_second": network["tx_bytes_per_second"],
            "network_sample_interval_seconds": network["sample_interval_seconds"],
        }
        if metric
        else None,
    }


def pools(db: Session, _: dict[str, Any]) -> dict[str, Any]:
    state = db.get(Setting, "monitoring_state")
    value = state.value.get("pools", []) if state else []
    return {"pools": value if isinstance(value, list) else []}


def storage_history(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    days = min(max(int(args.get("days", 30)), 1), 3650)
    rows = list(
        db.scalars(
            select(StorageSample)
            .where(StorageSample.timestamp >= datetime.now(UTC) - timedelta(days=days))
            .order_by(StorageSample.timestamp)
        )
    )
    return {
        "days": days,
        "samples": [
            {
                "timestamp": x.timestamp.isoformat(),
                "total_bytes": x.total_bytes,
                "used_bytes": x.used_bytes,
                "free_bytes": x.free_bytes,
            }
            for x in rows[-500:]
        ],
    }


def storage_forecast(db: Session, _: dict[str, Any]) -> dict[str, Any]:
    rows = list(db.scalars(select(StorageSample).order_by(StorageSample.timestamp)))
    latest = rows[-1] if rows else None
    return {
        "current": {
            "total_bytes": latest.total_bytes,
            "used_bytes": latest.used_bytes,
            "free_bytes": latest.free_bytes,
        }
        if latest
        else None,
        "forecasts": [
            item.__dict__
            | {
                "exhaustion_date": item.exhaustion_date.isoformat()
                if item.exhaustion_date
                else None
            }
            for item in calculate_all(rows)
        ],
    }


def disks(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    rows = _latest_by(
        list(db.scalars(select(DiskSample).order_by(desc(DiskSample.timestamp)))), "disk_id"
    )
    disk_id = args.get("disk_id")
    if disk_id:
        rows = [row for row in rows if row.disk_id == disk_id]
    return {
        "disks": [
            {
                "id": x.disk_id,
                "name": x.name,
                "role": x.role,
                "manufacturer": x.manufacturer,
                "model": x.model,
                "interface": x.interface,
                "total_bytes": x.total_bytes,
                "used_bytes": x.used_bytes,
                "temperature_c": x.temperature_c,
                "smart_status": x.smart_status,
                "smart_attributes": x.smart_attributes,
            }
            for x in rows
        ]
    }


def containers(db: Session, _: dict[str, Any]) -> dict[str, Any]:
    rows = _latest_by(
        list(db.scalars(select(DockerSample).order_by(desc(DockerSample.timestamp)))),
        "container_id",
    )
    return {
        "containers": [
            {
                "name": x.name,
                "image": x.image,
                "status": x.status,
                "health": x.health,
                "uptime_seconds": _elapsed_since(x.started_at),
                "cpu_percent": x.cpu_percent,
                "memory_bytes": x.memory_bytes,
                "restart_count": x.restart_count,
            }
            for x in rows
        ]
    }


def recent_alerts(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(args.get("limit", 20)), 1), 100)
    rows = list(db.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(limit)))
    return {
        "alerts": [
            {
                "severity": x.severity,
                "title": x.title,
                "message": x.message,
                "active": x.active,
                "timestamp": x.created_at.isoformat(),
            }
            for x in rows
        ]
    }


def _media_rows(db: Session, args: dict[str, Any]) -> tuple[int, list[MediaActivity]]:
    days = min(max(int(args.get("days", 30)), 1), 365)
    query = select(MediaActivity).where(
        MediaActivity.occurred_at >= datetime.now(UTC) - timedelta(days=days)
    )
    if args.get("provider"):
        query = query.where(MediaActivity.provider == args["provider"])
    if args.get("instance"):
        query = query.where(MediaActivity.instance_name == args["instance"])
    if args.get("event_type"):
        query = query.where(MediaActivity.event_type == args["event_type"])
    return days, list(db.scalars(query.order_by(desc(MediaActivity.occurred_at))))


def media_activity_summary(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    days, rows = _media_rows(db, args)
    instances: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = instances.setdefault(
            row.instance_name,
            {
                "provider": row.provider,
                "events": {},
                "known_import_bytes": 0,
                "explicit_upgrades": 0,
            },
        )
        events: dict[str, int] = group["events"]
        events[row.event_type] = events.get(row.event_type, 0) + 1
        if row.event_type == "imported" and row.bytes is not None:
            group["known_import_bytes"] += row.bytes
        if row.event_type == "imported" and row.is_upgrade:
            group["explicit_upgrades"] += 1
    cutoff = datetime.now(UTC) - timedelta(days=days)
    storage = list(
        db.scalars(
            select(StorageSample)
            .where(StorageSample.timestamp >= cutoff)
            .order_by(StorageSample.timestamp)
        )
    )
    measured_change = storage[-1].used_bytes - storage[0].used_bytes if len(storage) >= 2 else None
    return {
        "days": days,
        "instances": instances,
        "measured_storage_change_bytes": measured_change,
        "evidence_note": (
            "Import sizes are gross media events and do not prove net storage growth; "
            "hardlinks, replacements, deletions, and incomplete size fields can differ. "
            "Upgrade counts include only events explicitly marked as upgrades by the provider."
        ),
    }


def media_activity_items(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    days, rows = _media_rows(db, args)
    limit = min(max(int(args.get("limit", 25)), 1), 100)
    return {
        "days": days,
        "activities": [
            {
                "timestamp": row.occurred_at.isoformat(),
                "provider": row.provider,
                "instance": row.instance_name,
                "event_type": row.event_type,
                "media_type": row.media_type,
                "title": row.title,
                "series": row.parent_title,
                "season": row.season_number,
                "episode": row.episode_number,
                "quality": row.quality,
                "bytes": row.bytes,
                "explicit_upgrade": row.is_upgrade,
            }
            for row in rows[:limit]
        ],
    }


TOOLS: dict[str, tuple[str, dict[str, Any], ToolHandler]] = {
    "get_server_overview": (
        "Get current array, capacity, and resource summary.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        server_overview,
    ),
    "get_array_status": (
        "Get current array status and capacity.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        server_overview,
    ),
    "get_array_capacity": (
        "Get current array capacity.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        server_overview,
    ),
    "get_storage_history": (
        "Get measured storage history.",
        {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 3650}},
            "additionalProperties": False,
        },
        storage_history,
    ),
    "get_storage_growth_rate": (
        "Get deterministic 7/30/90-day storage rates.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        storage_forecast,
    ),
    "get_storage_forecast": (
        "Get deterministic storage exhaustion forecasts.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        storage_forecast,
    ),
    "get_pool_status": (
        "Get normalized Unraid pool capacity, devices, and status.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        pools,
    ),
    "get_disk_list": (
        "List current physical disks and health.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        disks,
    ),
    "get_disk_details": (
        "Get one disk's details.",
        {
            "type": "object",
            "properties": {"disk_id": {"type": "string", "maxLength": 120}},
            "required": ["disk_id"],
            "additionalProperties": False,
        },
        disks,
    ),
    "get_disk_smart_health": (
        "Get SMART health for disks.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        disks,
    ),
    "get_disk_temperature_history": (
        "Get available disk temperatures.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        disks,
    ),
    "get_container_status": (
        "Get current Docker container states and health.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        containers,
    ),
    "get_recent_alerts": (
        "Get recent ServerSense alerts.",
        {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            "additionalProperties": False,
        },
        recent_alerts,
    ),
    "get_system_resources": (
        "Get current CPU, memory, load, and measured network transfer rates.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        server_overview,
    ),
    "get_media_activity_summary": (
        "Summarize normalized Sonarr/Radarr activity by configurable instance. Use this to explain recent storage changes, downloads, imports, or upgrades; heed its evidence note.",
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
                "provider": {"type": "string", "enum": ["sonarr", "radarr"]},
                "instance": {"type": "string", "maxLength": 160},
            },
            "additionalProperties": False,
        },
        media_activity_summary,
    ),
    "get_media_activity_items": (
        "List bounded normalized Sonarr/Radarr activity with titles and source instance. Use after a summary when the user asks which items.",
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
                "provider": {"type": "string", "enum": ["sonarr", "radarr"]},
                "instance": {"type": "string", "maxLength": 160},
                "event_type": {
                    "type": "string",
                    "enum": [
                        "grabbed",
                        "imported",
                        "download_failed",
                        "file_deleted",
                        "file_renamed",
                    ],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        media_activity_items,
    ),
}


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": name, "description": value[0], "parameters": value[1]},
        }
        for name, value in TOOLS.items()
    ]


def _validate_arguments(name: str, arguments: dict[str, Any]) -> None:
    schema = TOOLS[name][1]
    properties = schema.get("properties", {})
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError(f"Unexpected argument for {name}: {sorted(unknown)[0]}")
    for required in schema.get("required", []):
        if required not in arguments:
            raise ValueError(f"Missing required argument for {name}: {required}")
    for key, value in arguments.items():
        rule = properties[key]
        if rule.get("type") == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"Argument must be an integer: {key}")
            if value < rule.get("minimum", value) or value > rule.get("maximum", value):
                raise ValueError(f"Argument is outside the permitted range: {key}")
        if rule.get("type") == "string":
            if not isinstance(value, str):
                raise ValueError(f"Argument must be a string: {key}")
            if len(value) > rule.get("maxLength", len(value)):
                raise ValueError(f"Argument is too long: {key}")
            if "enum" in rule and value not in rule["enum"]:
                raise ValueError(f"Argument is not an allowed value: {key}")


def execute_tool(db: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        raise ValueError(f"Tool is not permitted: {name}")
    _validate_arguments(name, arguments)
    policy.authorize(ActionRequest(principal="sense", action=name, risk=ActionRisk.READ_ONLY))
    return TOOLS[name][2](db, arguments)
