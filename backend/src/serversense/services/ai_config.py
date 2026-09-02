from typing import Any

from sqlalchemy.orm import Session

from serversense.models import Setting
from serversense.services.secrets import decrypt_secret

AI_DEFAULTS: dict[str, Any] = {
    "provider": "disabled",
    "model": "",
    "endpoint": "",
    "context_window": 4096,
    "temperature": 0.2,
    "timeout_seconds": 120,
    "max_tool_calls": 3,
    "max_output_tokens": 512,
    "tool_calling": "auto",
    "background_threshold_seconds": 30,
    "max_runtime_seconds": 300,
    "max_concurrent_jobs": 1,
    "max_queued_jobs": 10,
    "max_context_chars": 30_000,
    "max_telemetry_chars": 20_000,
    "conversation_retention_days": 30,
    "notify_long_running_jobs": True,
    "browser_notifications": False,
    "proactive_insights": False,
    "dashboard_summaries": False,
}


def read_ai_config(db: Session, include_secret: bool = False) -> dict[str, Any]:
    row = db.get(Setting, "ai")
    value = AI_DEFAULTS | (dict(row.value) if row else {})
    encrypted = str(value.pop("api_key_encrypted", ""))
    if include_secret:
        value["api_key"] = decrypt_secret(encrypted) if encrypted else ""
    else:
        value["api_key_configured"] = bool(encrypted)
    return value
