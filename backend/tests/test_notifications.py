from datetime import UTC, datetime

import httpx
from pytest import MonkeyPatch

from serversense.models import Alert
from serversense.services.notifications import (
    DiscordProvider,
    EmailProvider,
    PushoverProvider,
    WebhookProvider,
)


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
