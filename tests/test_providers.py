import json

import httpx
import pytest

from shiritaibot.config import ModelConfig, ProviderConfig
from shiritaibot.llm import parse_reply, request_payload


@pytest.mark.parametrize(
    "protocol,body",
    [
        (
            "chat_completions",
            {"choices": [{"message": {"content": "ok", "reasoning_content": "opaque"}}]},
        ),
        (
            "responses",
            {
                "output": [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "encrypted_content": "opaque",
                        "summary": [],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok"}],
                    },
                ]
            },
        ),
        (
            "anthropic",
            {
                "content": [
                    {"type": "thinking", "thinking": "opaque", "signature": "signed"},
                    {"type": "text", "text": "ok"},
                ]
            },
        ),
        (
            "gemini",
            {
                "candidates": [
                    {"content": {"parts": [{"text": "ok", "thoughtSignature": "opaque"}]}}
                ]
            },
        ),
    ],
)
def test_codecs_preserve_same_model_opaque_state(protocol, body):
    reply = parse_reply(protocol, "m", body)
    assert reply.text == "ok"
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": reply.text, "_opaque": reply.opaque},
    ]
    path, payload = request_payload(protocol, "m", "system", messages, [], 100)
    assert "opaque" in json.dumps(payload)
    assert "opaque" not in json.dumps(
        request_payload(protocol, "another", "system", messages, [], 100)[1]
    )
    if protocol == "responses":
        assert payload["store"] is False
        assert "reasoning.encrypted_content" in payload["include"]


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic", "gemini"])
def test_tool_roundtrip(protocol):
    messages = [
        {"role": "user", "content": "time?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call1",
                    "type": "function",
                    "function": {"name": "current_time", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call1", "content": '{"utc":"2026-10-27"}'},
    ]
    path, payload = request_payload(protocol, "model", "sys", messages, [], 100)
    assert "current_time" in json.dumps(payload)
    assert "2026-10-27" in json.dumps(payload)
    assert path.startswith("/")


async def test_provider_fallback_and_usage(app):
    app.config.value.providers = [
        ProviderConfig(id="p", base_url="https://fake.test/v1", api_key="")
    ]
    app.config.value.models = [
        ModelConfig(id="bad", provider="p", model="bad"),
        ModelConfig(id="good", provider="p", model="good"),
    ]
    app.config.value.routes = {"agent": ["bad", "good"]}
    requests = []

    async def handle(req):
        body = json.loads(req.content)
        requests.append(body)
        if body["model"] == "bad":
            return httpx.Response(400)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "worked"}}], "usage": {"prompt_tokens": 10}},
        )

    await app.llm.client.aclose()
    app.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    reply = await app.llm.complete("agent", "sys", [{"role": "user", "content": "hi"}], [])
    assert reply.text == "worked"
    assert [r["model"] for r in requests] == ["bad", "good"]
    assert (await app.store.list("usage"))[0]["usage"]["prompt_tokens"] == 10


async def test_json_output_requests_native_json_mode_only_when_enabled(app):
    app.config.value.providers = [
        ProviderConfig(id="p", base_url="https://fake.test/v1", api_key="")
    ]
    app.config.value.models = [
        ModelConfig(id="plain", provider="p", model="plain"),
        ModelConfig(id="json", provider="p", model="json", json_output=True),
    ]
    requests = []

    async def handle(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    await app.llm.client.aclose()
    app.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    messages = [{"role": "user", "content": "hi"}]
    for model in ("plain", "json"):
        app.config.value.routes = {"light": [model]}
        await app.llm.complete("light", "sys", messages, [], json_output=True)
    await app.llm.complete("light", "sys", messages, [])
    assert [r.get("response_format") for r in requests] == [
        None,
        {"type": "json_object"},
        None,
    ]
