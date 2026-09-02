import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, desc, func, or_, select, update
from sqlalchemy.orm import Session

from serversense.db import SessionLocal, get_db
from serversense.models import AIConversation, AIJob, AIMessage, AIToolCall, InAppNotification, User
from serversense.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationRename,
    JobNotificationPreference,
)
from serversense.security import current_user
from serversense.services.ai_config import read_ai_config
from serversense.services.sense_jobs import (
    TERMINAL_STATUSES,
    cancel_job,
    create_job,
    public_job,
    queue_position,
)
from serversense.services.sense_router import Intent, RoutedRequest, route_request

router = APIRouter(prefix="/api/ai", tags=["SENSE"])
HISTORY_MESSAGE_LIMIT = 6
HISTORY_CHARACTER_LIMIT = 10_000
HISTORY_IDLE_TIMEOUT = timedelta(minutes=30)
_active_requests: dict[str, tuple[int, asyncio.Task[Any]]] = {}


def _activity_for(question: str) -> str:
    lowered = question.lower()
    if any(word in lowered for word in ("storage", "capacity", "space", "run out")):
        return "Checking combined-array storage telemetry…"
    if any(word in lowered for word in ("disk", "smart", "temperature", "drive")):
        return "Checking disk and SMART health…"
    if any(word in lowered for word in ("docker", "container")):
        return "Checking container health…"
    return "Checking current server telemetry…"


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _history(
    db: Session,
    conversation: AIConversation | None,
    character_limit: int = HISTORY_CHARACTER_LIMIT,
) -> list[dict[str, str]]:
    now = datetime.now(UTC)
    if not conversation or now - _aware(conversation.updated_at) > HISTORY_IDLE_TIMEOUT:
        return []
    rows = list(
        db.scalars(
            select(AIMessage)
            .where(
                AIMessage.conversation_id == conversation.id,
                AIMessage.timestamp >= now - HISTORY_IDLE_TIMEOUT,
            )
            .order_by(desc(AIMessage.timestamp))
            .limit(HISTORY_MESSAGE_LIMIT)
        )
    )
    selected: list[AIMessage] = []
    characters = 0
    for row in rows:
        remaining = min(character_limit, HISTORY_CHARACTER_LIMIT) - characters
        if remaining <= 0:
            break
        if len(row.content) <= remaining:
            selected.append(row)
            characters += len(row.content)
    return [
        {"role": row.role, "content": row.content}
        for row in reversed(selected)
        if row.role in {"user", "assistant"}
    ]


def _recent_references(db: Session, conversation: AIConversation | None) -> dict[str, Any]:
    if not conversation:
        return {}
    row = db.scalar(
        select(AIMessage)
        .where(AIMessage.conversation_id == conversation.id, AIMessage.role == "assistant")
        .order_by(desc(AIMessage.timestamp))
        .limit(1)
    )
    return dict(row.references or {}) if row else {}


def _conversation(db: Session, conversation_id: int | None) -> AIConversation | None:
    conversation = db.get(AIConversation, conversation_id) if conversation_id else None
    if conversation_id and not conversation:
        raise HTTPException(404, "Conversation not found")
    return conversation


def _new_conversation(db: Session, question: str) -> AIConversation:
    conversation = AIConversation(title=question[:80], summary="")
    db.add(conversation)
    db.flush()
    return conversation


def _persist_direct(
    db: Session, conversation: AIConversation | None, question: str, routed: RoutedRequest
) -> tuple[AIConversation, AIMessage]:
    conversation = conversation or _new_conversation(db, question)
    now = datetime.now(UTC)
    db.add(
        AIMessage(
            conversation_id=conversation.id,
            timestamp=now,
            role="user",
            content=question,
            source="user",
            references={},
        )
    )
    assistant = AIMessage(
        conversation_id=conversation.id,
        timestamp=now,
        role="assistant",
        content=routed.answer,
        source="serversense",
        references=routed.references or {},
    )
    db.add(assistant)
    db.flush()
    for name in routed.tools:
        db.add(
            AIToolCall(
                message_id=assistant.id,
                timestamp=now,
                tool_name=name,
                arguments={},
                result={"recorded": True},
            )
        )
    entry = f"User asked: {question[:240]} ServerSense answered: {routed.answer[:500]}"
    conversation.summary = (conversation.summary.strip() + "\n" + entry).strip()[-4000:]
    conversation.summary_updated_at = now
    conversation.updated_at = now
    db.commit()
    return conversation, assistant


def _enqueue(
    db: Session,
    user: User,
    conversation: AIConversation | None,
    payload: ChatRequest,
    routed: RoutedRequest,
) -> AIJob:
    config = read_ai_config(db, include_secret=True)
    if config.get("provider") == "disabled" or not config.get("model"):
        raise HTTPException(
            409,
            "This question needs SENSE AI for reasoning, but no model is configured. Current factual telemetry questions still work without AI.",
        )
    queued = int(db.scalar(select(func.count(AIJob.id)).where(AIJob.status == "queued")) or 0)
    if queued >= int(config.get("max_queued_jobs", 10)):
        raise HTTPException(429, "The SENSE queue is full; try again after an active job finishes")
    history = _history(db, conversation, int(config.get("max_context_chars", 30_000)) // 3)
    references = _recent_references(db, conversation)
    conversation = conversation or _new_conversation(db, payload.message)
    user_message = AIMessage(
        conversation_id=conversation.id,
        timestamp=datetime.now(UTC),
        role="user",
        content=payload.message,
        source="user",
        references={},
    )
    db.add(user_message)
    db.flush()
    return create_job(
        db, user.id, conversation, user_message, routed.intent, config, history, references
    )


@router.post("/chat", response_model=ChatResponse)
async def send_chat(
    payload: ChatRequest, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> ChatResponse:
    conversation = _conversation(db, payload.conversation_id)
    routed = route_request(db, payload.message)
    if routed.direct:
        saved, _ = _persist_direct(db, conversation, payload.message, routed)
        return ChatResponse(
            conversation_id=saved.id,
            message=routed.answer,
            tools_used=list(routed.tools),
            model="ServerSense deterministic",
            source="serversense",
        )
    job = _enqueue(db, user, conversation, payload, routed)
    deadline = (
        asyncio.get_running_loop().time()
        + float(job.config_snapshot.get("max_runtime_seconds", 300))
        + 30
    )
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)
        db.expire_all()
        current = db.get(AIJob, job.id)
        if current and current.status in TERMINAL_STATUSES:
            if current.status == "completed" and current.response_message_id:
                message = db.get(AIMessage, current.response_message_id)
                if message:
                    return ChatResponse(
                        conversation_id=current.conversation_id,
                        message=message.content,
                        tools_used=list((current.tools_used or {}).get("names", [])),
                        model=current.model,
                        source="sense_ai",
                        job_id=current.id,
                    )
            if current.response_message_id:
                message = db.get(AIMessage, current.response_message_id)
                if message:
                    return ChatResponse(
                        conversation_id=current.conversation_id,
                        message=message.content,
                        tools_used=list((current.tools_used or {}).get("names", [])),
                        model=current.model,
                        source="sense_ai",
                        job_id=current.id,
                    )
            raise HTTPException(502, current.error or f"SENSE job was {current.status}")
    raise HTTPException(504, "SENSE model response timed out")


@router.post("/direct", response_model=ChatResponse)
def send_direct_telemetry(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ChatResponse:
    conversation = _conversation(db, payload.conversation_id)
    routed = route_request(db, payload.message)
    if not routed.direct:
        raise HTTPException(
            422,
            "That question requires SENSE AI analysis. Direct telemetry remains available for current factual status questions.",
        )
    saved, _message = _persist_direct(db, conversation, payload.message, routed)
    return ChatResponse(
        conversation_id=saved.id,
        message=routed.answer,
        tools_used=list(routed.tools),
        model="ServerSense deterministic",
        source="serversense",
    )


@router.post("/chat/stream")
async def stream_chat(
    payload: ChatRequest, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> StreamingResponse:
    conversation = _conversation(db, payload.conversation_id)
    routed = route_request(db, payload.message)
    if routed.direct:
        saved, assistant = _persist_direct(db, conversation, payload.message, routed)

        async def direct_stream() -> AsyncIterator[str]:
            yield _sse("activity", {"message": _activity_for(payload.message)})
            yield _sse("delta", {"message": routed.answer})
            yield _sse(
                "message",
                {
                    "conversation_id": saved.id,
                    "message_id": assistant.id,
                    "message": routed.answer,
                    "tools_used": list(routed.tools),
                    "model": None,
                    "source": "serversense",
                },
            )

        return StreamingResponse(direct_stream(), media_type="text/event-stream")
    job = _enqueue(db, user, conversation, payload, routed)

    async def job_stream() -> AsyncIterator[str]:
        yield _sse(
            "activity",
            {
                "message": "Queued for SENSE AI…",
                "request_id": job.id,
                "job_id": job.id,
                "conversation_id": job.conversation_id,
            },
        )
        previous = ""
        prior_status = ""
        background_reported = False
        while True:
            with SessionLocal() as stream_db:
                current = stream_db.get(AIJob, job.id)
                if not current:
                    yield _sse("error", {"message": "SENSE job no longer exists"})
                    return
                if current.status != prior_status:
                    prior_status = current.status
                    yield _sse(
                        "status",
                        {
                            "job_id": current.id,
                            "status": current.status,
                            "queue_position": queue_position(stream_db, current),
                            "model": current.model,
                            "notify_on_completion": current.notify_on_completion,
                            "started_at": current.started_at,
                            "first_token_at": current.first_token_at,
                        },
                    )
                partial = current.partial_response or ""
                if partial.startswith(previous) and len(partial) > len(previous):
                    yield _sse("delta", {"message": partial[len(previous) :]})
                elif partial != previous:
                    yield _sse("reset", {})
                    if partial:
                        yield _sse("delta", {"message": partial})
                previous = partial
                if current.backgrounded_at and not background_reported:
                    background_reported = True
                    yield _sse(
                        "backgrounded",
                        {
                            "message": "SENSE AI is still working. You can stay here, or leave this page and choose whether ServerSense notifies you when the analysis is complete.",
                            "job_id": current.id,
                            "notify_on_completion": current.notify_on_completion,
                        },
                    )
                if current.status == "completed":
                    response = stream_db.get(AIMessage, current.response_message_id)
                    yield _sse(
                        "message",
                        {
                            "conversation_id": current.conversation_id,
                            "message_id": current.response_message_id,
                            "message": response.content if response else current.partial_response,
                            "tools_used": list((current.tools_used or {}).get("names", [])),
                            "model": current.model,
                            "source": "sense_ai",
                            "job_id": current.id,
                        },
                    )
                    return
                if current.status in {"failed", "cancelled", "timed_out", "interrupted"}:
                    response = stream_db.get(AIMessage, current.response_message_id)
                    yield _sse(
                        "terminal",
                        {
                            "conversation_id": current.conversation_id,
                            "message_id": current.response_message_id,
                            "message": response.content if response else "",
                            "error": current.error or f"SENSE job was {current.status}",
                            "model": current.model,
                            "source": "sense_ai",
                            "job_id": current.id,
                            "status": current.status,
                        },
                    )
                    return
            await asyncio.sleep(0.25)

    return StreamingResponse(
        job_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/requests/{request_id}")
async def cancel_request(
    request_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, bool]:
    active = _active_requests.get(request_id)
    if active:
        if active[0] != user.id:
            return {"cancelled": False}
        active[1].cancel()
        return {"cancelled": True}
    job = db.get(AIJob, request_id)
    if not job or job.user_id != user.id:
        return {"cancelled": False}
    return {"cancelled": cancel_job(db, job)}


@router.get("/jobs")
def jobs(
    conversation_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[dict[str, Any]]:
    query = select(AIJob).where(AIJob.user_id == user.id)
    if conversation_id is not None:
        query = query.where(AIJob.conversation_id == conversation_id)
    rows = list(db.scalars(query.order_by(desc(AIJob.created_at)).limit(100)))
    return [public_job(row, queue_position(db, row)) for row in rows]


@router.get("/jobs/{job_id}")
def job_status(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, Any]:
    job = db.get(AIJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "SENSE job not found")
    return public_job(job, queue_position(db, job))


@router.patch("/jobs/{job_id}/notification")
def update_job_notification(
    job_id: str,
    payload: JobNotificationPreference,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    job = db.get(AIJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "SENSE job not found")
    job.notify_on_completion = payload.notify_on_completion
    db.commit()
    return public_job(job, queue_position(db, job))


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, Any]:
    original = db.get(AIJob, job_id)
    if not original or original.user_id != user.id:
        raise HTTPException(404, "SENSE job not found")
    if original.status not in {"failed", "cancelled", "timed_out", "interrupted"}:
        raise HTTPException(409, "Only incomplete terminal jobs can be retried")
    message = db.get(AIMessage, original.user_message_id)
    conversation = db.get(AIConversation, original.conversation_id)
    if not message or not conversation:
        raise HTTPException(409, "The original conversation is unavailable")
    config = read_ai_config(db, include_secret=True)
    retried = create_job(
        db,
        user.id,
        conversation,
        message,
        cast(Intent, original.intent),
        config,
        _history(db, conversation, int(config.get("max_context_chars", 30_000)) // 3),
        _recent_references(db, conversation),
    )
    return public_job(retried, queue_position(db, retried))


@router.get("/conversations")
def conversations(
    q: str = "", db: Session = Depends(get_db), _: User = Depends(current_user)
) -> list[dict[str, Any]]:
    query = select(AIConversation)
    if q.strip():
        pattern = f"%{q.strip()}%"
        matching = select(AIMessage.conversation_id).where(AIMessage.content.ilike(pattern))
        query = query.where(
            or_(AIConversation.title.ilike(pattern), AIConversation.id.in_(matching))
        )
    rows = db.scalars(query.order_by(desc(AIConversation.updated_at)).limit(100))
    return [
        {"id": row.id, "title": row.title, "updated_at": row.updated_at, "summary": row.summary}
        for row in rows
    ]


@router.get("/conversations/{conversation_id}")
def conversation_messages(
    conversation_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)
) -> dict[str, Any]:
    conversation = db.get(AIConversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    rows = db.scalars(
        select(AIMessage)
        .where(AIMessage.conversation_id == conversation_id)
        .order_by(AIMessage.timestamp)
    )
    return {
        "id": conversation.id,
        "title": conversation.title,
        "summary": conversation.summary,
        "messages": [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "timestamp": row.timestamp,
                "source": row.source,
                "provider": row.provider,
                "model": row.model,
                "references": row.references,
            }
            for row in rows
        ],
    }


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: int,
    payload: ConversationRename,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> dict[str, Any]:
    conversation = db.get(AIConversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    conversation.title = payload.title.strip()
    conversation.updated_at = datetime.now(UTC)
    db.commit()
    return {
        "id": conversation.id,
        "title": conversation.title,
        "updated_at": conversation.updated_at,
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)
) -> Response:
    conversation = db.get(AIConversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    active_jobs = list(
        db.scalars(
            select(AIJob).where(
                AIJob.conversation_id == conversation_id,
                AIJob.status.not_in(TERMINAL_STATUSES),
            )
        )
    )
    for active_job in active_jobs:
        cancel_job(db, active_job)
    job_ids = select(AIJob.id).where(AIJob.conversation_id == conversation_id)
    db.execute(delete(InAppNotification).where(InAppNotification.job_id.in_(job_ids)))
    db.execute(delete(AIJob).where(AIJob.conversation_id == conversation_id))
    message_ids = select(AIMessage.id).where(AIMessage.conversation_id == conversation_id)
    db.execute(delete(AIToolCall).where(AIToolCall.message_id.in_(message_ids)))
    db.execute(delete(AIMessage).where(AIMessage.conversation_id == conversation_id))
    db.delete(conversation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/notifications")
def notifications(
    unread_only: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, Any]:
    query = select(InAppNotification).where(InAppNotification.user_id == user.id)
    if unread_only:
        query = query.where(InAppNotification.read_at.is_(None))
    rows = list(db.scalars(query.order_by(desc(InAppNotification.created_at)).limit(100)))
    unread = int(
        db.scalar(
            select(func.count(InAppNotification.id)).where(
                InAppNotification.user_id == user.id, InAppNotification.read_at.is_(None)
            )
        )
        or 0
    )
    return {
        "unread": unread,
        "items": [
            {
                "id": row.id,
                "kind": row.kind,
                "title": row.title,
                "preview": row.preview,
                "conversation_id": row.conversation_id,
                "message_id": row.message_id,
                "job_id": row.job_id,
                "created_at": row.created_at,
                "read_at": row.read_at,
            }
            for row in rows
        ],
    }


@router.post("/notifications/{notification_id}/read")
def read_notification(
    notification_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, bool]:
    row = db.get(InAppNotification, notification_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    row.read_at = datetime.now(UTC)
    db.commit()
    return {"read": True}


@router.post("/notifications/read-all")
def read_all_notifications(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, bool]:
    rows = db.scalars(
        select(InAppNotification).where(
            InAppNotification.user_id == user.id, InAppNotification.read_at.is_(None)
        )
    )
    now = datetime.now(UTC)
    for row in rows:
        row.read_at = now
    db.commit()
    return {"read": True}


@router.delete("/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(
    notification_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> Response:
    row = db.get(InAppNotification, notification_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    job = db.get(AIJob, row.job_id) if row.job_id else None
    if job and job.notification_id == row.id:
        job.notification_id = None
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/notifications", status_code=status.HTTP_204_NO_CONTENT)
def clear_notifications(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> Response:
    notice_ids = select(InAppNotification.id).where(InAppNotification.user_id == user.id)
    db.execute(
        update(AIJob)
        .where(AIJob.user_id == user.id, AIJob.notification_id.in_(notice_ids))
        .values(notification_id=None)
    )
    db.execute(delete(InAppNotification).where(InAppNotification.user_id == user.id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
