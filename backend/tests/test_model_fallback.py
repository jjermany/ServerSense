import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from serversense.db import SessionLocal
from serversense.models import AIConversation, AIJob, AIMessage, Setting, User
from serversense.services import ai, codex, sense_jobs
from serversense.services.ai import ChatEvent, _ProviderTurn
from serversense.services.ai_config import AI_DEFAULTS, read_ai_config
from serversense.services.model_fallback import complete_with_fallback, eligible_failure
from serversense.services.secrets import encrypt_secret

CONFIG = AI_DEFAULTS | {
    "provider": "codex",
    "model": "primary",
    "context_window": 32768,
    "fallback_provider": "ollama",
    "fallback_model": "backup",
    "fallback_endpoint": "http://backup.test",
    "fallback_context_window": 8192,
    "notify_long_running_jobs": False,
}


@pytest.fixture(autouse=True)
def restore_settings() -> Any:
    with SessionLocal() as db:
        row = db.get(Setting, "ai")
        old = dict(row.value) if row else None
    yield
    with SessionLocal() as db:
        row = db.get(Setting, "ai")
        if old is None:
            if row:
                db.delete(row)
        elif row:
            row.value = old
        else:
            db.add(Setting(key="ai", value=old, secret=True))
        db.commit()


def test_fallback_credentials_are_private_preserved_and_independently_clearable(
    authenticated_client: TestClient,
) -> None:
    payload = CONFIG | {"fallback_api_key": "backup-secret", "api_key": "primary-secret"}
    response = authenticated_client.put("/api/settings/ai", json=payload)
    assert response.status_code == 200
    assert response.json()["fallback_api_key_configured"] is True
    assert "secret" not in response.text
    assert authenticated_client.put("/api/settings/ai", json=CONFIG).status_code == 200
    with SessionLocal() as db:
        saved = read_ai_config(db, include_secret=True)
        assert saved["fallback_api_key"] == "backup-secret"
        row = db.get(Setting, "ai")
        assert row and "backup-secret" not in str(row.value)
        snapshot = sense_jobs.config_snapshot(saved)
        assert snapshot["fallback_endpoint"] == "http://backup.test"
        assert not any("key" in name for name in snapshot)
    cleared = authenticated_client.delete("/api/settings/ai/fallback/api-key")
    assert cleared.json()["fallback_api_key_configured"] is False
    assert cleared.json()["api_key_configured"] is True
    invalid = authenticated_client.put(
        "/api/settings/ai", json=CONFIG | {"fallback_endpoint": "file:///config"}
    )
    assert invalid.status_code == 422


def create_job() -> str:
    with SessionLocal() as db:
        user = User(
            username=f"fallback-{datetime.now(UTC).timestamp()}",
            password_hash="unused",
            is_admin=True,
        )
        conversation = AIConversation(title="Fallback test")
        db.add_all([user, conversation])
        db.flush()
        message = AIMessage(
            conversation_id=conversation.id,
            timestamp=datetime.now(UTC),
            role="user",
            content="Analyze the server.",
            source="user",
            references={},
        )
        db.add(message)
        db.flush()
        job = sense_jobs.create_job(db, user.id, conversation, message, "analysis", CONFIG, [])
        return job.id


@pytest.mark.parametrize("backup_fails", [False, True])
async def test_durable_failover_preserves_partial_and_snapshotted_provenance(
    monkeypatch: pytest.MonkeyPatch,
    backup_fails: bool,
) -> None:
    job_id = create_job()
    with SessionLocal() as db:
        row = db.get(Setting, "ai")
        changed = CONFIG | {
            "fallback_model": "changed-after-queue",
            "fallback_api_key_encrypted": encrypt_secret("backup-secret"),
        }
        if row:
            row.value = changed
        else:
            db.add(Setting(key="ai", value=changed, secret=True))
        db.commit()
    calls: list[dict[str, Any]] = []

    async def stream(
        db: Any, question: str, config: dict[str, Any], history: Any
    ) -> AsyncIterator[ChatEvent]:
        calls.append(dict(config))
        if config["provider"] == "codex":
            yield ChatEvent("delta", "Primary partial")
            raise codex.limit_error([], "UTC")
        assert config["model"] == "backup"
        assert config["api_key"] == "backup-secret"
        yield ChatEvent("delta", "Backup partial")
        if backup_fails:
            raise httpx.ConnectError("private-provider-data")
        yield ChatEvent("complete", "Backup answer", model="backup")

    monkeypatch.setattr(sense_jobs, "chat_stream", stream)
    await sense_jobs._run_job(job_id)
    assert len(calls) == 2
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job and job.status == ("failed" if backup_fails else "completed")
        assert job.provider == "codex" and job.model == "primary"
        public = sense_jobs.public_job(job)
        assert public["active_provider"] == "ollama"
        assert public["active_model"] == "backup"
        assert "allowance" in public["fallback_reason"]
        assistant = db.get(AIMessage, job.response_message_id)
        assert assistant and assistant.provider == "ollama" and assistant.model == "backup"
        partials = list(
            db.scalars(
                select(AIMessage).where(
                    AIMessage.conversation_id == job.conversation_id, AIMessage.provider == "codex"
                )
            )
        )
        assert len(partials) == 1 and partials[0].references["incomplete"]
        assert "Primary partial" in partials[0].content
        assert "private-provider-data" not in str(public)


@pytest.mark.parametrize("error", [ValueError("tool policy"), codex.CodexError("stream limit")])
async def test_policy_failures_do_not_switch_models(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    job_id = create_job()
    calls: list[str] = []

    async def stream(
        db: Any, question: str, config: dict[str, Any], history: Any
    ) -> AsyncIterator[ChatEvent]:
        calls.append(config["provider"])
        raise error
        yield ChatEvent("complete")

    monkeypatch.setattr(sense_jobs, "chat_stream", stream)
    await sense_jobs._run_job(job_id)
    assert calls == ["codex"]
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job and job.status == "failed"


def test_background_fallback_is_once_and_uses_remaining_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([0.0, 12.0])
    monkeypatch.setattr("serversense.services.model_fallback.monotonic", lambda: next(times))
    calls: list[dict[str, Any]] = []

    def request(config: dict[str, Any]) -> str:
        calls.append(config)
        if config["provider"] == "codex":
            raise codex.limit_error([], "UTC")
        assert config["max_runtime_seconds"] == 288
        assert config["fallback_provider"] == "disabled"
        return "Backup summary"

    answer, actual = complete_with_fallback(CONFIG, request)
    assert answer == "Backup summary" and actual["provider"] == "ollama"
    assert len(calls) == 2
    assert not eligible_failure(ValueError("output limit"))
    assert not eligible_failure(codex.CodexError("unsupported tool"))


@pytest.mark.parametrize("cancel", [False, True])
async def test_overall_runtime_and_cancellation_do_not_fail_over(
    monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    job_id = create_job()
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job
        job.config_snapshot = dict(job.config_snapshot) | {"max_runtime_seconds": 0.02}
        db.commit()
    calls: list[str] = []

    async def stream(
        db: Any, question: str, config: dict[str, Any], history: Any
    ) -> AsyncIterator[ChatEvent]:
        calls.append(config["provider"])
        yield ChatEvent("delta", "Retained partial")
        if cancel:
            current = db.get(AIJob, job_id)
            current.cancel_requested = True
            db.commit()
            raise asyncio.CancelledError
        await asyncio.sleep(1)

    monkeypatch.setattr(sense_jobs, "chat_stream", stream)
    await sense_jobs._run_job(job_id)
    assert calls == ["codex"]
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job and job.status == ("cancelled" if cancel else "timed_out")
        assert job.response_message_id


def test_codex_policy_failures_are_ineligible() -> None:
    for category in [
        "contextWindowExceeded",
        "sessionBudgetExceeded",
        "cyberPolicy",
        "misalignmentPolicyViolation",
        "sandboxError",
        "tooManyDenials",
    ]:
        assert not eligible_failure(codex.provider_error({"codexErrorInfo": category}, [], "UTC"))
    assert eligible_failure(codex.provider_error({"codexErrorInfo": "serverOverloaded"}, [], "UTC"))


async def test_switching_models_does_not_reset_the_tool_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = create_job()
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job
        job.provider = "openai_compatible"
        job.config_snapshot = dict(job.config_snapshot) | {
            "provider": "openai_compatible",
            "endpoint": "http://primary.test",
            "max_tool_calls": 1,
            "fallback_context_window": 32768,
        }
        db.commit()
    requests: list[str] = []
    executions: list[str] = []

    async def turn(client: Any, endpoint: str, headers: Any, payload: Any) -> AsyncIterator[Any]:
        requests.append(endpoint)
        if endpoint == "http://primary.test" and len(requests) == 2:
            response = httpx.Response(503, request=httpx.Request("POST", endpoint))
            response.raise_for_status()
        yield _ProviderTurn(
            "",
            ({"id": "overview", "function": {"name": "get_server_overview", "arguments": "{}"}},),
            "tool_calls",
        )

    def execute(db: Any, name: str, arguments: Any) -> dict[str, Any]:
        executions.append(name)
        return {"measurement": "normalized"}

    monkeypatch.setattr(ai, "_provider_turn", turn)
    monkeypatch.setattr(ai, "execute_tool", execute)
    await sense_jobs._run_job(job_id)
    assert requests == ["http://primary.test", "http://primary.test", "http://backup.test"]
    assert executions == ["get_server_overview"]
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        assert job and job.status == "failed" and "tool-call limit" in job.error
