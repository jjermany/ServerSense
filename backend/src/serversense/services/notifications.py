import smtplib
import ssl
from abc import ABC, abstractmethod
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from serversense.models import Alert, Setting
from serversense.services.secrets import decrypt_secret


class NotificationProvider(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> None: ...


def _alert_payload(alert: Alert) -> dict[str, Any]:
    return {
        "source": "ServerSense",
        "type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.title,
        "message": alert.message,
        "timestamp": alert.created_at.isoformat(),
        "data": alert.data,
    }


def _http_url(value: str, label: str) -> str:
    if not value.startswith(("http://", "https://")):
        raise ValueError(f"{label} must use HTTP or HTTPS")
    return value


class WebhookProvider(NotificationProvider):
    def __init__(self, url: str, timeout: float = 10):
        self.url = _http_url(url, "Webhook URL")
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        httpx.post(self.url, json=_alert_payload(alert), timeout=self.timeout).raise_for_status()


class DiscordProvider(NotificationProvider):
    def __init__(self, webhook_url: str, timeout: float = 10):
        parsed = urlparse(webhook_url)
        hostname = parsed.hostname or ""
        valid_host = hostname in {"discord.com", "discordapp.com"} or hostname.endswith(
            (".discord.com", ".discordapp.com")
        )
        if (
            parsed.scheme != "https"
            or not valid_host
            or not parsed.path.startswith("/api/webhooks/")
        ):
            raise ValueError("Discord webhook URL must be an HTTPS discord.com webhook")
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        colors = {"critical": 0xED4245, "warning": 0xFEE75C, "info": 0x5865F2}
        payload = {
            "username": "ServerSense",
            "embeds": [
                {
                    "title": alert.title[:256],
                    "description": alert.message[:4096],
                    "color": colors.get(alert.severity.lower(), colors["info"]),
                    "timestamp": alert.created_at.isoformat(),
                    "footer": {"text": f"{alert.severity.upper()} · {alert.alert_type}"},
                }
            ],
        }
        httpx.post(self.webhook_url, json=payload, timeout=self.timeout).raise_for_status()


class PushoverProvider(NotificationProvider):
    endpoint = "https://api.pushover.net/1/messages.json"

    def __init__(self, user_key: str, app_token: str, timeout: float = 10):
        if not user_key or not app_token:
            raise ValueError("Pushover user key and application token are required")
        self.user_key = user_key
        self.app_token = app_token
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        priority = 1 if alert.severity.lower() == "critical" else 0
        httpx.post(
            self.endpoint,
            data={
                "user": self.user_key,
                "token": self.app_token,
                "title": alert.title[:250],
                "message": alert.message[:1024],
                "priority": priority,
            },
            timeout=self.timeout,
        ).raise_for_status()


class EmailProvider(NotificationProvider):
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        security: str,
        sender: str,
        recipient: str,
        timeout: float = 10,
    ):
        if not host or any(character.isspace() for character in host):
            raise ValueError("A valid SMTP host is required")
        if not sender or not recipient:
            raise ValueError("SMTP host, sender, and recipient are required")
        if any("\n" in value or "\r" in value for value in (sender, recipient)):
            raise ValueError("Email addresses cannot contain line breaks")
        if security not in {"starttls", "tls", "none"}:
            raise ValueError("Unsupported SMTP security mode")
        if any(
            parseaddr(address)[1] != address or "@" not in address
            for address in (sender, recipient)
        ):
            raise ValueError("Valid sender and recipient email addresses are required")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.security = security
        self.sender = sender
        self.recipient = recipient
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        message = EmailMessage()
        message["Subject"] = f"[ServerSense {alert.severity.upper()}] {alert.title}"[:998]
        message["From"] = self.sender
        message["To"] = self.recipient
        message.set_content(
            f"{alert.message}\n\nAlert type: {alert.alert_type}\n"
            f"Severity: {alert.severity}\nTime: {alert.created_at.isoformat()}\n"
        )
        context = ssl.create_default_context()
        if self.security == "tls":
            with smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=context
            ) as smtp:
                self._deliver(smtp, message)
            return
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            if self.security == "starttls":
                smtp.starttls(context=context)
            self._deliver(smtp, message)

    def _deliver(self, smtp: smtplib.SMTP, message: EmailMessage) -> None:
        if self.username:
            smtp.login(self.username, self.password)
        smtp.send_message(message)


def _secret(value: dict[str, Any], key: str) -> str:
    encrypted = str(value.get(f"{key}_encrypted", ""))
    return decrypt_secret(encrypted) if encrypted else ""


def provider_from_config(key: str, value: dict[str, Any]) -> NotificationProvider:
    if key == "webhook":
        return WebhookProvider(_secret(value, "webhook_url"))
    if key == "discord":
        return DiscordProvider(_secret(value, "discord_webhook_url"))
    if key == "pushover":
        return PushoverProvider(
            _secret(value, "pushover_user_key"), _secret(value, "pushover_app_token")
        )
    if key == "email":
        return EmailProvider(
            host=str(value.get("smtp_host", "")),
            port=int(value.get("smtp_port", 587)),
            username=_secret(value, "smtp_username"),
            password=_secret(value, "smtp_password"),
            security=str(value.get("smtp_security", "starttls")),
            sender=str(value.get("email_from", "")),
            recipient=str(value.get("email_to", "")),
        )
    raise ValueError(f"Unknown notification provider: {key}")


def configured_providers(db: Session) -> list[NotificationProvider]:
    row = db.get(Setting, "alerts")
    if not row:
        return []
    providers: list[NotificationProvider] = []
    for key in ("webhook", "discord", "pushover", "email"):
        if row.value.get(f"{key}_enabled"):
            try:
                providers.append(provider_from_config(key, row.value))
            except (TypeError, ValueError):
                continue
    return providers


DELIVERY_ERRORS = (httpx.HTTPError, smtplib.SMTPException, OSError, ValueError)
NOTIFICATION_PREFERENCES = {
    "storage_low": "notify_storage_low",
    "forecast_low": "notify_forecast_low",
    "disk_smart": "notify_disk_smart",
    "disk_temperature": "notify_disk_temperature",
    "container_stopped": "notify_container_stopped",
}


def notification_enabled(value: dict[str, Any], alert: Alert) -> bool:
    preference = NOTIFICATION_PREFERENCES.get(alert.alert_type)
    return preference is None or bool(value.get(preference, True))


def dispatch_notifications(db: Session, alerts: list[Alert]) -> list[str]:
    failures: list[str] = []
    row = db.get(Setting, "alerts")
    values = row.value if row else {}
    selected_alerts = [alert for alert in alerts if notification_enabled(values, alert)]
    for provider in configured_providers(db):
        for alert in selected_alerts:
            try:
                provider.send(alert)
            except DELIVERY_ERRORS as exc:
                failures.append(type(exc).__name__)
    return failures
