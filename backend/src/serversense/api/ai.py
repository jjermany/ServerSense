import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from serversense.db import get_db
from serversense.models import AIConversation, AIMessage, AIToolCall, User
from serversense.schemas import ChatRequest, ChatResponse
from serversense.security import current_user
from serversense.services.ai import chat, chat_stream
from serversense.services.ai_config import read_ai_config

router = APIRouter(prefix="/api/ai", tags=["SENSE"])

HISTORY_MESSAGE_LIMIT = 6
HISTORY_CHARACTER_LIMIT = 10_000
HISTORY_IDLE_TIMEOUT = timedelta(minutes=30)

_active_requests: dict[str, tuple[int, asyncio.Task[Any]]] = {}


def _activity_for(question: str) -> str:
    lowered = question.lower()
    if any(word in lowered for word in ("storage", "capacity", "space", "run out")):
        return "Checking storage history and deterministic forecast…"
    if any(word in lowered for word in ("disk", "smart", "temperature", "drive")):
        return "Checking disk and SMART health…"
    if any(word in lowered for word in ("docker", "container")):
        return "Checking container health…"
    return "Checking current server telemetry…"


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _history(db: Session, conversation: AIConversation | None) -> list[dict[str, str]]:
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
        remaining = HISTORY_CHARACTER_LIMIT - characters
        if remaining <= 0:
            break
        if len(row.content) > remaining:
            continue
        selected.append(row)
        characters += len(row.content)
    return [
        {"role": row.role, "content": row.content}
        for row in reversed(selected)
        if row.role in {"user", "assistant"}
    ]


def _conversation(db: Session, conversation_id: int | None) -> AIConversation | None:
    conversation = db.get(AIConversation, conversation_id) if conversation_id else None
    if conversation_id and not conversation:
        raise HTTPException(404, "Conversation not found")
    return conversation


def _persist_exchange(
    db: Session,
    conversation: AIConversation | None,
    question: str,
    answer: str,
    tools: Sequence[str],
) -> AIConversation:
    if not conversation:
        conversation = AIConversation(title=question[:80])
        db.add(conversation)
        db.flush()
    now = datetime.now(UTC)
    db.add(
        AIMessage(
            conversation_id=conversation.id,
            timestamp=now,
            role="user",
            content=question,
        )
    )
    assistant = AIMessage(
        conversation_id=conversation.id,
        timestamp=now,
        role="assistant",
        content=answer,
    )
    db.add(assistant)
    db.flush()
    for name in tools:
        db.add(
            AIToolCall(
                message_id=assistant.id,
                timestamp=now,
                tool_name=name,
                arguments={},
                result={"recorded": True},
            )
        )
    conversation.updated_at = now
    db.commit()
    return conversation


@router.post("/chat", response_model=ChatResponse)
async def send_chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ChatResponse:
    conversation = _conversation(db, payload.conversation_id)
    history = _history(db, conversation)
    try:
        answer, tools, model = await chat(
            db,
            payload.message,
            read_ai_config(db, include_secret=True),
            history,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "SENSE model response timed out") from exc
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502, f"SENSE provider returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"SENSE provider connection failed: {type(exc).__name__}") from exc
    conversation = _persist_exchange(db, conversation, payload.message, answer, tools)
    return ChatResponse(
        conversation_id=conversation.id, message=answer, tools_used=tools, model=model
    )


@router.post("/chat/stream")
async def stream_chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> StreamingResponse:
    conversation = _conversation(db, payload.conversation_id)
    history = _history(db, conversation)
    config = read_ai_config(db, include_secret=True)
    request_id = uuid.uuid4().hex

    async def generate() -> AsyncIterator[str]:
        task = asyncio.current_task()
        if task is None:
            yield _sse("error", {"message": "SENSE could not start the request"})
            return
        _active_requests[request_id] = (user.id, task)
        yield _sse(
            "activity",
            {"message": _activity_for(payload.message), "request_id": request_id},
        )
        try:
            async for event in chat_stream(db, payload.message, config, history):
                if event.kind == "activity":
                    yield _sse("activity", {"message": event.message})
                elif event.kind == "delta":
                    yield _sse("delta", {"message": event.message})
                elif event.kind == "complete":
                    saved = _persist_exchange(
                        db,
                        conversation,
                        payload.message,
                        event.message,
                        event.tools,
                    )
                    yield _sse(
                        "message",
                        {
                            "conversation_id": saved.id,
                            "message": event.message,
                            "tools_used": list(event.tools),
                            "model": event.model,
                        },
                    )
        except asyncio.CancelledError:
            db.rollback()
            raise
        except httpx.TimeoutException:
            db.rollback()
            timeout_seconds = int(config.get("timeout_seconds", 60))
            yield _sse(
                "error",
                {"message": f"The model did not respond within {timeout_seconds} seconds."},
            )
        except httpx.HTTPStatusError as exc:
            db.rollback()
            yield _sse(
                "error",
                {"message": f"The model endpoint returned HTTP {exc.response.status_code}."},
            )
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            db.rollback()
            yield _sse("error", {"message": str(exc)})
        except httpx.HTTPError as exc:
            db.rollback()
            yield _sse(
                "error",
                {"message": f"The model connection failed: {type(exc).__name__}."},
            )
        except Exception as exc:
            db.rollback()
            yield _sse("error", {"message": f"SENSE failed: {type(exc).__name__}"})
        finally:
            _active_requests.pop(request_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/requests/{request_id}")
async def cancel_request(
    request_id: str,
    user: User = Depends(current_user),
) -> dict[str, bool]:
    active = _active_requests.get(request_id)
    if not active or active[0] != user.id:
        return {"cancelled": False}
    active[1].cancel()
    return {"cancelled": True}


@router.get("/conversations")
def conversations(
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> list[dict[str, Any]]:
    rows = db.scalars(select(AIConversation).order_by(desc(AIConversation.updated_at)).limit(100))
    return [{"id": x.id, "title": x.title, "updated_at": x.updated_at} for x in rows]


@router.get("/conversations/{conversation_id}")
def conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
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
        "messages": [
            {"id": x.id, "role": x.role, "content": x.content, "timestamp": x.timestamp}
            for x in rows
        ],
    }
