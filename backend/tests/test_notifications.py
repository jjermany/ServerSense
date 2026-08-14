from datetime import UTC, datetime

import httpx
from pytest import MonkeyPatch

from serversense.models import Alert
from serversense.services.notifications import WebhookProvider


def test_generic_webhook_sends_structured_alert(monkeypatch: MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    alert = Alert(
        alert_type="disk_temperature",
        severity="warning",
        title="Disk is hot",
        message="Disk temperature is 52°C.",
        fingerprint="test-hot-disk",
        data={"temperature_c": 52},
    )
    alert.created_at = datetime.now(UTC)
    WebhookProvider("https://notifications.test/serversense").send(alert)
    assert captured["url"] == "https://notifications.test/serversense"
    assert captured["json"]["source"] == "ServerSense"
    assert captured["json"]["data"]["temperature_c"] == 52
