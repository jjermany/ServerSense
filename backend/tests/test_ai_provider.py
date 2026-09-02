import json

import httpx
from pytest import MonkeyPatch

from serversense.db import SessionLocal
from serversense.services.ai import (
    FOLLOW_UP_PROMPT,
    GROUNDED_ANSWER_PROMPT,
    _calendar_window_days,
    _provider_messages,
    _required_tool,
    chat,
)
from serversense.services.demo import seed_demo_data


def _stream(*chunks: dict) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def test_follow_up_prompt_makes_history_context_only() -> None:
    messages = _provider_messages(
        "Yes, list the recently upgraded titles.",
        [
            {"role": "user", "content": "How long until storage is full?"},
            {"role": "assistant", "content": "About 239 days. Want recent upgrades?"},
        ],
    )

    assert "Current date and time in the configured UTC timezone:" in messages[0]["content"]
    assert "combined_array_data_disks" in messages[0]["content"]
    assert "12-hour format with AM or PM" in messages[0]["content"]
    assert messages[-2] == {"role": "system", "content": FOLLOW_UP_PROMPT}
    assert messages[-1]["content"] == "Yes, list the recently upgraded titles."


def test_upcoming_media_follow_up_requires_calendar_tool() -> None:
    history = [
        {"role": "user", "content": "What's being added this week?"},
        {
            "role": "assistant",
            "content": "There are three upcoming calendar entries. Want the titles?",
        },
    ]

    assert _required_tool("Yes please", history) == "get_upcoming_media"
    assert _required_tool("What's getting downloaded this week?", ()) == "get_upcoming_media"
    assert _calendar_window_days("Yes please", history) == 7


async def test_tool_call_preamble_is_not_compiled_into_final_answer(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(
                200,
                content=_stream(
                    {"choices": [{"delta": {"content": "The old answer repeated. "}}]},
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "media-call",
                                            "function": {
                                                "name": "get_quality_upgrades",
                                                "arguments": "{}",
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                ),
            )
        return httpx.Response(
            200,
            content=_stream({"choices": [{"delta": {"content": "Here are the titles."}}]}),
        )

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return async_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    with SessionLocal() as db:
        answer, tools, _ = await chat(
            db,
            "List the recently upgraded titles.",
            {
                "provider": "openai_compatible",
                "endpoint": "http://local-model.test",
                "model": "local-test-model",
                "max_tool_calls": 2,
                "timeout_seconds": 5,
            },
        )

    assert answer == "Here are the titles."
    assert tools == ["get_quality_upgrades"]
    assert calls[1]["messages"][-1] == {
        "role": "system",
        "content": GROUNDED_ANSWER_PROMPT,
    }


async def test_openai_compatible_provider_streams_and_executes_read_only_tool_call(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(
                200,
                content=_stream(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "safe-call-1",
                                            "type": "function",
                                            "function": {
                                                "name": "get_storage_forecast",
                                                "arguments": "{}",
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
            )
        assert payload["messages"][-2]["role"] == "tool"
        assert payload["messages"][-1]["content"] == GROUNDED_ANSWER_PROMPT
        return httpx.Response(
            200,
            content=_stream(
                {"choices": [{"delta": {"content": "The measured 30-day forecast is available."}}]}
            ),
        )

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return async_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    with SessionLocal() as db:
        seed_demo_data(db)
        answer, tools, model = await chat(
            db,
            "How long until I run out of storage?",
            {
                "provider": "openai_compatible",
                "endpoint": "http://local-model.test",
                "model": "local-test-model",
                "max_tool_calls": 3,
                "max_output_tokens": 256,
                "timeout_seconds": 5,
                "temperature": 0.1,
            },
            [
                {"role": "user", "content": "How is storage trending?"},
                {"role": "assistant", "content": "I can check the measured history."},
            ],
        )
    assert answer == "The measured 30-day forecast is available."
    assert tools == ["get_storage_forecast"]
    assert model == "local-test-model"
    assert len(calls) == 2
    assert calls[0]["stream"] is True
    assert calls[0]["max_tokens"] == 256
    assert calls[0]["messages"][1]["content"] == "How is storage trending?"
