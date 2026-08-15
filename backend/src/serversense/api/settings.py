from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from serversense.db import get_db
from serversense.models import Alert, Setting
from serversense.schemas import AISettings, AlertSettings
from serversense.security import current_user
from serversense.services.ai_config import read_ai_config
from serversense.services.notifications import WebhookProvider
from serversense.services.secrets import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(current_user)])


@router.get("/ai")
def get_ai_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return read_ai_config(db)


@router.put("/ai")
def update_ai_settings(payload: AISettings, db: Session = Depends(get_db)) -> dict[str, Any]:
    current = db.get(Setting, "ai")
    value = payload.model_dump(exclude={"api_key"})
    if payload.api_key:
        value["api_key_encrypted"] = encrypt_secret(payload.api_key)
    elif current and current.value.get("api_key_encrypted"):
        value["api_key_encrypted"] = current.value["api_key_encrypted"]
    if current:
        current.value = value
    else:
        db.add(Setting(key="ai", value=value, secret=True))
    db.commit()
    return read_ai_config(db)


@router.post("/ai/test")
def test_ai_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    config = read_ai_config(db, include_secret=True)
    if config.get("provider") == "disabled":
        return {"healthy": True, "detail": "SENSE is using built-in deterministic mode"}
    endpoint = str(config.get("endpoint", "")).rstrip("/")
    try:
        headers = {"Authorization": f"Bearer {config['api_key']}"} if config.get("api_key") else {}
        response = httpx.get(
            f"{endpoint}/v1/models",
            headers=headers,
            timeout=float(config.get("timeout_seconds", 60)),
        )
        response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Model endpoint is unavailable: {type(exc).__name__}") from exc
    return {"healthy": True, "detail": f"Connected to {config.get('model')}"}


@router.get("/general")
def get_general_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(Setting, "general")
    return row.value if row else {}


def read_alert_config(db: Session, include_secret: bool = False) -> dict[str, Any]:
    row = db.get(Setting, "alerts")
    value = (
        dict(row.value)
        if row
        else {
            "free_percent_threshold": 10,
            "forecast_days_threshold": 90,
            "temperature_c_threshold": 50,
            "webhook_enabled": False,
        }
    )
    encrypted = str(value.pop("webhook_url_encrypted", ""))
    if include_secret:
        value["webhook_url"] = decrypt_secret(encrypted) if encrypted else ""
    else:
        value["webhook_configured"] = bool(encrypted)
    return value


@router.get("/alerts")
def get_alert_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return read_alert_config(db)


@router.put("/alerts")
def update_alert_settings(payload: AlertSettings, db: Session = Depends(get_db)) -> dict[str, Any]:
    current = db.get(Setting, "alerts")
    value = payload.model_dump(exclude={"webhook_url"})
    if payload.webhook_url:
        if not payload.webhook_url.startswith(("http://", "https://")):
            raise HTTPException(422, "Webhook URL must use HTTP or HTTPS")
        value["webhook_url_encrypted"] = encrypt_secret(payload.webhook_url)
    elif current and current.value.get("webhook_url_encrypted"):
        value["webhook_url_encrypted"] = current.value["webhook_url_encrypted"]
    if current:
        current.value = value
    else:
        db.add(Setting(key="alerts", value=value, secret=True))
    db.commit()
    return read_alert_config(db)


@router.post("/alerts/test")
def test_alert_webhook(db: Session = Depends(get_db)) -> dict[str, Any]:
    config = read_alert_config(db, include_secret=True)
    url = str(config.get("webhook_url", ""))
    if not config.get("webhook_enabled") or not url:
        raise HTTPException(422, "Enable and save a webhook before testing")
    alert = Alert(
        alert_type="test",
        severity="info",
        title="ServerSense test notification",
        message="Your generic webhook integration is working.",
        fingerprint="test",
        data={},
    )
    alert.created_at = datetime.now(UTC)
    try:
        WebhookProvider(url).send(alert)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Webhook delivery failed: {type(exc).__name__}") from exc
    return {"ok": True, "detail": "Test notification delivered"}
