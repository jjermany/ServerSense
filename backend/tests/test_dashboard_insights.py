from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pytest import MonkeyPatch
from sqlalchemy import delete

from serversense.db import SessionLocal
from serversense.models import Alert, Event, StorageSample
from serversense.services.dashboard_insights import (
    latest_dashboard_summary,
    refresh_dashboard_summary,
)
from serversense.services.storage import latest_storage_sample


def _config(enabled: bool = True, max_runtime_seconds: int = 300) -> dict[str, object]:
    return {
        "provider": "ollama",
        "model": "small-local-model",
        "endpoint": "http://ollama.test:11434",
        "temperature": 0.8,
        "timeout_seconds": 120,
        "max_runtime_seconds": max_runtime_seconds,
        "max_output_tokens": 512,
        "dashboard_summaries": enabled,
    }


def test_dashboard_summary_is_opt_in_bounded_and_cached(monkeypatch: MonkeyPatch) -> None:
    calls: list[dict] = []
    read_timeouts: list[float | None] = []

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        assert not db.in_transaction()
        payload = kwargs["json"]
        assert isinstance(payload, dict)
        calls.append(payload)
        request_timeout = kwargs["timeout"]
        assert isinstance(request_timeout, httpx.Timeout)
        read_timeouts.append(request_timeout.read)
        assert url == "http://ollama.test:11434/v1/chat/completions"
        assert "tools" not in payload
        assert payload["max_tokens"] == 512
        assert payload["reasoning_effort"] == "none"
        assert payload["temperature"] == 0.3
        assert "untrusted JSON data" in payload["messages"][0]["content"]
        assert "combined_array_data_disks" in payload["messages"][0]["content"]
        assert "12-hour format with AM or PM" in payload["messages"][0]["content"]
        assert "never call them scheduled imports" in payload["messages"][0]["content"]
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

        event = refresh_dashboard_summary(db, _config(max_runtime_seconds=600), now)
        assert event is not None
        assert event.message == (
            "Storage remains healthy. Recent activity is measured and no active issue needs attention."
        )
        assert event.data["model"] == "small-local-model"
        assert event.data["storage_source"] == "test"
        assert read_timeouts == [600.0]
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
        assert read_timeouts == [600.0, 300.0]

        replacement = StorageSample(
            timestamp=now + timedelta(minutes=17),
            total_bytes=20_000,
            used_bytes=8_000,
            free_bytes=12_000,
            source="unraid_array",
        )
        db.add(replacement)
        db.commit()
        assert latest_dashboard_summary(db, now + timedelta(minutes=17)) is None

        db.delete(alert)
        db.delete(replacement)
        db.execute(delete(Event).where(Event.event_type == "sense_dashboard_summary"))
        db.commit()


def test_failed_refresh_preserves_recent_cached_summary(monkeypatch: MonkeyPatch) -> None:
    now = datetime.now(UTC)

    def fail_post(url: str, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("offline", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fail_post)
    with SessionLocal() as db:
        db.execute(delete(Event).where(Event.event_type == "sense_dashboard_summary"))
        storage = latest_storage_sample(db)
        cached = Event(
            timestamp=now - timedelta(hours=7),
            event_type="sense_dashboard_summary",
            severity="info",
            title="Current server summary",
            message="The last successful cached summary.",
            data={
                "source": "model",
                "model": "small-local-model",
                "storage_source": storage.source if storage else None,
            },
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


def test_dashboard_summary_rejects_military_time_and_guaranteed_import_claims(
    monkeypatch: MonkeyPatch,
) -> None:
    now = datetime.now(UTC) + timedelta(days=1)

    def invalid_post(url: str, **_: object) -> httpx.Response:
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "Mayday is scheduled for import at 19:00 local time, "
                                "with no immediate risk of exhaustion."
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", invalid_post)
    with SessionLocal() as db:
        db.execute(delete(Event).where(Event.event_type == "sense_dashboard_summary"))
        sample = StorageSample(
            timestamp=now,
            total_bytes=10_000,
            used_bytes=9_760,
            free_bytes=240,
            source="summary-policy-test",
        )
        db.add(sample)
        db.commit()
        try:
            with pytest.raises(ValueError, match="presentation policy"):
                refresh_dashboard_summary(db, _config(), now)

            cached = Event(
                timestamp=now,
                event_type="sense_dashboard_summary",
                severity="info",
                title="Current server summary",
                message="Mayday is scheduled for import at 19:00 local time.",
                data={
                    "source": "model",
                    "model": "small-local-model",
                    "storage_source": "summary-policy-test",
                    "storage_forecast_days": None,
                },
            )
            db.add(cached)
            db.commit()
            assert latest_dashboard_summary(db, now) is None
            db.delete(cached)
        finally:
            db.delete(sample)
            db.commit()
