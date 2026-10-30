import asyncio
import json

import pytest

from shiritaibot.config import Settings
from shiritaibot.core import Context, PublicError, Tool
from shiritaibot.tools import schema

OWNER = Context("web:owner", "owner", "agent", True)

# Basic conversation tools remain available in Chat mode (web_search needs a Tavily key).
CHAT_TOOLS = ["artifact_read", "current_time"]


def tool_names(specs):
    return [spec["function"]["name"] for spec in specs]


async def eventually(check):
    async with asyncio.timeout(3):
        while not (result := await check()):  # noqa: ASYNC110 -- bounded polling of persisted state
            await asyncio.sleep(0.01)
        return result


@pytest.mark.parametrize(
    "ctx",
    [
        Context("group:a:1", "1", "chat"),
        Context("group:a:1", "owner", "agent", True, True),
        Context("private:a:2", "2", "agent"),
    ],
)
async def test_private_capabilities_cannot_escape_into_chat(app, ctx):
    for tool in [
        "shell",
        "memory_search",
        "memory_save",
        "routine_create",
        "subagent_start",
        "qq_file_download",
    ]:
        assert app.permissions.decision(ctx, app.registry.tools[tool]) == "deny"
    assert tool_names(app.registry.specs(ctx)) == CHAT_TOOLS
    result = json.loads(
        await app.registry.execute(
            ctx, "memory_save", {"kind": "fact", "content": "must not", "evidence": "must not"}
        )
    )
    assert "error" in result
    assert await app.store.query("SELECT id FROM mem_fact") == []


@pytest.mark.parametrize("allow", [False, True])
async def test_approval_is_one_shot_and_exact(app, allow):
    effects = []

    async def handler(ctx, args):
        effects.append(args["value"])
        return {"ok": True}

    app.registry.add(
        Tool(
            "danger_test",
            "",
            schema({"value": {"type": "string"}}, ["value"]),
            handler,
            risk="dangerous",
        )
    )
    task = asyncio.create_task(app.registry.execute(OWNER, "danger_test", {"value": "exact"}))
    rows = await eventually(lambda: app.store.list("approval"))
    assert effects == []
    assert rows[0]["args"] == {"value": "exact"}
    await app.permissions.resolve(rows[0]["id"], allow)
    result = json.loads(await task)
    assert bool(result.get("ok")) is allow
    assert effects == (["exact"] if allow else [])
    with pytest.raises(PublicError):
        await app.permissions.resolve(rows[0]["id"], True)


async def test_cancel_pending_approval_cannot_later_execute(app):
    async def never(ctx, args):
        pytest.fail("approval must prevent handler")

    app.registry.add(Tool("danger_test", "", schema({}), never, risk="dangerous"))
    task = asyncio.create_task(app.registry.execute(OWNER, "danger_test", {}))
    rows = await eventually(lambda: app.store.list("approval"))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert (await app.store.get("approval", rows[0]["id"]))["status"] == "cancelled"
    with pytest.raises(PublicError):
        await app.permissions.resolve(rows[0]["id"], True)


async def test_scope_policy_intersects_and_background_uses_default(app):
    app.config.value.policy.scope_tools = {"web:*": ["memory_*"], "web:owner": ["memory_search"]}
    assert app.permissions.decision(OWNER, app.registry.tools["memory_save"]) == "deny"
    assert app.permissions.decision(OWNER, app.registry.tools["memory_search"]) == "allow"
    background = Context("background:test", "owner", "agent", True, background=True)
    assert app.permissions.decision(background, app.registry.tools["routine_create"]) == "approve"


def test_config_rejects_duplicate_ids_and_accepts_inline_credentials():
    assert Settings(providers=[{"id": "a", "api_key": "raw-key"}]).providers[0].api_key == "raw-key"
    with pytest.raises(ValueError):
        Settings(adapters=[{"id": "a"}, {"id": "a"}])
    assert (
        Settings(plugins=[{"id": "learn", "settings": {"password": "raw"}}])
        .plugins[0]
        .settings["password"]
        == "raw"
    )
    with pytest.raises(ValueError):
        Settings(routes={"agent": ["missing"]})


@pytest.mark.parametrize(
    "preset,safe,unsafe",
    [
        ("all_manual", "approve", "approve"),
        ("safe_manual", "allow", "approve"),
        ("safe_auto", "allow", "auto"),
        ("full_access", "allow", "allow"),
    ],
)
async def test_preset_matrix_includes_shell_and_new_tools(app, preset, safe, unsafe):
    from shiritaibot.config import SessionPolicy

    app.config.value.policy.sessions[OWNER.session] = SessionPolicy(preset=preset)
    assert app.permissions.decision(OWNER, app.registry.tools["current_time"]) == safe
    for name in ("shell", "routine_create", "memory_save", "subagent_start"):
        assert app.permissions.decision(OWNER, app.registry.tools[name]) == unsafe
    # Registration after selecting a preset still follows its declared safety.
    tool = Tool("new_plugin_tool", "", schema({}), lambda *_: None, risk="sensitive")
    app.registry.add(tool)
    assert app.permissions.decision(OWNER, tool) == unsafe


@pytest.mark.parametrize(
    "permission,expected",
    [
        ("deny", "deny"),
        ("manual", "approve"),
        ("auto", "auto"),
        ("allow", "allow"),
    ],
)
async def test_per_tool_override_and_chat_ceiling(app, permission, expected):
    from shiritaibot.config import SessionPolicy

    profile = SessionPolicy(preset="full_access", tools={"shell": permission})
    app.config.value.policy.sessions[OWNER.session] = profile
    assert app.permissions.decision(OWNER, app.registry.tools["shell"]) == expected
    profile.mode = "chat"
    assert tool_names(app.registry.specs(OWNER)) == CHAT_TOOLS
    assert "error" in await app.registry.execute(OWNER, "shell", {"command": "echo blocked"})


async def test_group_and_nonowner_can_use_explicitly_granted_agent_tools(app):
    from shiritaibot.config import SessionPolicy

    effects = []

    async def handler(ctx, args):
        effects.append(ctx.session)
        return "ok"

    app.registry.add(Tool("group_action", "", schema({}), handler, risk="normal"))
    group = Context("group:a:1", "member", group=True)
    private = Context("private:a:2", "friend")
    for ctx in (group, private):
        assert tool_names(app.registry.specs(ctx)) == CHAT_TOOLS
        app.config.value.policy.sessions[ctx.session] = SessionPolicy(
            tools={"group_action": "allow"}
        )
        assert json.loads(await app.registry.execute(ctx, "group_action", {})) == "ok"
    assert effects == [group.session, private.session]
    assert (
        tool_names(app.registry.specs(Context("group:a:other", "member", group=True))) == CHAT_TOOLS
    )


async def test_main_defaults_and_session_override_persist(app):
    from shiritaibot.config import ConfigService, SessionPolicy

    app.config.value.policy.group_default = SessionPolicy(preset="all_manual")
    group = Context("group:a:1", "member", group=True)
    assert app.permissions.context(group).mode == "agent"
    assert app.permissions.decision(group, app.registry.tools["current_time"]) == "approve"
    await app.permissions.set_session(group, SessionPolicy(mode="chat", preset="full_access"))
    reloaded = ConfigService(app.home)
    await reloaded.load()
    assert reloaded.value.policy.sessions[group.session].mode == "chat"
    assert app.permissions.context(group).mode == "chat"
    assert tool_names(app.registry.specs(group)) == CHAT_TOOLS


async def test_proactive_snapshot_is_independent_of_main_and_new_defaults(app):
    from shiritaibot.config import SessionPolicy, ToolPolicy

    main = SessionPolicy(mode="chat", preset="full_access")
    app.config.value.policy.sessions[OWNER.session] = main
    ctx = Context("background:proactive:old", "owner", "agent", True, background=True)
    old = app.permissions.context(ctx)
    app.config.value.policy.proactive_default = ToolPolicy(preset="full_access")
    new = app.permissions.context(Context("background:proactive:new", "owner", background=True))
    assert app.permissions.decision(old, app.registry.tools["shell"]) == "approve"
    assert app.permissions.decision(new, app.registry.tools["shell"]) == "allow"
    assert tool_names(app.registry.specs(OWNER)) == CHAT_TOOLS
    with pytest.raises(PublicError):
        await app.permissions.set_session(old, SessionPolicy())
    with pytest.raises(ValueError):
        Settings(policy={"sessions": {old.session: {"mode": "agent"}}})


@pytest.mark.parametrize(
    "response,approved",
    [
        ('{"allow":true,"reason":"符合用户请求"}', True),
        ('{"allow":false,"reason":"未获授权"}', False),
        ('{"allow":"true","reason":"无效类型"}', False),
        ('{"allow":true}', False),
        ("not JSON", False),
        (None, False),
    ],
)
async def test_auto_review_is_independent_strict_and_audited(app, response, approved):
    from shiritaibot.core import Reply

    calls, effects = [], []

    class Reviewer:
        async def complete(self, route, system, messages, tools, settings):
            calls.append((route, system, messages, tools, settings))
            if response is None:
                raise TimeoutError
            return Reply(text=response)

    async def handler(ctx, args):
        effects.append(args["value"])
        return {"ok": True}

    app.permissions.llm = Reviewer()
    app.config.value.policy.owner_default.tools["reviewed_action"] = "auto"
    app.config.value.policy.approval_model = "review-model"
    app.registry.add(
        Tool(
            "reviewed_action",
            "执行操作",
            schema({"value": {"type": "string"}}, ["value"]),
            handler,
            risk="dangerous",
        )
    )
    await app.store.append(OWNER.session, "test", {"role": "user", "content": "请执行指定操作"})
    result = json.loads(await app.registry.execute(OWNER, "reviewed_action", {"value": "exact"}))
    assert bool(result.get("ok")) is approved
    assert effects == (["exact"] if approved else [])
    row = (await app.store.list("approval"))[0]
    assert row["reviewer"] == "model"
    assert row["status"] == ("approved" if approved else "denied")
    assert row["args"] == {"value": "exact"} and row["reason"]
    assert calls[0][0] == "approval" and calls[0][3] == []
    assert calls[0][4].routes["approval"] == ["review-model"]
    assert "请执行指定操作" in calls[0][2][0]["content"]
    assert not app.permissions.pending
    with pytest.raises(PublicError):
        await app.permissions.resolve(row["id"], True)


async def test_auto_approval_cancellation_and_revocation_do_not_execute(app):
    from shiritaibot.core import Reply

    entered, release = asyncio.Event(), asyncio.Event()
    effects = []

    class Reviewer:
        async def complete(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return Reply(text='{"allow":true,"reason":"approved"}')

    async def handler(ctx, args):
        effects.append(True)

    app.permissions.llm = Reviewer()
    app.config.value.policy.owner_default.tools["review_action"] = "auto"
    app.registry.add(Tool("review_action", "", schema({}), handler, risk="normal"))
    task = asyncio.create_task(app.registry.execute(OWNER, "review_action", {}))
    await asyncio.wait_for(entered.wait(), 3)
    app.config.value.policy.owner_default.tools["review_action"] = "deny"
    release.set()
    assert "权限已变更" in await task
    assert not effects
    app.config.value.policy.owner_default.tools["review_action"] = "auto"
    entered.clear()
    release.clear()
    task = asyncio.create_task(app.registry.execute(OWNER, "review_action", {}))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert (await app.store.list("approval"))[0]["status"] == "cancelled"
    assert not effects


async def test_manual_approval_revocation_and_chat_approval_command(app):
    from shiritaibot.config import SessionPolicy

    effects = []

    async def handler(ctx, args):
        effects.append(True)

    app.registry.add(Tool("manual_action", "", schema({}), handler, risk="normal"))
    task = asyncio.create_task(app.registry.execute(OWNER, "manual_action", {}))
    rows = await eventually(lambda: app.store.list("approval"))
    app.config.value.policy.sessions[OWNER.session] = SessionPolicy(mode="chat")
    await app.runtime.submit(OWNER, f"/approve {rows[0]['id']}")
    assert "权限已变更" in await task
    assert not effects


async def test_plugin_registration_requires_explicit_valid_safety(app):
    from shiritaibot.plugin_api import PluginContext

    context = PluginContext("example", {}, app.home, app.vault.resolve, app.proactive.emit)
    with pytest.raises(TypeError):
        context.register_tool("missing", "", schema({}), lambda *_: None)
    with pytest.raises(ValueError):
        context.register_tool("bad", "", schema({}), lambda *_: None, risk="unknown")
    for risk in ("safe", "normal", "sensitive", "dangerous"):
        context.register_tool(risk, "", schema({}), lambda *_: None, risk=risk)
    assert [tool.safe for tool in context.tools] == [True, False, False, False]


async def test_shell_executes_without_approval_in_full_access(app):
    app.config.value.policy.owner_default.preset = "full_access"
    result = json.loads(await app.registry.execute(OWNER, "shell", {"command": "printf permitted"}))
    assert result["exit_code"] == 0 and result["stdout"] == "permitted"
    assert not await app.store.list("approval")
    app.config.value.policy.owner_default.tools["shell"] = "deny"
    assert "权限策略拒绝" in await app.registry.execute(
        OWNER, "shell", {"command": "printf blocked"}
    )


async def test_all_manual_requires_approval_even_for_safe_tools(app):
    app.config.value.policy.owner_default.preset = "all_manual"
    task = asyncio.create_task(app.registry.execute(OWNER, "current_time", {}))
    rows = await eventually(lambda: app.store.list("approval"))
    assert not task.done()
    await app.permissions.resolve(rows[0]["id"], True)
    assert "local_time" in json.loads(await task)
