from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from serversense.models import StorageSample


def latest_storage_sample(db: Session) -> StorageSample | None:
    return db.scalar(select(StorageSample).order_by(desc(StorageSample.timestamp)))


def current_storage_samples(db: Session, *, since: datetime | None = None) -> list[StorageSample]:
    """Return only samples compatible with the newest measurement source."""
    latest = latest_storage_sample(db)
    if latest is None:
        return []
    statement = select(StorageSample).where(StorageSample.source == latest.source)
    if since is not None:
        statement = statement.where(StorageSample.timestamp >= since)
    return list(db.scalars(statement.order_by(StorageSample.timestamp)))


def storage_scope(sample: StorageSample) -> dict[str, object]:
    if sample.source in {"unraid_array", "demo"}:
        return {
            "scope": "combined_array_data_disks",
            "includes_named_pools": False,
            "measurement_source": sample.source,
        }
    return {
        "scope": "configured_storage_filesystem",
        "includes_named_pools": None,
        "measurement_source": sample.source,
    }
