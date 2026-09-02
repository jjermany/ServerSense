from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from serversense.models import (
    Alert,
    DiskSample,
    DockerSample,
    Integration,
    MediaActivity,
    MediaSchedule,
    MetricSample,
    Setting,
)
from serversense.services.forecasting import calculate_all
from serversense.services.metrics import calculate_network_rates
from serversense.services.permissions import ActionRequest, ActionRisk, policy
from serversense.services.storage import (
    current_storage_samples,
    latest_storage_sample,
    storage_scope,
)
from serversense.services.timezones import local_time, time_zone_details

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
    storage = latest_storage_sample(db)
    metrics = list(db.scalars(select(MetricSample).order_by(desc(MetricSample.timestamp)).limit(2)))
    metric = metrics[0] if metrics else None
    network = calculate_network_rates(metrics[1] if len(metrics) > 1 else None, metric)
    state = db.get(Setting, "monitoring_state")
    platform_state = (
        {key: value for key, value in state.value.items() if key != "pools"} if state else {}
    )
    return {
        "array_status": state.value.get("array_status")
        if state
        else "started"
        if storage
        else "unknown",
        "platform_state": platform_state,
        "storage": {
            "total_bytes": storage.total_bytes,
            "used_bytes": storage.used_bytes,
            "free_bytes": storage.free_bytes,
            "sampled_at": storage.timestamp.isoformat(),
            **storage_scope(storage),
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


def array_capacity(db: Session, _: dict[str, Any]) -> dict[str, Any]:
    return {"storage": server_overview(db, {})["storage"]}


def array_status(db: Session, _: dict[str, Any]) -> dict[str, Any]:
    overview = server_overview(db, {})
    return {
        "array_status": overview["array_status"],
        "platform_state": overview["platform_state"],
        "storage": overview["storage"],
    }


def system_resources(db: Session, _: dict[str, Any]) -> dict[str, Any]:
    return {"resources": server_overview(db, {})["resources"]}


def pools(db: Session, _: dict[str, Any]) -> dict[str, Any]:
    state = db.get(Setting, "monitoring_state")
    value = state.value.get("pools", []) if state else []
    return {
        "scope": "named_pools_separate_from_array_capacity",
        "included_in_array_capacity": False,
        "pools": value if isinstance(value, list) else [],
    }


def storage_history(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    days = min(max(int(args.get("days", 30)), 1), 3650)
    rows = current_storage_samples(db, since=datetime.now(UTC) - timedelta(days=days))
    return {
        "days": days,
        "storage_scope": storage_scope(rows[-1]) if rows else None,
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
    rows = current_storage_samples(db)
    latest = rows[-1] if rows else None
    return {
        "current": {
            "total_bytes": latest.total_bytes,
            "used_bytes": latest.used_bytes,
            "free_bytes": latest.free_bytes,
            "sampled_at": latest.timestamp.isoformat(),
            **storage_scope(latest),
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
                "scope": "physical_device",
                "included_in_array_capacity": x.role == "data",
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
    rows = list(
        db.scalars(
            select(Alert)
            .where(Alert.dismissed_at.is_(None))
            .order_by(desc(Alert.created_at))
            .limit(limit)
        )
    )
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
    if args.get("event_type") and not args.get("upgrades_only", False):
        query = query.where(MediaActivity.event_type == args["event_type"])
    return days, list(db.scalars(query.order_by(desc(MediaActivity.occurred_at))))


def _media_identity(row: MediaActivity) -> tuple[Any, ...]:
    return (
        row.integration_id,
        row.media_type,
        row.parent_title,
        row.title,
        row.season_number,
        row.episode_number,
    )


def _upgrade_pairs(rows: list[MediaActivity]) -> dict[int, MediaActivity]:
    deletions: dict[tuple[Any, ...], list[MediaActivity]] = {}
    for row in rows:
        if row.event_type == "file_deleted" and row.is_upgrade:
            deletions.setdefault(_media_identity(row), []).append(row)
    pairs: dict[int, MediaActivity] = {}
    used: set[int] = set()
    for imported in (row for row in rows if row.event_type == "imported"):
        candidates = [
            deleted
            for deleted in deletions.get(_media_identity(imported), [])
            if deleted.id not in used
            and abs(
                (
                    _aware_datetime(imported.occurred_at) - _aware_datetime(deleted.occurred_at)
                ).total_seconds()
            )
            <= 600
        ]
        if candidates:
            deleted = min(
                candidates,
                key=lambda row: abs(
                    (
                        _aware_datetime(imported.occurred_at) - _aware_datetime(row.occurred_at)
                    ).total_seconds()
                ),
            )
            pairs[imported.id] = deleted
            used.add(deleted.id)
    return pairs


def _aware_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def media_activity_summary(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    days, rows = _media_rows(db, args)
    upgrade_pairs = _upgrade_pairs(rows)
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
        if row.event_type == "file_deleted" and row.is_upgrade:
            group["explicit_upgrades"] += 1
        elif row.event_type == "imported" and row.is_upgrade and row.id not in upgrade_pairs:
            group["explicit_upgrades"] += 1
    cutoff = datetime.now(UTC) - timedelta(days=days)
    storage = current_storage_samples(db, since=cutoff)
    measured_change = storage[-1].used_bytes - storage[0].used_bytes if len(storage) >= 2 else None
    return {
        "days": days,
        "instances": instances,
        "measured_storage_change_bytes": measured_change,
        "evidence_note": (
            "Import sizes are gross media events and do not prove net storage growth; "
            "hardlinks, replacements, deletions, and incomplete size fields can differ. "
            "Quality upgrades are confirmed by a provider deletion event whose reason is Upgrade, "
            "paired with a nearby import when available. They are not video conversions."
        ),
    }


def media_activity_items(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    days, rows = _media_rows(db, args)
    limit = min(max(int(args.get("limit", 25)), 1), 100)
    upgrade_pairs = _upgrade_pairs(rows)
    paired_deletions = {row.id for row in upgrade_pairs.values()}
    activities: list[dict[str, Any]] = []
    for row in rows:
        deleted = upgrade_pairs.get(row.id)
        if args.get("upgrades_only", False):
            if deleted is not None:
                activities.append(
                    {
                        "timestamp": _aware_datetime(row.occurred_at).isoformat(),
                        "provider": row.provider,
                        "instance": row.instance_name,
                        "event_type": "quality_upgraded",
                        "media_type": row.media_type,
                        "title": row.title,
                        "series": row.parent_title,
                        "season": row.season_number,
                        "episode": row.episode_number,
                        "previous_quality": deleted.quality,
                        "quality": row.quality,
                        "bytes": row.bytes,
                        "evidence": "provider deletion reason Upgrade followed by import",
                    }
                )
            elif (
                row.event_type == "file_deleted"
                and row.is_upgrade
                and row.id not in paired_deletions
            ):
                activities.append(
                    {
                        "timestamp": _aware_datetime(row.occurred_at).isoformat(),
                        "provider": row.provider,
                        "instance": row.instance_name,
                        "event_type": "quality_upgrade",
                        "media_type": row.media_type,
                        "title": row.title,
                        "series": row.parent_title,
                        "season": row.season_number,
                        "episode": row.episode_number,
                        "previous_quality": row.quality,
                        "quality": None,
                        "bytes": None,
                        "evidence": "provider deletion reason Upgrade; matching import is outside the selected window",
                    }
                )
            elif row.event_type == "imported" and row.is_upgrade and deleted is None:
                activities.append(
                    {
                        "timestamp": _aware_datetime(row.occurred_at).isoformat(),
                        "provider": row.provider,
                        "instance": row.instance_name,
                        "event_type": "quality_upgraded",
                        "media_type": row.media_type,
                        "title": row.title,
                        "series": row.parent_title,
                        "season": row.season_number,
                        "episode": row.episode_number,
                        "previous_quality": None,
                        "quality": row.quality,
                        "bytes": row.bytes,
                        "evidence": "import explicitly marked as an upgrade by the provider",
                    }
                )
            continue
        activities.append(
            {
                "timestamp": _aware_datetime(row.occurred_at).isoformat(),
                "provider": row.provider,
                "instance": row.instance_name,
                "event_type": row.event_type,
                "media_type": row.media_type,
                "title": row.title,
                "series": row.parent_title,
                "season": row.season_number,
                "episode": row.episode_number,
                "previous_quality": deleted.quality if deleted else None,
                "quality": row.quality,
                "bytes": row.bytes,
                "explicit_upgrade": row.is_upgrade or deleted is not None,
            }
        )
    return {
        "days": days,
        "activities": activities[:limit],
    }


def upcoming_media(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    days = min(max(int(args.get("days", 1)), 1), 30)
    limit = min(max(int(args.get("limit", 50)), 1), 100)
    now = datetime.now(UTC)
    query = (
        select(MediaSchedule)
        .join(Integration, MediaSchedule.integration_id == Integration.id)
        .where(
            Integration.enabled.is_(True),
            MediaSchedule.monitored.is_(True),
            MediaSchedule.scheduled_at >= now,
            MediaSchedule.scheduled_at <= now + timedelta(days=days),
        )
    )
    if args.get("provider"):
        query = query.where(MediaSchedule.provider == args["provider"])
    if args.get("instance"):
        query = query.where(MediaSchedule.instance_name == args["instance"])
    if not args.get("include_acquired", False):
        query = query.where(MediaSchedule.has_file.is_(False))
    rows = list(db.scalars(query.order_by(MediaSchedule.scheduled_at).limit(limit)))
    timezone = time_zone_details(db)
    return {
        "display_timezone": timezone.name,
        "window_start_utc": now.isoformat(),
        "window_end_utc": (now + timedelta(days=days)).isoformat(),
        "items": [
            {
                "scheduled_at": _aware_datetime(row.scheduled_at).isoformat(),
                "scheduled_at_local": local_time(db, row.scheduled_at).isoformat(),
                "provider": row.provider,
                "instance": row.instance_name,
                "media_type": row.media_type,
                "title": row.title,
                "series": row.parent_title,
                "season": row.season_number,
                "episode": row.episode_number,
                "calendar_event": row.release_type,
                "already_has_file": row.has_file,
            }
            for row in rows
        ],
        "terminology_note": (
            "These are monitored Sonarr/Radarr calendar entries in a rolling UTC window, not "
            "guaranteed scheduled downloads. Sonarr/Radarr may grab them when an eligible "
            "release becomes available; use grabbed history to confirm an actual download."
        ),
    }


def quality_upgrades(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    return media_activity_items(db, args | {"upgrades_only": True})


TOOLS: dict[str, tuple[str, dict[str, Any], ToolHandler]] = {
    "get_server_overview": (
        "Get current combined Unraid array capacity (data disks only, excluding named pools), array status, and resource summary.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        server_overview,
    ),
    "get_array_status": (
        "Get current array status and combined data-disk capacity, excluding named pools.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        array_status,
    ),
    "get_array_capacity": (
        "Get combined Unraid array data-disk capacity. Never substitute a physical disk or named pool value.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        array_capacity,
    ),
    "get_storage_history": (
        "Get measured combined array history for the current measurement source, excluding incompatible older sources.",
        {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 3650}},
            "additionalProperties": False,
        },
        storage_history,
    ),
    "get_storage_growth_rate": (
        "Get deterministic combined-array 7/30/90-day storage rates, excluding named pools.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        storage_forecast,
    ),
    "get_storage_forecast": (
        "Get deterministic combined-array storage exhaustion forecasts, excluding named pools.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        storage_forecast,
    ),
    "get_pool_status": (
        "Get named Unraid pool capacity, devices, and status separately from combined array capacity.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        pools,
    ),
    "get_disk_list": (
        "List individual physical disks and health. Per-device capacity is not combined array capacity.",
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
        system_resources,
    ),
    "get_media_activity_summary": (
        "Summarize normalized Sonarr/Radarr activity by configurable instance. ServerSense does track quality upgrades: use this before answering questions about TV/movie upgrades, downloads, imports, or storage changes; heed its evidence note.",
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
        "List bounded normalized Sonarr/Radarr activity with titles and source instance. Set upgrades_only=true for quality upgrades confirmed by provider upgrade-deletion evidence and a nearby import when available.",
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
                "upgrades_only": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        media_activity_items,
    ),
    "get_upcoming_media": (
        "Get monitored upcoming Sonarr/Radarr calendar entries. Use for questions about what is coming today or may download soon, but describe them as upcoming/eligible rather than guaranteed scheduled downloads.",
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 30},
                "provider": {"type": "string", "enum": ["sonarr", "radarr"]},
                "instance": {"type": "string", "maxLength": 160},
                "include_acquired": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        upcoming_media,
    ),
    "get_quality_upgrades": (
        "List confirmed Sonarr/Radarr quality replacements. Use this for questions such as 'any TV upgrades today?'; these are higher-quality file replacements, not conversions.",
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
                "provider": {"type": "string", "enum": ["sonarr", "radarr"]},
                "instance": {"type": "string", "maxLength": 160},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        quality_upgrades,
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
        if rule.get("type") == "boolean" and not isinstance(value, bool):
            raise ValueError(f"Argument must be a boolean: {key}")


def execute_tool(db: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        raise ValueError(f"Tool is not permitted: {name}")
    _validate_arguments(name, arguments)
    policy.authorize(ActionRequest(principal="sense", action=name, risk=ActionRisk.READ_ONLY))
    return TOOLS[name][2](db, arguments)
