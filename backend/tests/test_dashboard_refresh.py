import asyncio
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from serversense.db import Base
from serversense.models import Setting, StorageSample
from serversense.services import dashboard_insights as insights


@pytest.fixture
def summary_db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add(
            StorageSample(
                timestamp=datetime.now(UTC),
                total_bytes=10000,
                used_bytes=4000,
                free_bytes=6000,
                source="refresh-test",
            )
        )
        db.commit()
        yield db
    engine.dispose()


def config() -> dict[str, Any]:
    return {
        "provider": "ollama",
        "model": "summary-model",
        "endpoint": "http://ollama.test",
        "dashboard_summaries": True,
        "max_runtime_seconds": 30,
        "max_output_tokens": 512,
    }


@pytest.mark.parametrize(
    "category",
    ["context_limit", "output_limit", "subscription_exhausted", "authentication", "empty_response"],
)
def test_dashboard_preserves_codex_category_with_safe_guidance(
    summary_db: Session,
    category: insights.codex.CodexFailureCategory,
) -> None:
    failure = insights.record_dashboard_summary_failure(
        summary_db,
        config(),
        insights.codex.CodexError("secret-provider-response", category=category),
    )
    summary_db.commit()
    status = insights.dashboard_summary_status(summary_db, config())
    assert failure.data["reason"] == category
    assert status["last_failure_category"] == category
    assert status["failure_guidance"]
    assert "secret-provider-response" not in str(failure.data) + str(status)


def test_dashboard_titles_are_bounded_without_changing_upgrade_counts(
    summary_db: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        insights,
        "media_activity_summary",
        lambda _db, _args: {
            "instances": {"Movies": {"confirmed_quality_upgrades": 100}},
            "confirmed_upgrade_titles": [{"title": f"Movie {i}"} for i in range(100)],
            "upgrade_titles_truncated": False,
        },
    )
    facts = insights._facts(summary_db)
    assert facts is not None
    media = facts["media_activity_7_days"]
    assert len(media["confirmed_upgrade_titles"]) == 10
    assert media["upgrade_titles_truncated"] is True
    assert media["instances"]["Movies"]["confirmed_quality_upgrades"] == 100


def test_manual_refresh_overrides_schedule_and_failure_cooldown(
    summary_db: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    calls = []

    def complete(url: str, **_: Any) -> httpx.Response:
        assert not summary_db.in_transaction()
        calls.append(url)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": "Storage measurements are available."}}],
            },
        )

    monkeypatch.setattr(insights.http_requests, "post", complete)
    now = datetime.now(UTC)
    assert insights.refresh_dashboard_summary(summary_db, config(), now) is not None
    assert (
        insights.refresh_dashboard_summary(summary_db, config(), now + timedelta(seconds=1)) is None
    )
    insights.record_dashboard_summary_failure(
        summary_db, config(), RuntimeError("private provider text"), now + timedelta(seconds=2)
    )
    summary_db.commit()
    assert insights.dashboard_summary_status(summary_db, config())["status"] == "cooldown"
    status = insights.request_dashboard_summary(summary_db, config())
    assert status["status"] == "queued"
    assert not status["can_refresh"]
    assert len(calls) == 1  # Request only queues; no inference.
    row = summary_db.get(Setting, insights.REFRESH_STATE_KEY)
    assert row is not None
    requested_at = row.value["updated_at"]
    insights.request_dashboard_summary(summary_db, config())
    assert row.value["updated_at"] == requested_at
    assert (
        insights.refresh_dashboard_summary(
            summary_db, config(), now + timedelta(seconds=3), force=True
        )
        is not None
    )
    assert len(calls) == 2
    assert insights.dashboard_summary_status(summary_db, config())["status"] == "ready"


def test_manual_refresh_still_yields_to_interactive_sense(
    summary_db: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(insights, "_interactive_ai_busy", lambda _: True)
    monkeypatch.setattr(
        insights.http_requests, "post", lambda *_a, **_k: pytest.fail("inference while busy")
    )
    status = insights.request_dashboard_summary(summary_db, config())
    assert status["status"] == "waiting"
    assert not status["can_refresh"]
    assert insights.refresh_dashboard_summary(summary_db, config(), force=True) is None
    row = summary_db.get(Setting, insights.REFRESH_STATE_KEY)
    assert row is not None and row.value["state"] == "queued"


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"dashboard_summaries": False}, "disabled"),
        ({"provider": "disabled"}, "unconfigured"),
        ({"model": ""}, "unconfigured"),
    ],
)
def test_manual_refresh_requires_saved_opt_in_and_model(
    summary_db: Session,
    changes: dict[str, Any],
    expected: str,
) -> None:
    status = insights.request_dashboard_summary(summary_db, config() | changes)
    assert status["status"] == expected
    assert not status["can_refresh"]
    assert summary_db.get(Setting, insights.REFRESH_STATE_KEY) is None


def test_dashboard_explains_missing_storage_and_safe_failure(
    summary_db: Session,
) -> None:
    failure = insights.record_dashboard_summary_failure(
        summary_db, config(), ValueError("sensitive content")
    )
    summary_db.commit()
    status = insights.dashboard_summary_status(summary_db, config())
    assert status["last_failure_category"] == "invalid_response"
    assert "sensitive" not in str(status) and "sensitive" not in str(failure.data)
    sample = summary_db.query(StorageSample).one()
    summary_db.delete(sample)
    summary_db.commit()
    status = insights.request_dashboard_summary(summary_db, config())
    assert status["status"] == "no_data"
    assert not status["can_refresh"]


def test_dashboard_refresh_authentication_and_mutation_header(client: TestClient) -> None:
    assert client.post("/api/dashboard/summary/refresh").status_code == 401


def test_dashboard_refresh_endpoint_only_queues(
    authenticated_client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    from serversense.api import monitoring

    monkeypatch.setattr(
        monitoring,
        "request_dashboard_summary",
        lambda _db, _config: {
            "status": "queued",
            "can_refresh": False,
            "message": "Queued",
        },
    )
    monkeypatch.setattr(
        insights.http_requests, "post", lambda *_a, **_k: pytest.fail("inference from API")
    )
    assert authenticated_client.post("/api/dashboard/summary/refresh").status_code == 202
    assert (
        authenticated_client.post(
            "/api/dashboard/summary/refresh", headers={"X-ServerSense-Request": ""}
        ).status_code
        == 403
    )


def test_worker_consumes_request_and_logs_only_safe_failure_category(
    summary_db: Session,
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from serversense.services import jobs

    insights.request_dashboard_summary(summary_db, config())
    monkeypatch.setattr(
        jobs, "SessionLocal", lambda: Session(summary_db.get_bind(), expire_on_commit=False)
    )
    monkeypatch.setattr(jobs, "read_ai_config", lambda _db, **_kwargs: config())

    def fail(_db: Session, _config: dict[str, Any], *, force: bool) -> None:
        assert force
        raise ValueError("sensitive-provider-response")

    monkeypatch.setattr(jobs, "refresh_dashboard_summary", fail)
    assert jobs.dashboard_summary_cycle() is False
    summary_db.expire_all()
    row = summary_db.get(Setting, insights.REFRESH_STATE_KEY)
    assert row is not None and row.value["state"] == "failed"
    assert insights.dashboard_summary_status(summary_db, config())["status"] == "cooldown"
    assert "invalid_response" in caplog.text
    assert "sensitive-provider-response" not in caplog.text


def test_worker_wakes_immediately_and_survives_temporary_check_failure(
    summary_db: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    from serversense.services import jobs

    monkeypatch.setattr(
        jobs, "SessionLocal", lambda: Session(summary_db.get_bind(), expire_on_commit=False)
    )

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        first, second = asyncio.Event(), asyncio.Event()
        calls = 0

        def cycle() -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                loop.call_soon_threadsafe(first.set)
                raise RuntimeError("temporary database failure")
            loop.call_soon_threadsafe(second.set)
            return False

        monkeypatch.setattr(jobs, "dashboard_summary_cycle", cycle)
        worker = asyncio.create_task(jobs.dashboard_summary_loop())
        try:
            await asyncio.sleep(0)
            jobs.wake_dashboard_summary()
            await asyncio.wait_for(first.wait(), timeout=2)
            jobs.wake_dashboard_summary()
            await asyncio.wait_for(second.wait(), timeout=2)
            assert calls == 2
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    asyncio.run(exercise())
