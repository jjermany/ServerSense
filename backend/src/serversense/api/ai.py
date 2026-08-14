import json
from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from serversense.api.settings import read_ai_config
from serversense.db import get_db
from serversense.models import AIConversation, AIMessage, AIToolCall
from serversense.schemas import ChatRequest, ChatResponse
from serversense.security import current_user
from serversense.services.ai import chat

router = APIRouter(prefix="/api/ai", tags=["SENSE"], dependencies=[Depends(current_user)])


def _activity_for(question: str) -> str:
    lowered = question.lower()
    if any(word in lowered for word in ("storage", "capacity", "space", "run out")):
        return "Checking storage history and deterministic forecast…"
    if any(word in lowered for word in ("disk", "smart", "temperature", "drive")):
        return "Checking disk and SMART health…"
    if any(word in lowered for word in ("docker", "container")):
        return "Checking container health…"
    return "Checking current server telemetry…"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/chat", response_model=ChatResponse)
def send_chat(payload: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    conversation = (
        db.get(AIConversation, payload.conversation_id) if payload.conversation_id else None
    )
    if payload.conversation_id and not conversation:
        raise HTTPException(404, "Conversation not found")
    if not conversation:
        conversation = AIConversation(title=payload.message[:80])
        db.add(conversation)
        db.flush()
    user_message = AIMessage(
        conversation_id=conversation.id,
        timestamp=datetime.now(UTC),
        role="user",
        content=payload.message,
    )
    db.add(user_message)
    try:
        answer, tools, model = chat(db, payload.message, read_ai_config(db, include_secret=True))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"SENSE provider request failed: {type(exc).__name__}") from exc
    assistant = AIMessage(
        conversation_id=conversation.id,
        timestamp=datetime.now(UTC),
        role="assistant",
        content=answer,
    )
    db.add(assistant)
    db.flush()
    for name in tools:
        db.add(
            AIToolCall(
                message_id=assistant.id,
                timestamp=datetime.now(UTC),
                tool_name=name,
                arguments={},
                result={"recorded": True},
            )
        )
    conversation.updated_at = datetime.now(UTC)
    db.commit()
    return ChatResponse(
        conversation_id=conversation.id, message=answer, tools_used=tools, model=model
    )


@router.post("/chat/stream")
def stream_chat(payload: ChatRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    def generate() -> Iterator[str]:
        yield _sse("activity", {"message": _activity_for(payload.message)})
        try:
            result = send_chat(payload, db)
            yield _sse("message", result.model_dump())
        except HTTPException as exc:
            yield _sse("error", {"message": str(exc.detail)})
        except Exception as exc:
            yield _sse("error", {"message": f"SENSE failed: {type(exc).__name__}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations")
def conversations(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(AIConversation).order_by(desc(AIConversation.updated_at)).limit(100))
    return [{"id": x.id, "title": x.title, "updated_at": x.updated_at} for x in rows]


@router.get("/conversations/{conversation_id}")
def conversation_messages(conversation_id: int, db: Session = Depends(get_db)) -> dict:
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
