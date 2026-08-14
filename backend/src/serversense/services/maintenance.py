import io
import json
import re
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from serversense.config import get_settings
from serversense.models import (
    Alert,
    DiskSample,
    DockerSample,
    MetricSample,
    Setting,
    StorageSample,
)

SENSITIVE_PATTERN = re.compile(
    r"(?i)(authorization:\s*bearer\s+|api[_ -]?key[=:]\s*|token[=:]\s*)[^\s,;]+"
)


def create_backup() -> Path:
    settings = get_settings()
    source = settings.config_dir / "serversense.db"
    if not source.exists():
        raise FileNotFoundError("ServerSense database does not exist")
    destination = (
        settings.config_dir
        / "backups"
        / (f"serversense-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.db")
    )
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
    return destination


def _safe_settings(db: Session) -> list[dict[str, Any]]:
    return [
        {
            "key": row.key,
            "value": {"configured": True} if row.secret else row.value,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in db.scalars(select(Setting).order_by(Setting.key))
    ]


def diagnostic_bundle(db: Session) -> bytes:
    settings = get_settings()
    models = [MetricSample, StorageSample, DiskSample, DockerSample, Alert]
    counts = {
        model.__tablename__: db.scalar(select(func.count()).select_from(model)) or 0
        for model in models
    }
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "version": "1.0.0",
        "demo_mode": settings.demo_mode,
        "retention_days": settings.retention_days,
        "record_counts": counts,
        "settings": _safe_settings(db),
    }
    log_path = settings.config_dir / "logs" / "serversense.log"
    logs = "No persistent log is available."
    if log_path.exists():
        logs = log_path.read_text(errors="replace")[-200_000:]
        logs = SENSITIVE_PATTERN.sub(r"\1[REDACTED]", logs)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(summary, indent=2, default=str))
        archive.writestr("serversense.log", logs)
    return output.getvalue()
