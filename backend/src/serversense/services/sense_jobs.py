import asyncio
import json
import logging
import re
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from serversense.db import SessionLocal
from serversense.models import (
    AIConversation,
    AIJob,
    AIMessage,
    AIToolCall,
    Alert,
    InAppNotification,
)
from serversense.services.ai import chat_stream
from serversense.services.ai_config import read_ai_config
from serversense.services.notifications import dispatch_notifications
from serversense.services.sense_router import Intent
from serversense.services.tools import execute_tool

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "timed_out", "interrupted"}
RUNNING_STATUSES = {"gathering_context", "analyzing", "streaming"}
_active: dict[str, asyncio.Task[None]] = {}
_shutting_down = False
logger = logging.getLogger(__name__)
NOTIFICATION_SUMMARY_LIMIT = 200


def public_job(job: AIJob, queue_position: int | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)

    def elapsed(start: datetime | None, end: datetime | None) -> float | None:
        if start is None:
            return None
        aware_start = start.replace(tzinfo=UTC) if start.tzinfo is None else start
        value = end or now
        aware_end = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return max(0.0, (aware_end - aware_start).total_seconds())

    return {
        "id": job.id,
        "conversation_id": job.conversation_id,
        "user_message_id": job.user_message_id,
        "response_message_id": job.response_message_id,
        "status": job.status,
        "intent": job.intent,
        "provider": job.provider,
        "model": job.model,
        "partial_response": job.partial_response,
        "tools_used": list((job.tools_used or {}).get("names", [])),
        "error": job.error,
        "queue_position": queue_position,
        "backgrounded": job.backgrounded_at is not None,
        "notify_on_completion": job.notify_on_completion,
        "completion_notification_sent": job.completion_notification_sent,
        "created_at": job.created_at,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "first_token_at": job.first_token_at,
        "backgrounded_at": job.backgrounded_at,
        "completed_at": job.completed_at,
        "cancelled_at": job.cancelled_at,
        "timed_out_at": job.timed_out_at,
        "interrupted_at": job.interrupted_at,
        "queue_wait_seconds": elapsed(job.queued_at, job.started_at),
        "time_to_first_token_seconds": elapsed(job.started_at, job.first_token_at)
        if job.first_token_at
        else None,
        "inference_seconds": elapsed(job.started_at, job.completed_at),
        "total_job_seconds": elapsed(job.created_at, job.completed_at),
        "generated_tokens": job.generated_tokens,
        "generated_tokens_estimated": job.generated_tokens is not None,
    }


def config_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "provider",
        "model",
        "endpoint",
        "context_window",
        "temperature",
        "timeout_seconds",
        "max_tool_calls",
        "max_output_tokens",
        "tool_calling",
        "background_threshold_seconds",
        "max_runtime_seconds",
        "max_context_chars",
        "max_telemetry_chars",
    }
    return {key: value for key, value in config.items() if key in allowed}


def create_job(
    db: Session,
    user_id: int,
    conversation: AIConversation,
    user_message: AIMessage,
    intent: Intent,
    config: dict[str, Any],
    history: list[dict[str, str]],
    references: dict[str, Any] | None = None,
) -> AIJob:
    now = datetime.now(UTC)
    job = AIJob(
        id=uuid.uuid4().hex,
        user_id=user_id,
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        status="queued",
        intent=intent,
        provider=str(config.get("provider", "disabled")),
        model=str(config.get("model", "")),
        config_snapshot=config_snapshot(config),
        context_snapshot={
            "history": history,
            "summary": conversation.summary,
            "references": references or {},
        },
        tools_used={"names": []},
        notify_on_completion=bool(config.get("notify_long_running_jobs", True)),
        queued_at=now,
    )
    db.add(job)
    conversation.updated_at = datetime.now(UTC)
    db.commit()
    return job


def queue_position(db: Session, job: AIJob) -> int | None:
    if job.status != "queued":
        return None
    return int(
        db.scalar(
            select(func.count(AIJob.id)).where(
                AIJob.status == "queued", AIJob.created_at <= job.created_at
            )
        )
        or 0
    )


def _curated_context(
    db: Session,
    question: str,
    intent: str,
    snapshot: dict[str, Any],
    max_context_chars: int,
    max_telemetry_chars: int,
) -> dict[str, Any]:
    text = question.lower()
    names: list[str] = []
    if any(term in text for term in ("storage", "space", "capacity", "full", "growth", "array")):
        if intent == "historical":
            names.append("get_storage_history")
        else:
            names.append("get_storage_forecast" if intent != "status" else "get_array_status")
    if any(term in text for term in ("disk", "drive", "smart", "hot", "temperature", "parity")):
        names.append("get_disk_smart_health")
    if any(term in text for term in ("container", "docker", "plex", "service")):
        names.append("get_container_status")
    if any(term in text for term in ("cpu", "memory", "ram", "load", "network")):
        names.append("get_system_resources")
    if any(term in text for term in ("alert", "changed", "today", "recent")):
        names.append("get_recent_alerts")
    if "changed" in text or "summar" in text:
        names.append("get_server_overview")
    if any(term in text for term in ("upcoming", "calendar", "release", "air date")):
        names.append("get_upcoming_media")
    if not names:
        names.append("get_server_overview")
    context: dict[str, Any] = {
        "conversation_summary": str(snapshot.get("summary", ""))[: max_context_chars // 4],
        "prior_references": snapshot.get("references", {}),
        "telemetry": {},
    }
    remaining = max_telemetry_chars
    for name in dict.fromkeys(names):
        result = execute_tool(db, name, {})
        encoded = json.dumps(result, default=str)
        if len(encoded) > remaining:
            context["telemetry"][name] = {
                "truncated": True,
                "json_excerpt": encoded[: max(0, remaining)],
            }
            remaining = 0
            context["truncated"] = True
            break
        context["telemetry"][name] = result
        remaining -= len(encoded)
    encoded_context = json.dumps(context, default=str)
    if len(encoded_context) > max_context_chars:
        context["prior_references"] = {}
        context["conversation_summary"] = str(context["conversation_summary"])[
            : max_context_chars // 8
        ]
        context["truncated"] = True
    return context


def _update_summary(conversation: AIConversation, question: str, answer: str) -> None:
    entry = f"User asked: {question[:240]} SENSE answered: {answer[:500]}"
    current = conversation.summary.strip()
    conversation.summary = (current + "\n" + entry).strip()[-4000:]
    conversation.summary_updated_at = datetime.now(UTC)


async def _mark_backgrounded(job_id: str, seconds: int) -> None:
    await asyncio.sleep(seconds)
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        if job and job.status not in TERMINAL_STATUSES:
            job.backgrounded_at = datetime.now(UTC)
            db.commit()


def _safe_error(exc: Exception, config: dict[str, Any] | None = None) -> str:
    snapshot = config or {}
    provider_timeout = float(snapshot.get("timeout_seconds", 120))
    max_runtime = float(snapshot.get("max_runtime_seconds", 300))
    if isinstance(exc, httpx.ConnectTimeout):
        connect_timeout = min(10.0, provider_timeout)
        return (
            f"ServerSense could not connect to the model provider within "
            f"{connect_timeout:g} seconds. This connection limit is separate from the "
            f"configured {max_runtime:g}-second maximum runtime. You can retry this job."
        )
    if isinstance(exc, httpx.ReadTimeout):
        return (
            f"The model provider sent no response data for {provider_timeout:g} seconds. "
            f"This provider inactivity timeout is separate from the configured "
            f"{max_runtime:g}-second maximum runtime. You can retry this job."
        )
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"The model request reached the configured {provider_timeout:g}-second "
            f"provider I/O timeout. This is separate from the configured "
            f"{max_runtime:g}-second maximum runtime. You can retry this job."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return f"The model endpoint returned HTTP {exc.response.status_code}."
    if isinstance(exc, httpx.HTTPError):
        return f"The model connection failed: {type(exc).__name__}."
    if isinstance(exc, (ValueError, RuntimeError, json.JSONDecodeError)):
        return str(exc)[:500]
    return f"SENSE failed: {type(exc).__name__}."


def _notification_summary(value: str) -> str:
    """Create a useful plain-text notification body without another model call."""
    text = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", value)
    text = re.sub(r"```(?:[^\n]*)\n?|```", " ", text)
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|[-*+]|\d+[.)])\s+", "", text)
    text = re.sub(r"[*_`~]", "", text)
    text = " ".join(text.split())
    if len(text) <= NOTIFICATION_SUMMARY_LIMIT:
        return text

    available = NOTIFICATION_SUMMARY_LIMIT - 1
    excerpt = text[:available]
    sentence_ends = [excerpt.rfind(mark) for mark in (". ", "! ", "? ")]
    sentence_end = max(sentence_ends)
    if sentence_end >= 80:
        return excerpt[: sentence_end + 1]
    word_end = excerpt.rfind(" ")
    if word_end >= 80:
        excerpt = excerpt[:word_end]
    return excerpt.rstrip(" ,;:-") + "…"


def _add_notification(
    db: Session,
    job: AIJob,
    conversation: AIConversation | None,
    kind: str,
    title: str,
    preview: str,
    message_id: int | None,
    now: datetime,
) -> Alert | None:
    if not job.notify_on_completion or job.completion_notification_sent:
        return None
    summary = _notification_summary(preview)
    notification = InAppNotification(
        user_id=job.user_id,
        job_id=job.id,
        conversation_id=job.conversation_id,
        message_id=message_id,
        kind=kind,
        title=title,
        preview=summary,
        created_at=now,
    )
    db.add(notification)
    db.flush()
    job.completion_notification_sent = True
    job.notification_id = notification.id
    external_notice = Alert(
        alert_type="sense_job",
        severity="info" if kind == "ai_job_complete" else "warning",
        title=title,
        message=summary,
        fingerprint=f"sense-job:{job.id}",
        data={
            "job_id": job.id,
            "conversation_id": job.conversation_id,
            "status": kind.removeprefix("ai_job_"),
        },
    )
    external_notice.created_at = now
    return external_notice


def _deliver_external_notification(db: Session, notice: Alert | None) -> None:
    if notice is None:
        return
    try:
        failures = dispatch_notifications(db, [notice])
        if failures:
            logger.warning(
                "SENSE completion notification delivery failed for %d provider(s)",
                len(failures),
            )
    except Exception as exc:
        # Notification delivery is isolated from durable job completion.
        logger.warning("SENSE completion notification delivery failed: %s", type(exc).__name__)


def _persist_partial_terminal(
    db: Session,
    job: AIJob,
    status: str,
    reason: str,
    *,
    notify: bool,
) -> None:
    now = datetime.now(UTC)
    conversation = db.get(AIConversation, job.conversation_id)
    partial = (job.partial_response or "").strip()
    if partial:
        job.generated_tokens = max(1, (len(partial) + 3) // 4)
    message_id: int | None = job.response_message_id
    if partial and not message_id and conversation:
        content = f"## Partial SENSE AI response\n\n{partial}\n\n_{reason}_"
        message = AIMessage(
            conversation_id=job.conversation_id,
            timestamp=now,
            role="assistant",
            content=content,
            source="sense_ai",
            provider=job.provider,
            model=job.model,
            references={"incomplete": True, "job_status": status},
        )
        db.add(message)
        db.flush()
        message_id = message.id
        job.response_message_id = message.id
        if conversation:
            conversation.updated_at = now
    job.status = status
    job.error = reason
    job.completed_at = now
    if status == "cancelled":
        job.cancelled_at = now
    elif status == "timed_out":
        job.timed_out_at = now
    elif status == "interrupted":
        job.interrupted_at = now
    external_notice: Alert | None = None
    if notify:
        label = {
            "timed_out": "SENSE AI analysis timed out",
            "failed": "SENSE AI analysis failed",
            "interrupted": "SENSE AI analysis interrupted",
        }.get(status, "SENSE AI analysis stopped")
        title = conversation.title if conversation else "AI request"
        external_notice = _add_notification(
            db,
            job,
            conversation,
            f"ai_job_{status}",
            f"{label}: {title[:140]}",
            reason,
            message_id,
            now,
        )
    db.commit()
    _deliver_external_notification(db, external_notice)


async def _run_job(job_id: str) -> None:
    current_task = asyncio.current_task()
    if current_task is not None:
        _active.setdefault(job_id, current_task)
    marker: asyncio.Task[None] | None = None
    try:
        with SessionLocal() as db:
            job = db.get(AIJob, job_id)
            if not job or job.cancel_requested:
                if job:
                    _persist_partial_terminal(
                        db,
                        job,
                        "cancelled",
                        "Analysis cancelled before inference started.",
                        notify=False,
                    )
                return
            message = db.get(AIMessage, job.user_message_id)
            conversation = db.get(AIConversation, job.conversation_id)
            if not message or not conversation:
                raise RuntimeError("The conversation for this SENSE job no longer exists")
            job.status = "gathering_context"
            job.started_at = datetime.now(UTC)
            snapshot = dict(job.context_snapshot or {})
            config = read_ai_config(db, include_secret=True)
            # Model/provider/endpoint/options are immutable per job; the current credential is
            # read at execution time and is never persisted in the snapshot.
            config.update(job.config_snapshot or {})
            curated = _curated_context(
                db,
                message.content,
                job.intent,
                snapshot,
                int(config.get("max_context_chars", 30_000)),
                int(config.get("max_telemetry_chars", 20_000)),
            )
            config["curated_context"] = curated
            history = list(snapshot.get("history", []))
            threshold = int(config.get("background_threshold_seconds", 30))
            job.status = "analyzing"
            db.commit()
            marker = asyncio.create_task(_mark_backgrounded(job_id, threshold))

            answer = ""
            used: tuple[str, ...] = ()
            model = job.model
            partial_limit = int(config.get("max_output_tokens", 512)) * 8
            max_runtime = float(config.get("max_runtime_seconds", 300))
            attempted_curated_fallback = config.get("tool_calling", "auto") == "curated_context"
            async with asyncio.timeout(max_runtime):
                while True:
                    try:
                        async for event in chat_stream(db, message.content, config, history):
                            db.refresh(job)
                            if job.cancel_requested:
                                raise asyncio.CancelledError
                            if event.kind == "activity":
                                job.status = "analyzing"
                            elif event.kind == "reset":
                                answer = ""
                                job.partial_response = ""
                            elif event.kind == "delta":
                                job.status = "streaming"
                                if job.first_token_at is None:
                                    job.first_token_at = datetime.now(UTC)
                                answer += event.message
                                job.partial_response = answer[-partial_limit:]
                            elif event.kind == "complete":
                                answer = event.message
                                used = event.tools
                                model = event.model
                            db.commit()
                        break
                    except httpx.HTTPStatusError as exc:
                        # Several OpenAI-compatible local providers implement chat streaming but
                        # reject the native tools fields. In auto mode, retry once with the same
                        # curated telemetry and no native tool schema.
                        if (
                            not attempted_curated_fallback
                            and config.get("tool_calling", "auto") == "auto"
                            and exc.response.status_code in {400, 404, 405, 422, 501}
                        ):
                            attempted_curated_fallback = True
                            config["tool_calling"] = "curated_context"
                            answer = ""
                            job.partial_response = ""
                            job.first_token_at = None
                            job.status = "analyzing"
                            db.commit()
                            continue
                        raise
            if not answer.strip():
                raise RuntimeError("SENSE provider ended without a result")
            now = datetime.now(UTC)
            assistant = AIMessage(
                conversation_id=conversation.id,
                timestamp=now,
                role="assistant",
                content=answer,
                source="sense_ai",
                provider=job.provider,
                model=model,
                references={"tools": list(used)},
            )
            db.add(assistant)
            db.flush()
            for name in used:
                db.add(
                    AIToolCall(
                        message_id=assistant.id,
                        timestamp=now,
                        tool_name=name,
                        arguments={},
                        result={"recorded": True},
                    )
                )
            _update_summary(conversation, message.content, answer)
            conversation.updated_at = now
            job.response_message_id = assistant.id
            job.partial_response = answer
            job.generated_tokens = max(1, (len(answer) + 3) // 4)
            job.tools_used = {"names": list(used)}
            job.status = "completed"
            job.completed_at = now
            external_notice: Alert | None = None
            if job.backgrounded_at is not None:
                external_notice = _add_notification(
                    db,
                    job,
                    conversation,
                    "ai_job_complete",
                    f"SENSE AI analysis complete: {conversation.title[:140]}",
                    answer,
                    assistant.id,
                    now,
                )
            db.commit()
            _deliver_external_notification(db, external_notice)
    except TimeoutError:
        with SessionLocal() as db:
            job = db.get(AIJob, job_id)
            if job and job.status not in TERMINAL_STATUSES:
                runtime = float((job.config_snapshot or {}).get("max_runtime_seconds", 300))
                _persist_partial_terminal(
                    db,
                    job,
                    "timed_out",
                    f"SENSE AI reached the configured {runtime:g}-second analysis limit. The partial response has been preserved.",
                    notify=True,
                )
    except asyncio.CancelledError:
        with SessionLocal() as db:
            job = db.get(AIJob, job_id)
            if job and job.status not in TERMINAL_STATUSES:
                user_cancelled = job.cancel_requested
                status = (
                    "cancelled"
                    if user_cancelled
                    else "interrupted"
                    if _shutting_down
                    else "cancelled"
                )
                reason = (
                    "This SENSE AI analysis was interrupted because ServerSense stopped or restarted."
                    if status == "interrupted"
                    else "Analysis cancelled. The partial response has been preserved."
                )
                _persist_partial_terminal(db, job, status, reason, notify=status == "interrupted")
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(AIJob, job_id)
            if job and job.status not in TERMINAL_STATUSES:
                _persist_partial_terminal(
                    db,
                    job,
                    "failed",
                    _safe_error(exc, job.config_snapshot)
                    + " ServerSense monitoring and direct telemetry remain available.",
                    notify=True,
                )
    finally:
        if marker:
            marker.cancel()
            with suppress(asyncio.CancelledError):
                await marker
        _active.pop(job_id, None)


def cancel_job(db: Session, job: AIJob) -> bool:
    if job.status in TERMINAL_STATUSES:
        return False
    job.cancel_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        now = datetime.now(UTC)
        job.cancelled_at = now
        job.completed_at = now
        job.error = "Analysis cancelled before inference started."
    db.commit()
    task = _active.get(job.id)
    if task:
        task.cancel()
    return True


def reconcile_interrupted_jobs() -> int:
    reconciled = 0
    with SessionLocal() as db:
        # Provider streams cannot be resumed after a process restart. Reconcile stale active
        # rows explicitly and preserve whatever output was persisted before the interruption.
        jobs = list(db.scalars(select(AIJob).where(AIJob.status.in_(RUNNING_STATUSES))))
        for job in jobs:
            _persist_partial_terminal(
                db,
                job,
                "interrupted",
                "This SENSE AI analysis was interrupted because ServerSense restarted. The partial response has been preserved.",
                notify=True,
            )
            reconciled += 1
    return reconciled


async def sense_job_loop() -> None:
    global _shutting_down
    _shutting_down = False
    reconcile_interrupted_jobs()
    while True:
        with SessionLocal() as db:
            config = read_ai_config(db)
            limit = int(config.get("max_concurrent_jobs", 1))
            slots = max(0, limit - len(_active))
            if slots:
                jobs = list(
                    db.scalars(
                        select(AIJob)
                        .where(AIJob.status == "queued", AIJob.cancel_requested.is_(False))
                        .order_by(AIJob.created_at)
                        .limit(slots)
                    )
                )
                for job in jobs:
                    task = asyncio.create_task(_run_job(job.id))
                    _active[job.id] = task
        await asyncio.sleep(0.25)


async def stop_sense_jobs() -> None:
    global _shutting_down
    _shutting_down = True
    tasks = list(_active.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
