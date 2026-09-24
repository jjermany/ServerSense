from collections.abc import Callable
from dataclasses import dataclass
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
from serversense.services.timezones import format_local_datetime, local_time, time_zone_details

ToolHandler = Callable[[Session, dict[str, Any]], dict[str, Any]]
UPGRADE_PAIR_WINDOW = timedelta(minutes=10)
UPGRADE_GRAB_LOOKBACK = timedelta(days=30)


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


def _signed_bytes_display(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    amount = abs(float(value))
    index = 0
    while amount >= 1000 and index < len(units) - 1:
        amount /= 1000
        index += 1
    sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}{amount:.1f} {units[index]}"


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
            "sampled_at_local_display": format_local_datetime(db, storage.timestamp),
            **storage_scope(storage),
        }
        if storage
        else None,
        "resources": {
            "cpu_percent": metric.cpu_percent,
            "memory_percent": metric.memory_percent,
            "load_1m": metric.load_1m,
            "uptime_seconds": metric.uptime_seconds,
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
    local_today = bool(args.get("today", False))
    cutoff = (
        local_time(db).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        if local_today
        else datetime.now(UTC) - timedelta(days=days)
    )
    rows = current_storage_samples(db, since=cutoff)
    first = rows[0] if rows else None
    last = rows[-1] if rows else None
    used_bytes_change = last.used_bytes - first.used_bytes if first and last else None
    return {
        "days": days,
        "period": "configured_timezone_today" if local_today else "rolling_days",
        "window_start_utc": cutoff.isoformat(),
        "storage_scope": storage_scope(rows[-1]) if rows else None,
        "sample_count": len(rows),
        "first_sampled_at_local_display": format_local_datetime(db, first.timestamp)
        if first
        else None,
        "last_sampled_at_local_display": format_local_datetime(db, last.timestamp)
        if last
        else None,
        "first_used_bytes": first.used_bytes if first else None,
        "last_used_bytes": last.used_bytes if last else None,
        "used_bytes_change": used_bytes_change,
        "used_bytes_change_display": _signed_bytes_display(used_bytes_change)
        if used_bytes_change is not None
        else None,
        "change_note": (
            "used_bytes_change is the canonical measured change for this window. Report it once; "
            "do not recalculate it from rounded capacity values or media event sizes."
        ),
        "samples": [
            {
                "timestamp": x.timestamp.isoformat(),
                "timestamp_local_display": format_local_datetime(db, x.timestamp),
                "total_bytes": x.total_bytes,
                "used_bytes": x.used_bytes,
                "free_bytes": x.free_bytes,
            }
            for x in rows[-500:]
        ],
    }


def storage_forecast(db: Session, _: dict[str, Any]) -> dict[str, Any]:
    rows = current_storage_samples(db, window_days=90)
    latest = rows[-1] if rows else None
    return {
        "current": {
            "total_bytes": latest.total_bytes,
            "used_bytes": latest.used_bytes,
            "free_bytes": latest.free_bytes,
            "sampled_at": latest.timestamp.isoformat(),
            "sampled_at_local_display": format_local_datetime(db, latest.timestamp),
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
        "state_change_note": (
            "state_changed_at records a status, health, restart-count change, or first observation. "
            "It does not identify the cause and must not be described as a restart by itself. "
            "restart_count is the current Docker-reported cumulative count, not a count for today."
        ),
        "containers": [
            {
                "name": x.name,
                "image": x.image,
                "status": x.status,
                "health": x.health,
                "state_changed_at": x.state_changed_at.isoformat() if x.state_changed_at else None,
                "state_changed_at_local_display": format_local_datetime(db, x.state_changed_at)
                if x.state_changed_at
                else None,
                "uptime_seconds": _elapsed_since(x.started_at),
                "cpu_percent": x.cpu_percent,
                "memory_bytes": x.memory_bytes,
                "restart_count": x.restart_count,
                "state_change_cause": "unknown_from_current_snapshot"
                if x.state_changed_at
                else None,
            }
            for x in rows
        ],
    }


def recent_alerts(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(args.get("limit", 20)), 1), 100)
    query = select(Alert).where(Alert.dismissed_at.is_(None))
    local_today = bool(args.get("today", False))
    cutoff: datetime | None = None
    if local_today:
        cutoff = local_time(db).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = cutoff.astimezone(UTC)
        query = query.where(Alert.created_at >= cutoff)
    rows = list(db.scalars(query.order_by(desc(Alert.created_at)).limit(limit)))
    return {
        "period": "configured_timezone_today" if local_today else "recent",
        "window_start_utc": cutoff.isoformat() if cutoff else None,
        "alerts": [
            {
                "severity": x.severity,
                "title": x.title,
                "message": x.message,
                "active": x.active,
                "timestamp": x.created_at.isoformat(),
                "timestamp_local_display": format_local_datetime(db, x.created_at),
            }
            for x in rows
        ],
    }


def _media_rows(db: Session, args: dict[str, Any]) -> tuple[int, datetime, list[MediaActivity]]:
    days = min(max(int(args.get("days", 30)), 1), 365)
    local_today = bool(args.get("today", False))
    cutoff = (
        local_time(db).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        if local_today
        else datetime.now(UTC) - timedelta(days=days)
    )
    # Load bounded precursor evidence before applying the requested reporting
    # period or event filter. Otherwise an import-only request hides the grab
    # and Upgrade deletion needed to classify that import.
    query = select(MediaActivity).where(MediaActivity.occurred_at >= cutoff - UPGRADE_GRAB_LOOKBACK)
    if args.get("provider"):
        query = query.where(MediaActivity.provider == args["provider"])
    if args.get("instance"):
        query = query.where(MediaActivity.instance_name == args["instance"])
    return days, cutoff, list(db.scalars(query.order_by(desc(MediaActivity.occurred_at))))


def _media_identity(row: MediaActivity) -> tuple[Any, ...]:
    return (
        row.integration_id,
        row.media_type,
        row.parent_title,
        row.title,
        row.season_number,
        row.episode_number,
    )


def _same_media(left: MediaActivity, right: MediaActivity) -> bool:
    if left.integration_id != right.integration_id or left.media_type != right.media_type:
        return False
    if left.provider_media_id is not None and right.provider_media_id is not None:
        return left.provider_media_id == right.provider_media_id
    return _media_identity(left) == _media_identity(right)


def _same_quality(left: MediaActivity, right: MediaActivity) -> bool:
    return (
        left.quality is None
        or right.quality is None
        or left.quality.casefold() == right.quality.casefold()
    )


@dataclass(frozen=True)
class _UpgradeChain:
    deleted: MediaActivity
    grabbed: MediaActivity | None


def _upgrade_pairs(rows: list[MediaActivity]) -> dict[int, _UpgradeChain]:
    deletions = [row for row in rows if row.event_type == "file_deleted" and row.is_upgrade]
    grabs = [row for row in rows if row.event_type == "grabbed"]
    pairs: dict[int, _UpgradeChain] = {}
    used_deletions: set[int] = set()
    used_grabs: set[int] = set()
    for imported in (row for row in rows if row.event_type == "imported"):
        candidates = [
            deleted
            for deleted in deletions
            if deleted.id not in used_deletions
            and _same_media(imported, deleted)
            and timedelta(0)
            <= _aware_datetime(imported.occurred_at) - _aware_datetime(deleted.occurred_at)
            <= UPGRADE_PAIR_WINDOW
        ]
        if candidates:
            deleted = min(
                candidates,
                key=lambda row: (
                    _aware_datetime(imported.occurred_at) - _aware_datetime(row.occurred_at)
                ),
            )
            grabbed_candidates = [
                grabbed
                for grabbed in grabs
                if grabbed.id not in used_grabs
                and _same_media(imported, grabbed)
                and grabbed.download_id_hash is not None
                and grabbed.download_id_hash == imported.download_id_hash
                and _same_quality(imported, grabbed)
                and timedelta(0)
                <= _aware_datetime(deleted.occurred_at) - _aware_datetime(grabbed.occurred_at)
                <= UPGRADE_GRAB_LOOKBACK
            ]
            grabbed = (
                min(
                    grabbed_candidates,
                    key=lambda row: (
                        _aware_datetime(deleted.occurred_at) - _aware_datetime(row.occurred_at)
                    ),
                )
                if grabbed_candidates
                else None
            )
            pairs[imported.id] = _UpgradeChain(deleted=deleted, grabbed=grabbed)
            used_deletions.add(deleted.id)
            if grabbed is not None:
                used_grabs.add(grabbed.id)
    return pairs


def _upgrade_evidence(chain: _UpgradeChain) -> tuple[str, list[str]]:
    if chain.grabbed is not None:
        return (
            "provider grab followed by deletion reason Upgrade and import",
            ["grabbed", "file_deleted", "imported"],
        )
    return (
        "provider deletion reason Upgrade followed by import",
        ["file_deleted", "imported"],
    )


def _aware_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def media_activity_summary(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    days, cutoff, evidence_rows = _media_rows(db, args)
    rows = [row for row in evidence_rows if _aware_datetime(row.occurred_at) >= cutoff]
    upgrade_pairs = _upgrade_pairs(evidence_rows)
    paired_deletions = {chain.deleted.id for chain in upgrade_pairs.values()}
    instances: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = instances.setdefault(
            row.instance_name,
            {
                "provider": row.provider,
                "events": {},
                "known_import_bytes": 0,
                "confirmed_quality_upgrades": 0,
                "explicit_upgrade_deletions": 0,
                "upgrade_deletions_without_matching_import": 0,
            },
        )
        events: dict[str, int] = group["events"]
        events[row.event_type] = events.get(row.event_type, 0) + 1
        if row.event_type == "imported" and row.bytes is not None:
            group["known_import_bytes"] += row.bytes
        if row.event_type == "imported" and row.id in upgrade_pairs:
            group["confirmed_quality_upgrades"] += 1
        elif row.event_type == "file_deleted" and row.is_upgrade:
            group["explicit_upgrade_deletions"] += 1
            if row.id not in paired_deletions:
                group["upgrade_deletions_without_matching_import"] += 1
    storage = current_storage_samples(db, since=cutoff)
    measured_change = storage[-1].used_bytes - storage[0].used_bytes if len(storage) >= 2 else None
    return {
        "days": days,
        "period": "configured_timezone_today" if args.get("today", False) else "rolling_days",
        "instances": instances,
        "measured_storage_change_bytes": measured_change,
        "quality_upgrade_definition": (
            "confirmed_quality_upgrades counts imports paired one-to-one with a provider deletion "
            "whose explicit reason is Upgrade. A matching grab corroborates the replacement. "
            "A grab, import, rename, quality difference, or ordinary deletion alone is not a "
            "quality upgrade. upgrade_deletions_without_matching_import is incomplete evidence, "
            "not a confirmed replacement."
        ),
        "evidence_note": (
            "known_import_bytes totals gross media import events; hardlinks, replacements, "
            "deletions, and incomplete size fields mean those totals do not prove net growth. "
            "measured_storage_change_bytes is a separate net combined-array measurement. "
            "The values may correlate, but telemetry does not prove media activity caused the "
            "measured storage change. "
            "Quality upgrades are confirmed by a provider deletion event whose reason is Upgrade, "
            "paired with a nearby import. A matching prior grab is included as corroborating "
            "evidence when available; a grab or import alone is not upgrade evidence. They are "
            "not video conversions."
        ),
    }


def media_activity_items(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    days, cutoff, rows = _media_rows(db, args)
    limit = min(max(int(args.get("limit", 25)), 1), 100)
    upgrade_pairs = _upgrade_pairs(rows)
    paired_deletions = {chain.deleted.id for chain in upgrade_pairs.values()}
    requested_event = args.get("event_type") if not args.get("upgrades_only", False) else None
    activities: list[dict[str, Any]] = []
    for row in rows:
        if _aware_datetime(row.occurred_at) < cutoff:
            continue
        if requested_event and row.event_type != requested_event:
            continue
        chain = upgrade_pairs.get(row.id)
        deleted = chain.deleted if chain else None
        evidence, evidence_events = _upgrade_evidence(chain) if chain else (None, None)
        if args.get("upgrades_only", False):
            if deleted is not None:
                activities.append(
                    {
                        "timestamp": _aware_datetime(row.occurred_at).isoformat(),
                        "timestamp_local_display": format_local_datetime(db, row.occurred_at),
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
                        "previous_bytes": deleted.bytes,
                        "replacement_bytes": row.bytes,
                        "net_bytes_change": row.bytes - deleted.bytes
                        if row.bytes is not None and deleted.bytes is not None
                        else None,
                        "evidence": evidence,
                        "evidence_events": evidence_events,
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
                        "timestamp_local_display": format_local_datetime(db, row.occurred_at),
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
                        "previous_bytes": row.bytes,
                        "replacement_bytes": None,
                        "net_bytes_change": None,
                        "evidence": (
                            "provider deletion reason Upgrade; no matching import was found "
                            "in the bounded evidence window"
                        ),
                        "evidence_events": ["file_deleted"],
                    }
                )
            continue
        if row.id in paired_deletions and requested_event != "file_deleted":
            continue
        activities.append(
            {
                "timestamp": _aware_datetime(row.occurred_at).isoformat(),
                "timestamp_local_display": format_local_datetime(db, row.occurred_at),
                "provider": row.provider,
                "instance": row.instance_name,
                "event_type": "quality_upgraded"
                if row.event_type == "imported" and deleted is not None
                else row.event_type,
                "classification": "confirmed_quality_upgrade"
                if row.event_type == "imported" and deleted is not None
                else "unclassified_import"
                if row.event_type == "imported"
                else row.event_type,
                "media_type": row.media_type,
                "title": row.title,
                "series": row.parent_title,
                "season": row.season_number,
                "episode": row.episode_number,
                "previous_quality": deleted.quality if deleted else None,
                "quality": row.quality,
                "bytes": row.bytes,
                "previous_bytes": deleted.bytes if deleted else None,
                "replacement_bytes": row.bytes
                if row.event_type == "imported" and deleted is not None
                else None,
                "net_bytes_change": row.bytes - deleted.bytes
                if row.bytes is not None and deleted is not None and deleted.bytes is not None
                else None,
                "explicit_upgrade": deleted is not None
                or (row.event_type == "file_deleted" and row.is_upgrade),
                "upgrade_evidence": evidence,
                "upgrade_evidence_events": evidence_events,
            }
        )
    selected_activities = activities[:limit]
    result: dict[str, Any] = {
        "days": days,
        "period": "configured_timezone_today" if args.get("today", False) else "rolling_days",
        "activities": selected_activities,
    }
    if args.get("upgrades_only", False):
        known_changes = [
            item["net_bytes_change"]
            for item in selected_activities
            if item["net_bytes_change"] is not None
        ]
        result.update(
            {
                "known_net_bytes_change": sum(known_changes),
                "net_change_known_count": len(known_changes),
                "net_change_unknown_count": len(selected_activities) - len(known_changes),
                "net_change_evidence_note": (
                    "Each known net change is replacement_bytes minus previous_bytes. This is "
                    "the logical media-file size difference, not a measured array-storage change. "
                    "Upgrades missing either provider-reported size are excluded from the known sum."
                ),
            }
        )
    return result


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
                "scheduled_at_local_display": format_local_datetime(db, row.scheduled_at),
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
        "Get measured combined-array history and its canonical used_bytes_change. Report used_bytes_change_display once instead of recalculating from rounded values.",
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 3650},
                "today": {"type": "boolean"},
            },
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
        "Get current Docker states. state_changed_at alone does not prove a restart; restart_count is cumulative, not today-only.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        containers,
    ),
    "get_recent_alerts": (
        "Get recent ServerSense alerts.",
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "today": {"type": "boolean"},
            },
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
        "Summarize Sonarr/Radarr activity by instance. confirmed_quality_upgrades are paired Upgrade deletions/imports; unmatched upgrade deletions are incomplete evidence. Heed the definition and evidence notes.",
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
                "today": {"type": "boolean"},
                "provider": {"type": "string", "enum": ["sonarr", "radarr"]},
                "instance": {"type": "string", "maxLength": 160},
            },
            "additionalProperties": False,
        },
        media_activity_summary,
    ),
    "get_media_activity_items": (
        "List bounded Sonarr/Radarr activity. Correlation runs before event filtering. confirmed_quality_upgrade requires an Upgrade deletion plus import and may include a matched grab. unclassified_import means unknown, not regular or new. Renames are not evidence. Set upgrades_only=true for confirmed upgrades.",
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
                "today": {"type": "boolean"},
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
        "List provider-confirmed Sonarr/Radarr replacements from Upgrade deletion/import evidence, plus a matched grab when available. Includes provider-reported old/new sizes and logical net change. Never estimate missing sizes or call this measured array growth.",
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
                "today": {"type": "boolean"},
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
