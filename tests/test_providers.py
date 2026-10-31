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
            context={"compression_model": "jev"},
        )
    # Auto-approval is a decision route, so Jev may lead it like router / light / memory.
    assert Settings(providers=providers, models=models, routes={"approval": ["jev"]}).routes[
        "approval"
    ] == ["jev"]


def test_legacy_approval_model_becomes_the_approval_route():
    from shiritaibot.config import Settings

    providers = [ProviderConfig(id="p", base_url="https://fake.test/v1")]
    models = [
        ModelConfig(id="m1", provider="p", model="a"),
        ModelConfig(id="m2", provider="p", model="b"),
    ]
    old = Settings(providers=providers, models=models, policy={"approval_model": "m1"})
    assert old.routes["approval"] == ["m1"]
    assert "approval_model" not in old.model_dump()["policy"]
    # An explicit route wins over the legacy field.
    both = Settings(
        providers=providers,
        models=models,
        routes={"approval": ["m2", "m1"]},
        policy={"approval_model": "m1"},
    )
    assert both.routes["approval"] == ["m2", "m1"]
    assert Settings.model_validate(old.model_dump()).routes["approval"] == ["m1"]
    with pytest.raises(ValueError, match="unknown model"):
        Settings(providers=providers, models=models, policy={"approval_model": "missing"})


YES = '{"allow":true,"reason":"文本模型复核通过"}'
NO = '{"allow":false,"reason":"文本模型复核拒绝"}'


@pytest.mark.parametrize(
    ("allow", "policy", "text_reply", "approved", "reviewer"),
    [
        (0.95, {}, YES, True, "Jev"),  # a confident approval is final
        (0.03, {}, YES, True, "文本模型"),  # by default a denial gets a second opinion
        (0.03, {"approval_on_deny": "deny"}, YES, False, "Jev"),
        # Below the deny threshold a denial is only unsure, which still falls back.
        (
            0.05,
            {"approval_on_deny": "deny", "approval_deny_threshold": 0.97},
            YES,
            True,
            "文本模型",
        ),
        (0.6, {}, YES, True, "文本模型"),
        (0.6, {"approval_on_unsure": "deny"}, YES, False, "Jev"),
        (0.85, {"approval_allow_threshold": 0.9}, NO, False, "文本模型"),
        (0.6, {}, None, False, "none"),  # unsure Jev and no text model: never approve alone
    ],
)
async def test_auto_approval_uses_jev_then_falls_back_to_text(
    app, allow, policy, text_reply, approved, reviewer
):
    from shiritaibot.core import Context, Tool
    from shiritaibot.tools import schema

    routes = ["jev", "text"] if text_reply else ["jev"]
    jev_config(app, {"approval": routes, "light": []})
    seen = []

    async def handle(req):
        body = json.loads(req.content)
        if req.url.host == "jev.test":
            seen.append("jev")
            assert body["questions"]["verdict"]["type"] == "choice"
            assert body["state"]["tool"] == "reviewed" and body["state"]["arguments"] == {"v": 1}
            answer = {
                "type": "choice",
                "choice": "allow" if allow > 0.5 else "deny",
                "probabilities": {"allow": allow, "deny": 1 - allow},
            }
            return httpx.Response(200, json={"answers": {"verdict": answer}})
        seen.append("text")
        return httpx.Response(200, json={"choices": [{"message": {"content": text_reply}}]})

    await use_transport(app, handle)
    effects = []

    async def run(ctx, args):
        effects.append(args)
        return {"ok": True}

    for key, value in policy.items():
        setattr(app.config.value.policy, key, value)
    app.config.value.policy.owner_default.tools["reviewed"] = "auto"
    app.registry.add(
        Tool("reviewed", "", schema({"v": {"type": "integer"}}), run, risk="dangerous")
    )
    owner = Context("web:owner", "owner", "agent", True)
    result = json.loads(await app.registry.execute(owner, "reviewed", {"v": 1}))
    assert bool(result.get("ok")) is approved and len(effects) == int(approved)
    assert seen == (["jev", "text"] if reviewer == "文本模型" else ["jev"])
    row = (await app.store.list("approval"))[0]
    assert row["status"] == ("approved" if approved else "denied")
    if reviewer == "Jev":
        assert "Jev" in row["reason"]
    elif reviewer == "文本模型":
        assert row["reason"] == ("文本模型复核通过" if approved else "文本模型复核拒绝")
    else:
        assert "文本模型" in row["reason"]  # tells the owner what the route is missing


@pytest.mark.parametrize(("chat", "expected"), [(0.8, "agent_chat"), (0.55, "agent")])
async def test_router_uses_jev_probabilities(app, chat, expected):
    from shiritaibot.core import Context

    jev_config(app, {"router": ["jev"], "agent": ["text"], "agent_chat": ["text"]})
    app.config.value.agent_split = True

    async def handle(req):
        answer = {"type": "choice", "choice": "chat", "confidence": chat}
        answer["probabilities"] = {"chat": chat, "work": 1 - chat}
        # The owner's private turns also ask whether memory is needed, in the same request.
        assert json.loads(req.content)["questions"]["recall"]["type"] == "noul"
        recall = {"type": "noul", "noul": 0.9}
        return httpx.Response(
            200, json={"answers": {"route": answer, "recall": recall}, "usage": {}}
        )

    await use_transport(app, handle)
    ctx = Context("web:owner", "owner", "agent", True, turn="r1")
    assert await app.runtime.route(ctx, app.config.value, "今天心情不好") == expected
    assert app.runtime.recall_hints == {"r1": 0.9}


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
                    "remember": {"type": "noul", "noul": 0.15},
                },
                "usage": {},
            },
        )

    await use_transport(app, handle)
    event = ExternalEvent(source="email", kind="email.received", dedup_key="a", title="服务器宕机")
    decision = await app.proactive.judge(event)
    assert (decision.importance, decision.decision, decision.message) == (4, "notify", "服务器宕机")
    assert "Jev" in decision.reason
    assert decision.remember == 0.15  # Kept for memory extraction's gate.
