from typing import Any

from sqlalchemy.orm import Session

from serversense.models import Setting
from serversense.services.secrets import decrypt_secret


def read_ai_config(db: Session, include_secret: bool = False) -> dict[str, Any]:
    row = db.get(Setting, "ai")
    value = (
        dict(row.value)
        if row
        else {
            "provider": "disabled",
            "model": "",
            "endpoint": "",
            "context_window": 8192,
            "temperature": 0.2,
            "timeout_seconds": 60,
            "max_tool_calls": 5,
            "proactive_insights": False,
        }
    )
    value.setdefault("proactive_insights", False)
    encrypted = str(value.pop("api_key_encrypted", ""))
    if include_secret:
        value["api_key"] = decrypt_secret(encrypted) if encrypted else ""
    else:
        value["api_key_configured"] = bool(encrypted)
    return value
