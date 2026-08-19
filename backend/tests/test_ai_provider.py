import json

import httpx
from pytest import MonkeyPatch

from serversense.db import SessionLocal
from serversense.services.ai import chat
from serversense.services.demo import seed_demo_data


def _stream(*chunks: dict) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


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
        assert payload["messages"][-1]["role"] == "tool"
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
