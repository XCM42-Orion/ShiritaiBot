import asyncio
import json

import pytest

from shiritaibot.config import Settings
from shiritaibot.core import Context, PublicError, Tool
from shiritaibot.tools import schema

OWNER = Context("web:owner", "owner", "agent", True)


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
        "memory_write",
        "routine_create",
        "subagent_start",
        "qq_file_download",
    ]:
        assert app.permissions.decision(ctx, app.registry.tools[tool]) == "deny"
    assert [x["function"]["name"] for x in app.registry.specs(ctx)] == ["current_time"]
    result = json.loads(
        await app.registry.execute(ctx, "memory_write", {"content": "must not be stored"})
    )
    assert "error" in result
    assert await app.memory.list() == []


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


async def test_scope_policy_intersects_and_background_is_readonly(app):
    app.config.value.policy.scope_tools = {"web:*": ["memory_*"], "web:owner": ["memory_search"]}
    assert app.permissions.decision(OWNER, app.registry.tools["memory_write"]) == "deny"
    assert app.permissions.decision(OWNER, app.registry.tools["memory_search"]) == "allow"
    background = Context("background:test", "owner", "agent", True, background=True)
    assert app.permissions.decision(background, app.registry.tools["routine_create"]) == "deny"


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
