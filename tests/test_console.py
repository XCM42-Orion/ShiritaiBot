import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from test_permissions import eventually

from shiritaibot.adapters.onebot11 import OneBot
from shiritaibot.config import AdapterConfig, ModelConfig, ProviderConfig
from shiritaibot.core import Context, PublicError, Reply, Tool, canonical
from shiritaibot.store.statistics import statistics, tokens
from shiritaibot.tools import schema
from shiritaibot.web import create_app

# Basic conversation tools remain available in Chat mode (web_search needs a Tavily key).
CHAT_TOOLS = ["artifact_read", "current_time"]


def tool_names(specs):
    return [spec["function"]["name"] for spec in specs]


def test_inline_credentials_roundtrip_and_dashboard_api(tmp_path):
    with TestClient(create_app(tmp_path, token="admin")) as client:
        assert client.get("/api/stats").status_code == 401
        client.headers["Authorization"] = "Bearer admin"
        config = client.get("/api/config").json()
        config["providers"] = [{"id": "provider.1", "api_key": "private-value"}]
        config["models"] = [
            {"id": "GLM-5.3-Flash", "provider": "provider.1", "model": "GLM-5.3-Flash"}
        ]
        config["plugins"][0]["settings"] = {"password": "plugin-private-value"}
        assert client.put("/api/config", json=config).status_code == 200
        result = client.get("/api/config")
        assert "private-value" not in result.text
        assert result.json()["providers"][0]["api_key"].startswith("secret:")
        assert "private-value" not in (tmp_path / "config.toml").read_text("utf-8")
        assert "private-value".encode() not in (tmp_path / "state.sqlite3").read_bytes()
        # Saved opaque values are accepted without re-entering credentials.
        assert client.put("/api/config", json=result.json()).status_code == 200
        config["models"][0]["id"] = "bad alias!"
        error = client.put("/api/config", json=config)
        assert error.status_code == 422 and "private-value" not in error.text
        assert "models.0.id" in error.json()["detail"]
        for hours in [24, 72, 168]:
            response = client.get(f"/api/stats?hours={hours}")
            assert response.status_code == 200, response.text
            assert len(response.json()["buckets"]) == hours + 1
        assert client.get("/api/stats?hours=10").status_code == 422
        assert any(c["name"] == "model" for c in client.get("/api/commands").json())


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic", "gemini"])
async def test_discover_models_credentials_and_pagination(app, protocol):
    await app.vault.set("discovery", "test-key")
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.path == "/v1/models"
        if protocol == "gemini":
            assert request.headers["x-goog-api-key"] == "test-key"
            return httpx.Response(
                200,
                json={
                    "models": [{"name": "models/gemini-test", "outputTokenLimit": 8192}],
                    **({"nextPageToken": "next"} if len(requests) == 1 else {}),
                },
            )
        if protocol == "anthropic":
            assert request.headers["x-api-key"] == "test-key"
            assert request.headers["anthropic-version"] == "2023-06-01"
        else:
            assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "data": [{"id": "GLM-5.3-Flash"}],
                "has_more": len(requests) == 1,
                "last_id": "next",
            },
        )

    await app.llm.client.aclose()
    app.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    result = await app.llm.discover(
        ProviderConfig(
            id="p", protocol=protocol, base_url="https://fake.test/v1", api_key="secret:discovery"
        )
    )
    assert len(result) == 1 and len(requests) == 2
    assert "next" in str(requests[1].url)
    assert not await app.store.list("usage")


async def test_discovery_errors_do_not_echo_provider_secrets(app):
    await app.llm.client.aclose()
    app.llm.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="test-private-key"))
    )
    with pytest.raises(PublicError) as error:
        await app.llm.discover(ProviderConfig(id="p", api_key="test-private-key"))
    assert "401" in str(error.value) and "test-private-key" not in str(error.value)


@pytest.mark.parametrize(
    "usage,protocol,expected",
    [
        (
            {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
            "chat_completions",
            (10, 4, 2, 14),
        ),
        (
            {"input_tokens": 10, "output_tokens": 4, "input_tokens_details": {"cached_tokens": 2}},
            "responses",
            (10, 4, 2, 14),
        ),
        (
            {
                "input_tokens": 10,
                "output_tokens": 4,
                "cache_read_input_tokens": 8,
                "cache_creation_input_tokens": 3,
            },
            "anthropic",
            (21, 4, 8, 25),
        ),
        (
            {
                "promptTokenCount": 10,
                "candidatesTokenCount": 4,
                "thoughtsTokenCount": 3,
                "cachedContentTokenCount": 2,
                "totalTokenCount": 17,
            },
            "gemini",
            (10, 7, 2, 17),
        ),
    ],
)
def test_usage_normalization(usage, protocol, expected):
    result = tokens(usage, protocol)
    assert tuple(result.values()) == expected


async def test_statistics_window_and_message_exclusions(app):
    end = datetime(2026, 10, 27, 12, 30, tzinfo=timezone.utc)
    for index, delta in enumerate(
        [timedelta(hours=24), timedelta(hours=24, seconds=1), timedelta(hours=1)]
    ):
        stamp = (end - delta).timestamp()
        await app.store.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?)",
            ("usage", str(index), "", canonical({"usage": {"prompt_tokens": 10}}), stamp, stamp),
        )
    for message, turn in [
        ({"role": "user", "content": "hi"}, "t"),
        ({"role": "assistant", "content": "ok"}, "t"),
        ({"role": "assistant", "tool_calls": [{}]}, "t"),
        ({"role": "tool"}, "t"),
        ({"role": "user"}, "handoff"),
    ]:
        await app.store.execute(
            "INSERT INTO messages(session,turn,data,created) VALUES(?,?,?,?)",
            ("web:owner", turn, canonical(message), end.timestamp() - 100),
        )
    result = await statistics(app.store, 24, end)
    assert result["totals"]["total_tokens"] == 20
    assert result["totals"]["llm_calls"] == 2
    assert result["totals"]["messages"] == 2
    assert len(result["buckets"]) == 25
    assert result["buckets"][0]["input_tokens"] == 10


async def test_commands_reset_archive_model_and_no_llm(app):
    ctx = Context("web:owner", "owner", "agent", True)
    await app.store.append(ctx.session, "old", {"role": "user", "content": "old context"})
    await app.runtime.submit(ctx, "/clear")
    assert not await app.store.history(ctx.session, include_commands=False)
    assert len(await app.store.query("SELECT * FROM messages WHERE turn='old'")) == 1
    await app.runtime.submit(ctx, "/help")
    assert "/model" in (await app.store.list("reply"))[0]["text"]
    assert not await app.store.list("usage")
    app.config.value.providers = [ProviderConfig(id="p")]
    app.config.value.models = [ModelConfig(id="GLM-5.3-Flash", provider="p", model="glm")]
    await app.runtime.submit(ctx, "/model GLM-5.3-Flash")
    assert (await app.store.get("session_model", ctx.session))["model"] == "GLM-5.3-Flash"
    finished = asyncio.Event()

    class LLM:
        async def complete(self, route, system, messages, tools, settings):
            assert settings.routes[route] == ["GLM-5.3-Flash"]
            assert len(messages) == 1 and "old context" not in str(messages)
            assert "/help" not in str(messages)
            finished.set()
            return Reply(text="ok")

    app.runtime.llm = LLM()
    await app.runtime.submit(ctx, "hello")
    await asyncio.wait_for(finished.wait(), 3)
    await app.runtime.submit(ctx, "/model default")
    assert not (await app.store.get("session_model", ctx.session))["model"]


async def test_commands_private_data_and_approvals_are_denied_in_groups(app):
    ctx = Context("group:qq:1", "owner", "chat", True, True)
    for text in ["/memory", "/tasks", "/approve abc", "/deny abc"]:
        await app.runtime.submit(ctx, text)
        assert "仅限主人私聊" in (await app.store.list("outbox"))[0]["text"]
    ctx = Context("private:qq:friend", "friend", "chat")
    await app.runtime.submit(ctx, "/model GLM-5.3-Flash")
    assert not await app.store.get("session_model", ctx.session)
    await app.runtime.submit(ctx, "/clear")
    assert not await app.store.get("session_epoch", ctx.session)


@pytest.mark.parametrize("allow", [True, False])
async def test_slash_approval_resolves_pending_operation_once(app, allow):
    ctx = Context("web:owner", "owner", "agent", True)
    effects = []

    async def handler(ctx, args):
        effects.append(True)
        return {"ok": True}

    app.registry.add(Tool("command_test", "", schema({}), handler, risk="dangerous"))
    task = asyncio.create_task(app.registry.execute(ctx, "command_test", {}))
    row = (await eventually(lambda: app.store.list("approval")))[0]
    await app.runtime.submit(ctx, f"/{'approve' if allow else 'deny'} {row['id']}")
    await task
    assert effects == ([True] if allow else [])
    await app.runtime.submit(ctx, f"/approve {row['id']}")
    assert "已处理或已过期" in (await app.store.list("reply"))[0]["text"]


async def test_onebot_slash_help_uses_same_command_router(app):
    cfg = AdapterConfig(id="qq", owner_id="1", self_id="100")
    bot = OneBot(cfg, app.vault, app.store, app.adapters.receive)
    bot.self_id = "100"
    await app.adapters.receive(
        bot,
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 1,
            "message_id": 999,
            "message": [{"type": "text", "data": {"text": "/help"}}],
        },
    )
    rows = await app.store.list("outbox")
    assert rows[0]["scope"] == "private:qq:1"
    assert "/approve" in rows[0]["text"]
    assert not await app.store.list("usage")


async def test_commands_respect_scope_tool_restrictions(app):
    ctx = Context("web:owner", "owner", "agent", True)
    app.config.value.policy.scope_tools = {"web:*": ["current_time"]}
    for text in ["/memory", "/tasks"]:
        await app.runtime.submit(ctx, text)
        assert "权限策略拒绝" in (await app.store.list("reply"))[0]["text"]


async def test_tools_command_edits_permissions_modes_and_presets(app):
    ctx = Context("web:owner", "owner", "agent", True)
    await app.runtime.submit(ctx, "/tools shell deny")
    assert app.config.value.policy.sessions[ctx.session].tools["shell"] == "deny"
    assert "shell · 禁止使用" in (await app.store.list("reply"))[0]["text"]
    await app.runtime.submit(ctx, "/tools shell inherit")
    assert "shell" not in app.config.value.policy.sessions[ctx.session].tools
    await app.runtime.submit(ctx, "/tools preset full_access")
    assert app.permissions.decision(ctx, app.registry.tools["shell"]) == "allow"
    await app.runtime.submit(ctx, "/mode chat")
    assert tool_names(app.registry.specs(ctx)) == CHAT_TOOLS
    await app.runtime.submit(ctx, "/tools")
    answer = (await app.store.list("reply"))[0]["text"]
    assert "shell" in answer and "Chat 模式仅开放基础对话工具" in answer
    await app.runtime.submit(ctx, "/tools mode agent")
    assert app.permissions.decision(ctx, app.registry.tools["shell"]) == "allow"
    await app.runtime.submit(ctx, "/tools shell invalid")
    assert "无效" in (await app.store.list("reply"))[0]["text"]
    await app.runtime.submit(ctx, "/tools reset")
    assert ctx.session not in app.config.value.policy.sessions
    assert app.permissions.decision(ctx, app.registry.tools["shell"]) == "approve"
    assert not await app.store.list("usage")


async def test_nonowner_cannot_escalate_tools_but_owner_can_configure_group(app):
    group = Context("group:qq:1", "friend", group=True)
    await app.runtime.submit(group, "/tools preset full_access")
    assert group.session not in app.config.value.policy.sessions
    owner = Context(group.session, "owner", owner=True, group=True)
    await app.runtime.submit(owner, "/tools mode agent")
    await app.runtime.submit(owner, "/tools shell allow")
    assert app.permissions.decision(group, app.registry.tools["shell"]) == "allow"
    await app.runtime.submit(group, "/mode chat")
    assert app.config.value.policy.sessions[group.session].mode == "agent"


def test_raw_rolling_log_tail_and_filter(tmp_path):
    with TestClient(create_app(tmp_path, token="admin")) as client:
        assert client.get("/api/logs/raw").status_code == 401
        client.headers["Authorization"] = "Bearer admin"
        import logging

        logging.getLogger("uvicorn.error").info("uvicorn says hi")
        logging.getLogger("shiritaibot.test").warning("raw-log-marker")
        lines = client.get("/api/logs/raw").json()["lines"]
        assert any("raw-log-marker" in line and "WARNING" in line for line in lines)
        assert any("uvicorn says hi" in line for line in lines)
        filtered = client.get("/api/logs/raw", params={"q": "marker"}).json()["lines"]
        assert filtered and all("marker" in line for line in filtered)
        assert len(client.get("/api/logs/raw", params={"lines": 1}).json()["lines"]) == 1
