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


def jev_config(app, routes):
    app.config.value.providers = [
        ProviderConfig(id="ts", protocol="systemone", base_url="https://jev.test/v1", api_key="k"),
        ProviderConfig(id="p", base_url="https://fake.test/v1", api_key=""),
    ]
    app.config.value.models = [
        ModelConfig(id="jev", provider="ts", model="jev-latest"),
        ModelConfig(id="text", provider="p", model="text"),
    ]
    app.config.value.routes.update(routes)


async def use_transport(app, handle):
    await app.llm.client.aclose()
    app.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def test_decision_models_answer_decide_and_are_skipped_for_text(app):
    jev_config(app, {"light": ["jev", "text"]})
    requests = []

    async def handle(req):
        body = json.loads(req.content)
        requests.append((str(req.url), req.headers.get("authorization"), body))
        if req.url.host == "jev.test":
            return httpx.Response(
                200,
                json={
                    "model": "jev-latest",
                    "answers": {"q": {"type": "noul", "noul": 0.9}},
                    "usage": {"input_tokens": 5},
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "summary"}}]})

    await use_transport(app, handle)
    questions = {"q": {"type": "noul", "instructions": "?"}}
    assert await app.llm.decide("light", "state", questions) == {"q": {"type": "noul", "noul": 0.9}}
    reply = await app.llm.complete("light", "sys", [{"role": "user", "content": "hi"}], [])
    assert reply.text == "summary"
    assert requests[0] == (
        "https://jev.test/v1/systemone",
        "Bearer k",
        {"model": "jev-latest", "state": "state", "questions": questions},
    )
    assert [url for url, *_ in requests[1:]] == ["https://fake.test/v1/chat/completions"]


async def test_decide_returns_none_without_decision_model_or_on_failure(app):
    jev_config(app, {"light": ["text"], "router": ["jev"]})
    statuses = iter([200, 404])

    async def handle(req):
        return httpx.Response(next(statuses), json={"answers": {}})

    await use_transport(app, handle)
    questions = {"q": {"type": "noul", "instructions": "?"}}
    assert await app.llm.decide("light", "s", questions) is None
    assert await app.llm.decide("router", "s", questions) is None
    assert await app.llm.decide("router", "s", questions) is None
    failures = [r for r in await app.store.list("activity") if r.get("kind") == "provider.failed"]
    assert any(r.get("detail") == "ts: HTTP 404" for r in failures)
    with pytest.raises(Exception, match="决策模型不生成文本"):
        await app.llm.complete("router", "sys", [{"role": "user", "content": "hi"}], [])


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.typesafe.ai/v1", ["jev-latest", "jev-preview"]),
        ("https://openrouter.ai/api/v1", ["~typesafe/jev-latest"]),
    ],
)
async def test_decision_model_discovery_uses_host_model_names(app, base_url, expected):
    provider = ProviderConfig(id="ts", protocol="systemone", base_url=base_url, api_key="k")
    assert [m["id"] for m in await app.llm.discover(provider)] == expected


def test_decision_models_are_limited_to_decision_routes():
    from shiritaibot.config import Settings

    providers = [ProviderConfig(id="ts", protocol="systemone", base_url="https://jev.test/v1")]
    models = [ModelConfig(id="jev", provider="ts", model="jev-latest")]
    Settings(providers=providers, models=models, routes={"router": ["jev"], "light": ["jev"]})
    with pytest.raises(ValueError, match="decision models"):
        Settings(providers=providers, models=models, routes={"agent": ["jev"]})
    with pytest.raises(ValueError, match="must generate text"):
        Settings(
            providers=providers,
            models=models,
            routes={"light": ["jev"]},
            policy={"approval_model": "jev"},
        )


@pytest.mark.parametrize(("chat", "expected"), [(0.8, "agent_chat"), (0.55, "agent")])
async def test_router_uses_jev_probabilities(app, chat, expected):
    from shiritaibot.core import Context

    jev_config(app, {"router": ["jev"], "agent": ["text"], "agent_chat": ["text"]})
    app.config.value.agent_split = True

    async def handle(req):
        answer = {"type": "choice", "choice": "chat", "confidence": chat}
        answer["probabilities"] = {"chat": chat, "work": 1 - chat}
        return httpx.Response(200, json={"answers": {"route": answer}, "usage": {}})

    await use_transport(app, handle)
    ctx = Context("web:owner", "owner", "agent", True)
    assert await app.runtime.route(ctx, app.config.value, "今天心情不好") == expected


async def test_triage_uses_jev_most_probable_level(app):
    from shiritaibot.core import ExternalEvent

    jev_config(app, {"light": ["jev"]})

    async def handle(req):
        questions = json.loads(req.content)["questions"]
        assert questions["importance"]["type"] == "score"
        return httpx.Response(
            200,
            json={
                "answers": {
                    "importance": {
                        "type": "score",
                        "score": 2.9,
                        "confidence": 0.5,
                        "probabilities": {"0": 0.05, "1": 0.05, "2": 0.1, "3": 0.2, "4": 0.6},
                    },
                    "decision": {"type": "choice", "choice": "digest", "confidence": 0.7},
                },
                "usage": {},
            },
        )

    await use_transport(app, handle)
    event = ExternalEvent(source="email", kind="email.received", dedup_key="a", title="服务器宕机")
    decision = await app.proactive.judge(event)
    assert (decision.importance, decision.decision, decision.message) == (4, "notify", "服务器宕机")
    assert "Jev" in decision.reason
