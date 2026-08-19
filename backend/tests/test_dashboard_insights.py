from datetime import UTC, datetime, timedelta

import httpx
from pytest import MonkeyPatch
from sqlalchemy import delete

from serversense.db import SessionLocal
from serversense.models import Alert, Event, StorageSample
from serversense.services.dashboard_insights import (
    latest_dashboard_summary,
    refresh_dashboard_summary,
)


def _config(enabled: bool = True) -> dict[str, object]:
    return {
        "provider": "ollama",
        "model": "small-local-model",
        "endpoint": "http://ollama.test:11434",
        "temperature": 0.8,
        "timeout_seconds": 120,
        "max_output_tokens": 200,
        "dashboard_summaries": enabled,
    }


def test_dashboard_summary_is_opt_in_bounded_and_cached(monkeypatch: MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        payload = kwargs["json"]
        assert isinstance(payload, dict)
        calls.append(payload)
        assert url == "http://ollama.test:11434/v1/chat/completions"
        assert "tools" not in payload
        assert payload["max_tokens"] == 200
        assert payload["temperature"] == 0.3
        assert "untrusted JSON data" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": "  Storage remains healthy.  Recent activity is measured and no active issue needs attention. "
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.execute(delete(Event).where(Event.event_type == "sense_dashboard_summary"))
        db.add(
            StorageSample(
                timestamp=now,
                total_bytes=10_000,
                used_bytes=4_000,
                free_bytes=6_000,
                source="test",
            )
        )
        db.commit()

        assert refresh_dashboard_summary(db, _config(False), now) is None
        assert calls == []

        event = refresh_dashboard_summary(db, _config(), now)
        assert event is not None
        assert event.message == (
            "Storage remains healthy. Recent activity is measured and no active issue needs attention."
        )
        assert event.data["model"] == "small-local-model"
        assert refresh_dashboard_summary(db, _config(), now + timedelta(minutes=1)) is None
        assert len(calls) == 1

        alert = Alert(
            alert_type="test_dashboard_refresh",
            severity="warning",
            title="A new measured warning",
            message="A normalized warning appeared.",
            fingerprint=f"dashboard-summary-{now.timestamp()}",
            created_at=now + timedelta(minutes=16),
            updated_at=now + timedelta(minutes=16),
            data={},
        )
        db.add(alert)
        db.commit()
        refreshed = refresh_dashboard_summary(db, _config(), now + timedelta(minutes=16))
        assert refreshed is not None
        assert refreshed.severity == "warning"
        assert len(calls) == 2

        db.delete(alert)
        db.execute(delete(Event).where(Event.event_type == "sense_dashboard_summary"))
        db.commit()


def test_failed_refresh_preserves_recent_cached_summary(monkeypatch: MonkeyPatch) -> None:
    now = datetime.now(UTC)

    def fail_post(url: str, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("offline", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fail_post)
    with SessionLocal() as db:
        db.execute(delete(Event).where(Event.event_type == "sense_dashboard_summary"))
        cached = Event(
            timestamp=now - timedelta(hours=7),
            event_type="sense_dashboard_summary",
            severity="info",
            title="Current server summary",
            message="The last successful cached summary.",
            data={"source": "model", "model": "small-local-model"},
        )
        db.add(cached)
        db.commit()

        try:
            refresh_dashboard_summary(db, _config(), now)
        except httpx.ConnectError:
            pass
        else:
            raise AssertionError("failed model request did not raise")

        assert latest_dashboard_summary(db, now) is not None
        assert latest_dashboard_summary(db, now).message == "The last successful cached summary."
        assert latest_dashboard_summary(db, now + timedelta(hours=6)) is None
        db.delete(cached)
        db.commit()
