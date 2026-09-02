from datetime import UTC, datetime

import httpx
from pytest import MonkeyPatch

from serversense.models import Alert, Setting
from serversense.services.notifications import (
    DiscordProvider,
    EmailProvider,
    PushoverProvider,
    WebhookProvider,
    dispatch_notifications,
)
from serversense.services.secrets import encrypt_secret


def sample_alert() -> Alert:
    alert = Alert(
        alert_type="disk_temperature",
        severity="warning",
        title="Disk is hot",
        message="Disk temperature is 52°C.",
        fingerprint="test-hot-disk",
        data={"temperature_c": 52},
    )
    alert.created_at = datetime.now(UTC)
    return alert


def test_generic_webhook_sends_structured_alert(monkeypatch: MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    WebhookProvider("https://notifications.test/serversense").send(sample_alert())
    assert captured["url"] == "https://notifications.test/serversense"
    assert captured["json"]["source"] == "ServerSense"
    assert captured["json"]["data"]["temperature_c"] == 52


def test_discord_sends_embed_and_pushover_sends_form(monkeypatch: MonkeyPatch) -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        requests.append((url, kwargs))
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    DiscordProvider("https://discord.com/api/webhooks/1/token").send(sample_alert())
    PushoverProvider("user-key", "app-token").send(sample_alert())

    discord_payload = requests[0][1]["json"]
    assert isinstance(discord_payload, dict)
    embeds = discord_payload["embeds"]
    assert isinstance(embeds, list)
    assert embeds[0]["title"] == "Disk is hot"
    assert requests[1][0] == PushoverProvider.endpoint
    pushover_data = requests[1][1]["data"]
    assert isinstance(pushover_data, dict)
    assert pushover_data["user"] == "user-key"


def test_email_uses_starttls_and_authentication(monkeypatch: MonkeyPatch) -> None:
    calls: list[object] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float):
            calls.append((host, port, timeout))

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def starttls(self, **kwargs: object) -> None:
            calls.append("starttls")

        def login(self, username: str, password: str) -> None:
            calls.append((username, password))

        def send_message(self, message: object) -> None:
            calls.append(message)

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    EmailProvider(
        host="smtp.test",
        port=587,
        username="alerts",
        password="secret",
        security="starttls",
        sender="alerts@test.invalid",
        recipient="admin@test.invalid",
    ).send(sample_alert())

    assert calls[0] == ("smtp.test", 587, 10)
    assert "starttls" in calls
    assert ("alerts", "secret") in calls


def test_dispatch_delivers_new_alert_to_every_enabled_provider(
    monkeypatch: MonkeyPatch,
) -> None:
    setting = Setting(
        key="alerts",
        secret=True,
        value={
            "webhook_enabled": True,
            "webhook_url_encrypted": encrypt_secret("https://notifications.test/hook"),
            "discord_enabled": True,
            "discord_webhook_url_encrypted": encrypt_secret(
                "https://discord.com/api/webhooks/1/token"
            ),
            "pushover_enabled": True,
            "pushover_user_key_encrypted": encrypt_secret("user-key"),
            "pushover_app_token_encrypted": encrypt_secret("app-token"),
            "email_enabled": True,
            "smtp_host": "smtp.test",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_username_encrypted": encrypt_secret("alerts"),
            "smtp_password_encrypted": encrypt_secret("secret"),
            "email_from": "alerts@test.invalid",
            "email_to": "admin@test.invalid",
        },
    )

    class FakeSession:
        def get(self, model: type[Setting], key: str) -> Setting | None:
            assert model is Setting
            return setting if key == "alerts" else None

    delivered: list[str] = []
    for provider_type in (WebhookProvider, DiscordProvider, PushoverProvider, EmailProvider):
        monkeypatch.setattr(
            provider_type,
            "send",
            lambda self, alert, name=provider_type.__name__: delivered.append(name),
        )

    failures = dispatch_notifications(FakeSession(), [sample_alert()])  # type: ignore[arg-type]

    assert failures == []
    assert delivered == [
        "WebhookProvider",
        "DiscordProvider",
        "PushoverProvider",
        "EmailProvider",
    ]


def test_dispatch_filters_disabled_notification_categories(
    monkeypatch: MonkeyPatch,
) -> None:
    setting = Setting(
        key="alerts",
        secret=True,
        value={
            "webhook_enabled": True,
            "webhook_url_encrypted": encrypt_secret("https://notifications.test/hook"),
            "notify_storage_low": True,
            "notify_forecast_low": False,
        },
    )

    class FakeSession:
        def get(self, model: type[Setting], key: str) -> Setting | None:
            assert model is Setting
            return setting if key == "alerts" else None

    storage_alert = sample_alert()
    storage_alert.alert_type = "storage_low"
    forecast_alert = sample_alert()
    forecast_alert.alert_type = "forecast_low"
    delivered: list[str] = []
    monkeypatch.setattr(
        WebhookProvider,
        "send",
        lambda self, alert: delivered.append(alert.alert_type),
    )

    failures = dispatch_notifications(  # type: ignore[arg-type]
        FakeSession(), [storage_alert, forecast_alert]
    )

    assert failures == []
    assert delivered == ["storage_low"]


def test_dispatch_filters_sense_job_notifications(monkeypatch: MonkeyPatch) -> None:
    setting = Setting(
        key="alerts",
        secret=True,
        value={
            "webhook_enabled": True,
            "webhook_url_encrypted": encrypt_secret("https://notifications.test/hook"),
            "notify_sense_jobs": False,
        },
    )

    class FakeSession:
        def get(self, model: type[Setting], key: str) -> Setting | None:
            return setting if model is Setting and key == "alerts" else None

    sense_notice = sample_alert()
    sense_notice.alert_type = "sense_job"
    delivered: list[str] = []
    monkeypatch.setattr(
        WebhookProvider,
        "send",
        lambda self, alert: delivered.append(alert.alert_type),
    )

    failures = dispatch_notifications(FakeSession(), [sense_notice])  # type: ignore[arg-type]

    assert failures == []
    assert delivered == []
