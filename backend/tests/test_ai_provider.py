import json

import httpx
import pytest
from pytest import MonkeyPatch

from serversense.db import SessionLocal
from serversense.services.ai import (
    FOLLOW_UP_PROMPT,
    GROUNDED_ANSWER_PROMPT,
    _calendar_window_days,
    _context_message_char_budget,
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


def test_provider_messages_enforce_total_context_budget_and_keep_current_request() -> None:
    question = "Explain the storage trend from the current measurements."
    messages = _provider_messages(
        question,
        [
            {"role": "user", "content": "old question " * 1000},
            {"role": "assistant", "content": "old answer " * 1000},
            {"role": "user", "content": "recent question"},
            {"role": "assistant", "content": "recent answer"},
        ],
        curated_context={"telemetry": {"large": "x" * 20_000}},
        max_context_chars=12_000,
    )

    assert messages[-1] == {"role": "user", "content": question}
    assert messages[-2] == {"role": "system", "content": FOLLOW_UP_PROMPT}
    assert "recent answer" in [item["content"] for item in messages]
    assert sum(len(item["content"]) for item in messages) <= 12_000


def test_context_window_reserves_output_and_tool_schema_capacity() -> None:
    config = {
        "context_window": 4096,
        "max_output_tokens": 512,
        "max_context_chars": 30_000,
    }

    without_tools = _context_message_char_budget(config, [])
    with_tools = _context_message_char_budget(
        config,
        [{"type": "function", "function": {"name": "example", "description": "x" * 500}}],
    )

    assert without_tools == (4096 - 512 - 64) * 3
    assert with_tools < without_tools
    assert _context_message_char_budget(config | {"max_context_chars": 2_000}, []) == 2_000


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


def test_quality_upgrade_challenge_requires_upgrade_evidence() -> None:
    history = [
        {"role": "user", "content": "What changed on my server today?"},
        {"role": "assistant", "content": "Radarr imported Mayday."},
    ]

    assert _required_tool("Wasn't that just a quality upgrade?", history) == (
        "get_quality_upgrades"
    )
    assert _calendar_window_days("Wasn't that just a quality upgrade?", history) == 1


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
    assert "reasoning_effort" not in calls[0]


async def test_ollama_uses_low_reasoning_for_interactive_answers(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(
            200,
            content=_stream({"choices": [{"delta": {"content": "Visible answer."}}]}),
        )

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return async_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    with SessionLocal() as db:
        answer, _, _ = await chat(
            db,
            "What changed on my server today?",
            {
                "provider": "ollama",
                "endpoint": "http://ollama.test:11434",
                "model": "qwen3.5:9b",
                "timeout_seconds": 5,
            },
        )

    assert answer == "Visible answer."
    assert calls[0]["reasoning_effort"] == "low"


async def test_ollama_retries_empty_reasoning_completion_without_reasoning(
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
                    {"choices": [{"delta": {"reasoning": "Hidden reasoning"}}]},
                    {"choices": [{"delta": {}, "finish_reason": "length"}]},
                ),
            )
        return httpx.Response(
            200,
            content=_stream({"choices": [{"delta": {"content": "Recovered answer."}}]}),
        )

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return async_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    with SessionLocal() as db:
        answer, _, _ = await chat(
            db,
            "What changed on my server today?",
            {
                "provider": "ollama",
                "endpoint": "http://ollama.test:11434",
                "model": "qwen3.5:9b",
                "timeout_seconds": 5,
            },
        )

    assert answer == "Recovered answer."
    assert [call["reasoning_effort"] for call in calls] == ["low", "none"]


async def test_empty_length_limited_completion_is_not_saved_as_an_answer(
    monkeypatch: MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_stream(
                {"choices": [{"delta": {"reasoning": "Hidden reasoning"}}]},
                {"choices": [{"delta": {}, "finish_reason": "length"}]},
            ),
        )

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return async_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    with SessionLocal() as db:
        with pytest.raises(RuntimeError, match="response-token limit"):
            await chat(
                db,
                "What changed on my server today?",
                {
                    "provider": "ollama",
                    "endpoint": "http://ollama.test:11434",
                    "model": "qwen3.5:9b",
                    "timeout_seconds": 5,
                },
            )
