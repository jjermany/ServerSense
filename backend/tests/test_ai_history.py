import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from serversense.api.ai import _active_requests, _history, cancel_request
from serversense.db import SessionLocal
from serversense.models import AIConversation, AIMessage, User


def test_ai_history_is_recent_and_bounded() -> None:
    with SessionLocal() as db:
        conversation = AIConversation(title="Bounded history")
        db.add(conversation)
        db.flush()
        for index in range(8):
            db.add(
                AIMessage(
                    conversation_id=conversation.id,
                    timestamp=datetime.now(UTC) + timedelta(seconds=index),
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"message-{index}",
                )
            )
        db.flush()

        history = _history(db, conversation)
        assert [item["content"] for item in history] == [
            "message-2",
            "message-3",
            "message-4",
            "message-5",
            "message-6",
            "message-7",
        ]

        conversation.updated_at = datetime.now(UTC) - timedelta(minutes=31)
        assert _history(db, conversation) == []
        db.rollback()


async def test_ai_cancellation_is_scoped_to_the_authenticated_user() -> None:
    async def pending() -> None:
        await asyncio.Event().wait()

    owner = User(
        id=98401,
        username="cancellation-owner",
        password_hash="not-used",
        is_admin=True,
    )
    other = User(
        id=98402,
        username="different-user",
        password_hash="not-used",
        is_admin=True,
    )
    task = asyncio.create_task(pending())
    _active_requests["owned-request"] = (owner.id, task)
    try:
        assert await cancel_request("owned-request", other) == {"cancelled": False}
        assert not task.cancelled()
        assert await cancel_request("owned-request", owner) == {"cancelled": True}
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        _active_requests.pop("owned-request", None)
