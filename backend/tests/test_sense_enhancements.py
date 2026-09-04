import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from serversense.api.ai import _enqueue
from serversense.db import SessionLocal
from serversense.models import (
    AIConversation,
    AIJob,
    AIMessage,
    Alert,
    InAppNotification,
    User,
)
from serversense.schemas import ChatRequest
from serversense.services.ai import ChatEvent
from serversense.services.sense_jobs import (
    NOTIFICATION_SUMMARY_LIMIT,
    _curated_context,
    _notification_summary,
    _run_job,
    _safe_error,
    cancel_job,
    config_snapshot,
    create_job,
    queue_position,
    reconcile_interrupted_jobs,
)
from serversense.services.sense_router import RoutedRequest, classify_intent


@pytest.fixture(autouse=True)
def isolate_external_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "serversense.services.sense_jobs.dispatch_notifications",
        lambda db, alerts: [],
    )


def test_intent_router_distinguishes_facts_reasoning_and_actions() -> None:
    assert classify_intent("How much storage is free?") == "telemetry"
    assert classify_intent("Is Plex running?") == "status"
    assert classify_intent("Why is that disk hot?") == "analysis"
    assert classify_intent("What changed over time?") == "historical"
    assert classify_intent("Restart Plex") == "action"


def test_notification_summary_is_plain_text_and_capped() -> None:
    answer = "## Result\n\n[Storage](https://example.invalid) is healthy. " + (
        "Capacity remains stable. " * 20
    )

    summary = _notification_summary(answer)

    assert len(summary) <= NOTIFICATION_SUMMARY_LIMIT
    assert "##" not in summary
    assert "https://" not in summary
    assert summary.startswith("Result Storage is healthy.")


def test_provider_read_timeout_names_the_limit_that_fired() -> None:
    error = _safe_error(
        httpx.ReadTimeout("provider stalled"),
        {"timeout_seconds": 120, "max_runtime_seconds": 600},
    )

    assert "sent no response data for 120 seconds" in error
    assert "separate from the configured 600-second maximum runtime" in error


def test_provider_connect_timeout_names_the_short_connection_limit() -> None:
    error = _safe_error(
        httpx.ConnectTimeout("provider unavailable"),
        {"timeout_seconds": 600, "max_runtime_seconds": 600},
    )

    assert "connect to the model provider within 10 seconds" in error
    assert "separate from the configured 600-second maximum runtime" in error


def test_broad_change_summary_preloads_historical_sources(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_tool(db, name, arguments):
        calls.append((name, arguments))
        return {"source": name}

    monkeypatch.setattr("serversense.services.sense_jobs.execute_tool", fake_tool)
    with SessionLocal() as db:
        context = _curated_context(
            db,
            "What changed on my server today?",
            "historical",
            {},
            30_000,
            20_000,
        )

    assert set(context["telemetry"]) >= {
        "get_storage_history",
        "get_recent_alerts",
        "get_media_activity_summary",
        "get_container_status",
        "get_server_overview",
    }
    assert ("get_storage_history", {"days": 1, "today": True}) in calls
    assert ("get_recent_alerts", {"limit": 100, "today": True}) in calls
    assert ("get_media_activity_summary", {"days": 1, "today": True}) in calls


def test_completed_conversation_message_includes_persisted_elapsed_time(
    authenticated_client: TestClient,
) -> None:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "administrator"))
        assert user is not None
        conversation = AIConversation(title="Elapsed time")
        db.add(conversation)
        db.flush()
        question = AIMessage(
            conversation_id=conversation.id,
            timestamp=now - timedelta(seconds=13),
            role="user",
            content="What changed?",
            source="user",
            references={},
        )
        answer = AIMessage(
            conversation_id=conversation.id,
            timestamp=now,
            role="assistant",
            content="Measured changes.",
            source="sense_ai",
            provider="ollama",
            model="test-model",
            references={},
        )
        db.add_all([question, answer])
        db.flush()
        job = create_job(
            db,
            user.id,
            conversation,
            question,
            "historical",
            {"provider": "ollama", "model": "test-model"},
            [],
        )
        job.status = "completed"
        job.started_at = now - timedelta(seconds=12.5)
        job.completed_at = now
        job.response_message_id = answer.id
        db.commit()
        conversation_id = conversation.id

    response = authenticated_client.get(f"/api/ai/conversations/{conversation_id}")

    assert response.status_code == 200
    assistant = next(row for row in response.json()["messages"] if row["role"] == "assistant")
    assert assistant["elapsed_seconds"] == 12.5


def test_direct_response_has_serversense_provenance_and_never_needs_ai(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/api/ai/chat", json={"message": "How much storage is free?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "serversense"
    assert body["tools_used"] == ["get_array_capacity"]
    assert "cache pool" in body["message"] or "combined array data disks" in body["message"]
    with SessionLocal() as db:
        message = db.scalar(
            select(AIMessage)
            .where(
                AIMessage.conversation_id == body["conversation_id"],
                AIMessage.role == "assistant",
            )
            .order_by(AIMessage.id.desc())
        )
        assert message is not None
        assert message.source == "serversense"
        assert message.model is None


def test_reasoning_requires_configured_ai_but_read_only_action_does_not(
    authenticated_client: TestClient,
) -> None:
    reasoning = authenticated_client.post(
        "/api/ai/chat", json={"message": "Why is my hottest disk hot?"}
    )
    assert reasoning.status_code == 409
    action = authenticated_client.post("/api/ai/chat", json={"message": "Restart Plex"})
    assert action.status_code == 200
    assert action.json()["source"] == "serversense"
    assert "read-only" in action.json()["message"]


def test_conversation_rename_and_search(authenticated_client: TestClient) -> None:
    created = authenticated_client.post(
        "/api/ai/chat", json={"message": "Which disk is hottest?"}
    ).json()
    conversation_id = created["conversation_id"]
    renamed = authenticated_client.patch(
        f"/api/ai/conversations/{conversation_id}", json={"title": "Thermal review"}
    )
    assert renamed.status_code == 200
    matches = authenticated_client.get("/api/ai/conversations?q=Thermal").json()
    assert any(row["id"] == conversation_id for row in matches)


def test_job_snapshot_excludes_secrets_and_retains_selected_model() -> None:
    snapshot = config_snapshot(
        {
            "provider": "openai_compatible",
            "model": "original-model",
            "endpoint": "http://model.local",
            "api_key": "must-not-persist",
            "temperature": 0.3,
        }
    )
    assert snapshot["model"] == "original-model"
    assert "api_key" not in snapshot


def test_queue_is_fifo_and_rejects_work_at_configured_capacity(monkeypatch) -> None:
    with SessionLocal() as db:
        user = User(
            username=f"queue-owner-{datetime.now(UTC).timestamp()}",
            password_hash="not-used",
            is_admin=True,
        )
        db.add(user)
        db.flush()
        jobs: list[AIJob] = []
        for suffix in ("first", "second"):
            conversation = AIConversation(title=suffix)
            db.add(conversation)
            db.flush()
            message = AIMessage(
                conversation_id=conversation.id,
                timestamp=datetime.now(UTC),
                role="user",
                content=f"Analyze {suffix}",
                source="user",
                references={},
            )
            db.add(message)
            db.flush()
            jobs.append(
                create_job(
                    db,
                    user.id,
                    conversation,
                    message,
                    "analysis",
                    {"provider": "openai_compatible", "model": "queue-model"},
                    [],
                )
            )

        assert queue_position(db, jobs[0]) == 1
        assert queue_position(db, jobs[1]) == 2
        monkeypatch.setattr(
            "serversense.api.ai.read_ai_config",
            lambda db, include_secret=False: {
                "provider": "openai_compatible",
                "model": "queue-model",
                "max_queued_jobs": 2,
            },
        )
        with pytest.raises(HTTPException) as exc_info:
            _enqueue(
                db,
                user,
                None,
                ChatRequest(message="Analyze one more request"),
                RoutedRequest(intent="analysis", direct=False),
            )
        assert exc_info.value.status_code == 429
        assert "queue is full" in str(exc_info.value.detail)


def test_direct_telemetry_remains_available_during_active_ai_job(
    authenticated_client: TestClient,
) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "administrator"))
        assert user is not None
        conversation = AIConversation(title="Active analysis")
        db.add(conversation)
        db.flush()
        message = AIMessage(
            conversation_id=conversation.id,
            timestamp=datetime.now(UTC),
            role="user",
            content="Explain the last day",
            source="user",
            references={},
        )
        db.add(message)
        db.flush()
        job = create_job(
            db,
            user.id,
            conversation,
            message,
            "analysis",
            {"provider": "openai_compatible", "model": "busy-model"},
            [],
        )
        job.status = "analyzing"
        job.started_at = datetime.now(UTC)
        db.commit()
        job_id = job.id
        conversation_id = conversation.id

    response = authenticated_client.post(
        "/api/ai/direct",
        json={"message": "How much storage is free?", "conversation_id": conversation_id},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "serversense"
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job is not None
        assert job.status == "analyzing"


async def test_fast_completed_job_persists_ai_provenance_without_notification(monkeypatch) -> None:
    captured_model = ""

    async def fake_stream(db, question, config, history):
        nonlocal captured_model
        captured_model = str(config["model"])
        yield ChatEvent("delta", "Grounded answer")
        yield ChatEvent(
            "complete",
            message="Grounded answer",
            tools=("get_server_overview",),
            model=str(config["model"]),
        )

    monkeypatch.setattr("serversense.services.sense_jobs.chat_stream", fake_stream)
    with SessionLocal() as db:
        user = User(
            username=f"job-owner-{datetime.now(UTC).timestamp()}",
            password_hash="not-used",
            is_admin=True,
        )
        conversation = AIConversation(title="Durable job")
        db.add_all([user, conversation])
        db.flush()
        message = AIMessage(
            conversation_id=conversation.id,
            timestamp=datetime.now(UTC),
            role="user",
            content="Explain the current server state",
            source="user",
            references={},
        )
        db.add(message)
        db.flush()
        job = create_job(
            db,
            user.id,
            conversation,
            message,
            "analysis",
            {
                "provider": "openai_compatible",
                "model": "snapshot-model",
                "endpoint": "http://model.invalid",
                "tool_calling": "curated_context",
                "background_threshold_seconds": 30,
            },
            [],
        )
        job_id = job.id

    await _run_job(job_id)

    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job is not None
        assert job.status == "completed"
        assert captured_model == "snapshot-model"
        response = db.get(AIMessage, job.response_message_id)
        assert response is not None
        assert response.source == "sense_ai"
        assert response.model == "snapshot-model"
        notice = db.scalar(select(InAppNotification).where(InAppNotification.job_id == job_id))
        assert notice is None


async def test_backgrounded_job_notifies_once_and_respects_job_preference(monkeypatch) -> None:
    delivered: list[Alert] = []

    def fake_dispatch(db, alerts):
        delivered.extend(alerts)
        return []

    monkeypatch.setattr("serversense.services.sense_jobs.dispatch_notifications", fake_dispatch)

    async def fake_stream(db, question, config, history):
        await asyncio.sleep(0.02)
        answer = "Storage is healthy. " + ("Capacity remains stable. " * 20)
        yield ChatEvent("delta", answer)
        yield ChatEvent("complete", message=answer, model=str(config["model"]))

    monkeypatch.setattr("serversense.services.sense_jobs.chat_stream", fake_stream)
    job_ids: list[str] = []
    with SessionLocal() as db:
        user = User(
            username=f"notify-owner-{datetime.now(UTC).timestamp()}",
            password_hash="not-used",
            is_admin=True,
        )
        db.add(user)
        db.flush()
        for notify in (True, False):
            conversation = AIConversation(title=f"Notify {notify}")
            db.add(conversation)
            db.flush()
            message = AIMessage(
                conversation_id=conversation.id,
                timestamp=datetime.now(UTC),
                role="user",
                content="Explain it",
                source="user",
                references={},
            )
            db.add(message)
            db.flush()
            job = create_job(
                db,
                user.id,
                conversation,
                message,
                "analysis",
                {
                    "provider": "openai_compatible",
                    "model": "test-model",
                    "endpoint": "http://model.invalid",
                    "tool_calling": "curated_context",
                    "background_threshold_seconds": 0,
                    "notify_long_running_jobs": notify,
                },
                [],
            )
            job_ids.append(job.id)

    for job_id in job_ids:
        await _run_job(job_id)

    with SessionLocal() as db:
        notices = list(
            db.scalars(select(InAppNotification).where(InAppNotification.job_id.in_(job_ids)))
        )
        assert [notice.job_id for notice in notices] == [job_ids[0]]
        notified = db.get(AIJob, job_ids[0])
        assert notified is not None
        assert notified.completion_notification_sent is True
        assert notified.notification_id == notices[0].id
        assert len(notices[0].preview) <= NOTIFICATION_SUMMARY_LIMIT
        assert len(delivered) == 1
        assert delivered[0].alert_type == "sense_job"
        assert delivered[0].message == notices[0].preview
        assert len(delivered[0].message) <= NOTIFICATION_SUMMARY_LIMIT


async def test_runtime_limit_preserves_partial_response(monkeypatch) -> None:
    async def slow_stream(db, question, config, history):
        yield ChatEvent("delta", "Useful partial result")
        await asyncio.sleep(10)

    monkeypatch.setattr("serversense.services.sense_jobs.chat_stream", slow_stream)
    with SessionLocal() as db:
        user = User(
            username=f"timeout-owner-{datetime.now(UTC).timestamp()}",
            password_hash="not-used",
            is_admin=True,
        )
        conversation = AIConversation(title="Timeout")
        db.add_all([user, conversation])
        db.flush()
        message = AIMessage(
            conversation_id=conversation.id,
            timestamp=datetime.now(UTC),
            role="user",
            content="Analyze slowly",
            source="user",
            references={},
        )
        db.add(message)
        db.flush()
        job = create_job(
            db,
            user.id,
            conversation,
            message,
            "analysis",
            {
                "provider": "openai_compatible",
                "model": "slow-model",
                "endpoint": "http://model.invalid",
                "tool_calling": "curated_context",
                "max_runtime_seconds": 0.02,
            },
            [],
        )
        job_id = job.id

    await _run_job(job_id)

    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job is not None
        assert job.status == "timed_out"
        assert job.timed_out_at is not None
        response = db.get(AIMessage, job.response_message_id)
        assert response is not None
        assert "Useful partial result" in response.content
        assert response.references["incomplete"] is True


async def test_provider_failure_preserves_partial_and_releases_job(monkeypatch) -> None:
    async def failing_stream(db, question, config, history):
        yield ChatEvent("delta", "Evidence gathered before disconnect")
        raise ConnectionError("provider disappeared")

    monkeypatch.setattr("serversense.services.sense_jobs.chat_stream", failing_stream)
    with SessionLocal() as db:
        user = User(
            username=f"failure-owner-{datetime.now(UTC).timestamp()}",
            password_hash="not-used",
            is_admin=True,
        )
        conversation = AIConversation(title="Provider failure")
        db.add_all([user, conversation])
        db.flush()
        message = AIMessage(
            conversation_id=conversation.id,
            timestamp=datetime.now(UTC),
            role="user",
            content="Analyze provider failure",
            source="user",
            references={},
        )
        db.add(message)
        db.flush()
        job = create_job(
            db,
            user.id,
            conversation,
            message,
            "analysis",
            {
                "provider": "openai_compatible",
                "model": "unstable-model",
                "endpoint": "http://model.invalid",
                "tool_calling": "curated_context",
            },
            [],
        )
        job_id = job.id

    await _run_job(job_id)

    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job is not None
        assert job.status == "failed"
        assert "monitoring and direct telemetry remain available" in (job.error or "")
        response = db.get(AIMessage, job.response_message_id)
        assert response is not None
        assert "Evidence gathered before disconnect" in response.content


async def test_running_job_cancellation_preserves_partial_response(monkeypatch) -> None:
    streaming = asyncio.Event()

    async def slow_stream(db, question, config, history):
        yield ChatEvent("delta", "Partial before cancellation")
        streaming.set()
        await asyncio.sleep(10)

    monkeypatch.setattr("serversense.services.sense_jobs.chat_stream", slow_stream)
    with SessionLocal() as db:
        user = User(
            username=f"cancel-owner-{datetime.now(UTC).timestamp()}",
            password_hash="not-used",
            is_admin=True,
        )
        conversation = AIConversation(title="Cancel")
        db.add_all([user, conversation])
        db.flush()
        message = AIMessage(
            conversation_id=conversation.id,
            timestamp=datetime.now(UTC),
            role="user",
            content="Analyze until stopped",
            source="user",
            references={},
        )
        db.add(message)
        db.flush()
        job = create_job(
            db,
            user.id,
            conversation,
            message,
            "analysis",
            {
                "provider": "openai_compatible",
                "model": "slow-model",
                "endpoint": "http://model.invalid",
                "tool_calling": "curated_context",
            },
            [],
        )
        job_id = job.id

    task = asyncio.create_task(_run_job(job_id))
    await asyncio.wait_for(streaming.wait(), timeout=1)
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job is not None
        assert cancel_job(db, job) is True
    await task

    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job is not None
        assert job.status == "cancelled"
        assert job.cancelled_at is not None
        response = db.get(AIMessage, job.response_message_id)
        assert response is not None
        assert "Partial before cancellation" in response.content


def test_restart_reconciliation_marks_active_job_interrupted() -> None:
    with SessionLocal() as db:
        user = User(
            username=f"restart-owner-{datetime.now(UTC).timestamp()}",
            password_hash="not-used",
            is_admin=True,
        )
        conversation = AIConversation(title="Restart")
        db.add_all([user, conversation])
        db.flush()
        message = AIMessage(
            conversation_id=conversation.id,
            timestamp=datetime.now(UTC),
            role="user",
            content="Analyze across restart",
            source="user",
            references={},
        )
        db.add(message)
        db.flush()
        job = create_job(
            db,
            user.id,
            conversation,
            message,
            "analysis",
            {"provider": "openai_compatible", "model": "test-model"},
            [],
        )
        job.status = "streaming"
        job.partial_response = "Persisted before restart"
        db.commit()
        job_id = job.id

    assert reconcile_interrupted_jobs() >= 1

    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job is not None
        assert job.status == "interrupted"
        assert job.interrupted_at is not None
        response = db.get(AIMessage, job.response_message_id)
        assert response is not None
        assert "Persisted before restart" in response.content
