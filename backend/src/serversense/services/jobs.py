import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from serversense.config import get_settings
from serversense.db import SessionLocal
from serversense.models import DiskSample, DockerSample, MetricSample, Setting, StorageSample
from serversense.services.alerting import evaluate_alerts
from serversense.services.collectors import build_collector, persist_snapshot
from serversense.services.notifications import dispatch_notifications

logger = logging.getLogger(__name__)


def collection_cycle(include_storage: bool = True) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        general = db.get(Setting, "general")
        if not general:
            return
        if settings.demo_mode or general.value.get("demo_mode"):
            return
        try:
            snapshot = build_collector(settings).collect()
            persist_snapshot(db, snapshot, include_storage=include_storage)
            alert_setting = db.get(Setting, "alerts")
            values = alert_setting.value if alert_setting else {}
            created = evaluate_alerts(
                db,
                free_percent_threshold=float(values.get("free_percent_threshold", 10)),
                temperature_threshold=float(values.get("temperature_c_threshold", 50)),
                forecast_days_threshold=int(values.get("forecast_days_threshold", 90)),
            )
            failures = dispatch_notifications(db, created)
            if failures:
                logger.warning("Notification delivery failed for %d alert(s)", len(failures))
        except Exception:
            logger.exception("Monitoring collection cycle failed")


def cleanup_cycle() -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=get_settings().retention_days)
    downsample_cutoff = now - timedelta(days=90)
    with SessionLocal() as db:
        db.execute(delete(MetricSample).where(MetricSample.timestamp < cutoff))
        db.execute(delete(DockerSample).where(DockerSample.timestamp < cutoff))
        db.execute(delete(DiskSample).where(DiskSample.timestamp < cutoff))
        db.execute(delete(StorageSample).where(StorageSample.timestamp < cutoff))
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
        db.commit()


async def monitoring_loop() -> None:
    settings = get_settings()
    storage_elapsed = settings.storage_interval_seconds
    cleanup_elapsed = 86400
    # Give first-run setup ownership of selecting demo versus live collection.
    await asyncio.sleep(2)
    while True:
        include_storage = storage_elapsed >= settings.storage_interval_seconds
        await asyncio.to_thread(collection_cycle, include_storage)
        storage_elapsed = (
            0 if include_storage else storage_elapsed + settings.metrics_interval_seconds
        )
        cleanup_elapsed += settings.metrics_interval_seconds
        if cleanup_elapsed >= 86400:
            await asyncio.to_thread(cleanup_cycle)
            cleanup_elapsed = 0
        await asyncio.sleep(settings.metrics_interval_seconds)
