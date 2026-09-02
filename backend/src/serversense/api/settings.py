import json
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from serversense.db import get_db
from serversense.models import Alert, Setting
from serversense.schemas import AISettings, AlertSettings, GeneralSettingsUpdate
from serversense.security import current_user
from serversense.services.ai_config import read_ai_config
from serversense.services.notifications import DELIVERY_ERRORS, provider_from_config
from serversense.services.secrets import decrypt_secret, encrypt_secret
from serversense.services.timezones import time_zone_details, validate_time_zone

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(current_user)])


def _ai_headers(config: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {config['api_key']}"} if config.get("api_key") else {}


def _discover_models(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("provider") == "disabled":
        return {"models": [], "selected_exists": False, "provider": "disabled"}
    endpoint = str(config.get("endpoint", "")).rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("AI endpoint must use HTTP or HTTPS")
    response = httpx.get(
        f"{endpoint}/v1/models",
        headers=_ai_headers(config),
        timeout=float(config.get("timeout_seconds", 60)),
    )
    response.raise_for_status()
    payload = response.json()
    records = payload.get("data", []) if isinstance(payload, dict) else []
    models = []
    for record in records[:500]:
        if not isinstance(record, dict) or not record.get("id"):
            continue
        capabilities = record.get("capabilities") or []
        supports_tools = record.get("supports_tools")
        if supports_tools is None and isinstance(capabilities, list):
            supports_tools = any(
                str(item).lower() in {"tools", "tool_use", "function_calling"}
                for item in capabilities
            )
        models.append(
            {
                "id": str(record["id"]),
                "owned_by": record.get("owned_by"),
                "supports_tools": supports_tools,
            }
        )
    selected = str(config.get("model", ""))
    return {
        "models": models,
        "selected_exists": any(row["id"] == selected for row in models),
        "provider": config.get("provider"),
        "selected": selected,
    }


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


@router.delete("/ai/api-key")
def clear_ai_api_key(db: Session = Depends(get_db)) -> dict[str, Any]:
    current = db.get(Setting, "ai")
    if current and current.value.get("api_key_encrypted"):
        value = dict(current.value)
        value.pop("api_key_encrypted", None)
        current.value = value
        db.commit()
    return read_ai_config(db)


@router.post("/ai/test")
def test_ai_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    config = read_ai_config(db, include_secret=True)
    if config.get("provider") == "disabled":
        return {"healthy": True, "detail": "SENSE is using built-in deterministic mode"}
    endpoint = str(config.get("endpoint", "")).rstrip("/")
    try:
        discovery = _discover_models(config)
        if discovery["models"] and not discovery["selected_exists"]:
            raise ValueError("The selected model was not returned by the provider")
        response = httpx.post(
            f"{endpoint}/v1/chat/completions",
            headers=_ai_headers(config),
            json={
                "model": config.get("model"),
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "temperature": 0,
                "max_tokens": 8,
                "stream": False,
            },
            timeout=float(config.get("timeout_seconds", 60)),
        )
        response.raise_for_status()
        body = response.json()
        if not (body.get("choices") if isinstance(body, dict) else None):
            raise ValueError("The model returned no completion choices")
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Model endpoint is unavailable: {type(exc).__name__}") from exc
    return {"healthy": True, "detail": f"Connected to {config.get('model')}"}


@router.get("/ai/models")
def discover_ai_models(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return _discover_models(read_ai_config(db, include_secret=True))
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(502, f"Could not discover models: {type(exc).__name__}") from exc


@router.get("/general")
def get_general_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(Setting, "general")
    value = dict(row.value) if row else {}
    timezone = time_zone_details(db)
    return value | {
        "timezone": timezone.name,
        "timezone_source": timezone.source,
        "timezone_configurable": timezone.configurable,
        "timezone_warning": timezone.warning,
    }


@router.put("/general")
def update_general_settings(
    payload: GeneralSettingsUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    timezone = time_zone_details(db)
    if not timezone.configurable:
        raise HTTPException(409, "Timezone is controlled by the container TZ variable")
    try:
        name = validate_time_zone(payload.timezone)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = db.get(Setting, "general")
    if row:
        row.value = dict(row.value) | {"timezone": name}
    else:
        db.add(Setting(key="general", value={"timezone": name}, secret=False))
    db.commit()
    return get_general_settings(db)


ALERT_DEFAULTS: dict[str, Any] = {
    "free_percent_threshold": 10,
    "forecast_days_threshold": 90,
    "temperature_c_threshold": 50,
    "notify_storage_low": True,
    "notify_forecast_low": True,
    "notify_disk_smart": True,
    "notify_disk_temperature": True,
    "notify_container_stopped": True,
    "notify_sense_jobs": True,
    "webhook_enabled": False,
    "discord_enabled": False,
    "pushover_enabled": False,
    "email_enabled": False,
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "email_from": "",
    "email_to": "",
}
ALERT_SECRET_FIELDS = (
    "webhook_url",
    "discord_webhook_url",
    "pushover_user_key",
    "pushover_app_token",
    "smtp_username",
    "smtp_password",
)


def read_alert_config(db: Session, include_secret: bool = False) -> dict[str, Any]:
    row = db.get(Setting, "alerts")
    value = {**ALERT_DEFAULTS, **(row.value if row else {})}
    for field in ALERT_SECRET_FIELDS:
        encrypted = str(value.pop(f"{field}_encrypted", ""))
        if include_secret:
            value[field] = decrypt_secret(encrypted) if encrypted else ""
        else:
            configured_key = (
                "webhook_configured" if field == "webhook_url" else f"{field}_configured"
            )
            value[configured_key] = bool(encrypted)
    return value


@router.get("/alerts")
def get_alert_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return read_alert_config(db)


@router.put("/alerts")
def update_alert_settings(payload: AlertSettings, db: Session = Depends(get_db)) -> dict[str, Any]:
    current = db.get(Setting, "alerts")
    value = payload.model_dump(exclude=set(ALERT_SECRET_FIELDS))
    for field in ALERT_SECRET_FIELDS:
        supplied = getattr(payload, field)
        if supplied:
            if field.endswith("webhook_url") and not supplied.startswith(("http://", "https://")):
                raise HTTPException(
                    422, f"{field.replace('_', ' ').title()} must use HTTP or HTTPS"
                )
            value[f"{field}_encrypted"] = encrypt_secret(supplied)
        elif current and current.value.get(f"{field}_encrypted"):
            value[f"{field}_encrypted"] = current.value[f"{field}_encrypted"]
    for provider_key in ("webhook", "discord", "pushover", "email"):
        if value.get(f"{provider_key}_enabled"):
            try:
                provider_from_config(provider_key, value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, str(exc)) from exc
    if current:
        current.value = value
    else:
        db.add(Setting(key="alerts", value=value, secret=True))
    db.commit()
    return read_alert_config(db)


@router.post("/alerts/test")
def test_alert_webhook(db: Session = Depends(get_db)) -> dict[str, Any]:
    return _test_notification_provider("webhook", db)


@router.post("/alerts/test/{provider_key}")
def test_alert_provider(provider_key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if provider_key not in {"webhook", "discord", "pushover", "email"}:
        raise HTTPException(404, "Notification provider was not found")
    return _test_notification_provider(provider_key, db)


def _test_notification_provider(provider_key: str, db: Session) -> dict[str, Any]:
    row = db.get(Setting, "alerts")
    value = row.value if row else {}
    if not value.get(f"{provider_key}_enabled"):
        raise HTTPException(422, f"Enable and save {provider_key} before testing")
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
        provider_from_config(provider_key, value).send(alert)
    except DELIVERY_ERRORS as exc:
        raise HTTPException(
            502, f"{provider_key.title()} delivery failed: {type(exc).__name__}"
        ) from exc
    return {"ok": True, "detail": "Test notification delivered"}
