from abc import ABC, abstractmethod
from typing import Any

import httpx
from sqlalchemy.orm import Session

from serversense.models import Alert, Setting
from serversense.services.secrets import decrypt_secret


class NotificationProvider(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> None: ...


class WebhookProvider(NotificationProvider):
    def __init__(self, url: str, timeout: float = 10):
        if not url.startswith(("http://", "https://")):
            raise ValueError("Webhook URL must use HTTP or HTTPS")
        self.url = url
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        payload: dict[str, Any] = {
            "source": "ServerSense",
            "type": alert.alert_type,
            "severity": alert.severity,
            "title": alert.title,
            "message": alert.message,
            "timestamp": alert.created_at.isoformat(),
            "data": alert.data,
        }
        httpx.post(self.url, json=payload, timeout=self.timeout).raise_for_status()


def configured_providers(db: Session) -> list[NotificationProvider]:
    row = db.get(Setting, "alerts")
    if not row or not row.value.get("webhook_enabled"):
        return []
    encrypted = str(row.value.get("webhook_url_encrypted", ""))
    url = decrypt_secret(encrypted) if encrypted else ""
    return [WebhookProvider(url)] if url else []


def dispatch_notifications(db: Session, alerts: list[Alert]) -> list[str]:
    failures: list[str] = []
    for provider in configured_providers(db):
        for alert in alerts:
            try:
                provider.send(alert)
            except (httpx.HTTPError, ValueError) as exc:
                failures.append(type(exc).__name__)
    return failures
