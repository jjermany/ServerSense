from sqlalchemy import select
from sqlalchemy.orm import Session

from serversense.models import DiskSample, DockerSample


def latest_inventory[InventorySample: (DiskSample, DockerSample)](
    db: Session, model: type[InventorySample]
) -> list[InventorySample]:
    """Load only the newest collected inventory, independent of retained history."""
    timestamp = db.scalar(select(model.timestamp).order_by(model.timestamp.desc()).limit(1))
    if timestamp is None:
        return []
    return list(db.scalars(select(model).where(model.timestamp == timestamp)))
