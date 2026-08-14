import httpx
from pytest import MonkeyPatch

from serversense.db import SessionLocal
from serversense.services.ai import chat
from serversense.services.demo import seed_demo_data


def test_openai_compatible_provider_executes_read_only_tool_call(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        payload = kwargs["json"]
        assert isinstance(payload, dict)
        calls.append(payload)
        request = httpx.Request("POST", url)
        if len(calls) == 1:
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "safe-call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "get_storage_forecast",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        assert payload["messages"][-1]["role"] == "tool"
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "The measured 30-day forecast is available.",
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with SessionLocal() as db:
        seed_demo_data(db)
        answer, tools, model = chat(
            db,
            "How long until I run out of storage?",
            {
                "provider": "openai_compatible",
                "endpoint": "http://local-model.test",
                "model": "local-test-model",
                "max_tool_calls": 3,
                "timeout_seconds": 5,
                "temperature": 0.1,
            },
        )
    assert answer == "The measured 30-day forecast is available."
    assert tools == ["get_storage_forecast"]
    assert model == "local-test-model"
    assert len(calls) == 2
