import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from serversense.config import get_settings
from serversense.db import SessionLocal
from serversense.models import (
    AIConversation,
    AIMessage,
    AIToolCall,
    DiskSample,
    DockerSample,
    Event,
    MediaActivity,
    MediaSchedule,
    MetricSample,
    Setting,
    StorageSample,
)
from serversense.services.activity import active_viewers
from serversense.services.ai_config import read_ai_config
from serversense.services.alerting import evaluate_alerts
from serversense.services.collectors import build_collector, persist_snapshot
from serversense.services.dashboard_insights import refresh_dashboard_summary
from serversense.services.integrations import collect_media_integrations
from serversense.services.notifications import dispatch_notifications
from serversense.services.proactive import explain_alerts

logger = logging.getLogger(__name__)


def collection_cycle(
    include_storage: bool = True,
    include_disks: bool = True,
    include_containers: bool = True,
    include_state: bool = True,
    include_integrations: bool = True,
) -> bool:
    settings = get_settings()
    with SessionLocal() as db:
        general = db.get(Setting, "general")
        if not general:
            return False
        if settings.demo_mode or general.value.get("demo_mode"):
            return True
        try:
            snapshot = build_collector(settings).collect(
                include_storage=include_storage,
                include_disks=include_disks,
                include_containers=include_containers,
                include_state=include_state,
            )
            persist_snapshot(db, snapshot, include_storage=include_storage)
            alert_setting = db.get(Setting, "alerts")
            values = alert_setting.value if alert_setting else {}
            created = evaluate_alerts(
                db,
                free_percent_threshold=float(values.get("free_percent_threshold", 10)),
                temperature_threshold=float(values.get("temperature_c_threshold", 50)),
                forecast_days_threshold=int(values.get("forecast_days_threshold", 90)),
            )
            try:
                explain_alerts(db, created, read_ai_config(db, include_secret=True))
            except Exception as exc:
                logger.warning("Proactive SENSE explanation failed: %s", type(exc).__name__)
            failures = dispatch_notifications(db, created)
            if failures:
                logger.warning("Notification delivery failed for %d alert(s)", len(failures))
            ai_config = read_ai_config(db)
            if (
                include_integrations
                and ai_config.get("provider") != "disabled"
                and ai_config.get("model")
            ):
                collect_media_integrations(db)
        except Exception:
            logger.exception("Monitoring collection cycle failed")
        return True


def cleanup_cycle() -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=get_settings().retention_days)
    downsample_cutoff = now - timedelta(days=90)
    conversation_cutoff = now - timedelta(days=30)
    with SessionLocal() as db:
        db.execute(delete(MetricSample).where(MetricSample.timestamp < cutoff))
        db.execute(delete(DockerSample).where(DockerSample.timestamp < cutoff))
        db.execute(delete(DiskSample).where(DiskSample.timestamp < cutoff))
        db.execute(delete(StorageSample).where(StorageSample.timestamp < cutoff))
        db.execute(delete(MediaActivity).where(MediaActivity.occurred_at < cutoff))
        db.execute(
            delete(MediaSchedule).where(MediaSchedule.scheduled_at < now - timedelta(days=2))
        )
        db.execute(
            delete(Event).where(
                Event.event_type == "sense_dashboard_summary",
                Event.timestamp < conversation_cutoff,
            )
        )
        older = list(
            db.scalars(
                select(StorageSample)
                .where(StorageSample.timestamp < downsample_cutoff)
                .order_by(StorageSample.timestamp.desc())
            )
        )
        seen_days: set[str] = set()
        duplicate_ids: list[int] = []
        for sample in older:
            day = sample.timestamp.date().isoformat()
            if day in seen_days:
                duplicate_ids.append(sample.id)
            else:
                seen_days.add(day)
        if duplicate_ids:
            db.execute(delete(StorageSample).where(StorageSample.id.in_(duplicate_ids)))
        old_conversations = select(AIConversation.id).where(
            AIConversation.updated_at < conversation_cutoff
        )
        old_messages = select(AIMessage.id).where(AIMessage.conversation_id.in_(old_conversations))
        db.execute(delete(AIToolCall).where(AIToolCall.message_id.in_(old_messages)))
        db.execute(delete(AIMessage).where(AIMessage.conversation_id.in_(old_conversations)))
        db.execute(delete(AIConversation).where(AIConversation.id.in_(old_conversations)))
        db.commit()


async def monitoring_loop() -> None:
    settings = get_settings()
    tick_seconds = min(
        settings.active_metrics_interval_seconds,
        settings.active_docker_interval_seconds,
    )
    metrics_elapsed = settings.metrics_interval_seconds
    docker_elapsed = settings.docker_interval_seconds
    state_elapsed = settings.metrics_interval_seconds
    storage_elapsed = settings.storage_interval_seconds
    disk_elapsed = settings.disk_interval_seconds
    integration_elapsed = 300
    cleanup_elapsed = 86400
    # Give first-run setup ownership of selecting demo versus live collection.
    await asyncio.sleep(2)
    while True:
        active = active_viewers.is_active()
        metrics_interval = (
            settings.active_metrics_interval_seconds
            if active
            else settings.metrics_interval_seconds
        )
        docker_interval = (
            settings.active_docker_interval_seconds if active else settings.docker_interval_seconds
        )
        include_metrics = metrics_elapsed >= metrics_interval
        include_containers = docker_elapsed >= docker_interval
        include_state = state_elapsed >= settings.metrics_interval_seconds
        include_storage = storage_elapsed >= settings.storage_interval_seconds
        include_disks = disk_elapsed >= settings.disk_interval_seconds
        include_integrations = integration_elapsed >= 300
        collection_due = any(
            (
                include_metrics,
                include_containers,
                include_state,
                include_storage,
                include_disks,
                include_integrations,
            )
        )
        configured = True
        if collection_due:
            configured = await asyncio.to_thread(
                collection_cycle,
                include_storage,
                include_disks,
                include_containers,
                include_state,
                include_integrations,
            )
        if not configured:
            await asyncio.sleep(2)
            continue
        if collection_due:
            metrics_elapsed = 0
        if include_containers:
            docker_elapsed = 0
        if include_state:
            state_elapsed = 0
        if include_storage:
            storage_elapsed = 0
        if include_disks:
            disk_elapsed = 0
        if include_integrations:
            integration_elapsed = 0
        if cleanup_elapsed >= 86400:
            await asyncio.to_thread(cleanup_cycle)
            cleanup_elapsed = 0
        await asyncio.sleep(tick_seconds)
        metrics_elapsed += tick_seconds
        docker_elapsed += tick_seconds
        state_elapsed += tick_seconds
        storage_elapsed += tick_seconds
        disk_elapsed += tick_seconds
        integration_elapsed += tick_seconds
        cleanup_elapsed += tick_seconds


def dashboard_summary_cycle() -> None:
    with SessionLocal() as db:
        try:
            refresh_dashboard_summary(db, read_ai_config(db, include_secret=True))
        except Exception as exc:
            db.rollback()
            logger.warning("Cached SENSE dashboard summary failed: %s", type(exc).__name__)


async def dashboard_summary_loop() -> None:
    # Keep optional model work fully outside monitoring and dashboard request paths.
    await asyncio.sleep(30)
    while True:
        await asyncio.to_thread(dashboard_summary_cycle)
        await asyncio.sleep(300)
