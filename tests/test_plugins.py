import json
import sys
from pathlib import Path

import pytest

from shiritaibot.config import PluginConfig
from shiritaibot.core import Context, PublicError
from shiritaibot.plugin_api import PluginContext


async def test_plugin_reload_is_atomic_and_removes_stale_tools(app):
    directory = app.home / "plugins" / "example"
    directory.mkdir(parents=True)
    (directory / "plugin.toml").write_text('api = 1\nname = "example"', encoding="utf-8")
    source = """from shiritaibot.plugin_api import Plugin, schema
class Entry(Plugin):
    async def start(self):
        self.context.register_tool("example", "Example", schema({}), self.call)
    async def call(self, ctx, args):
        return "FIRST"
"""
    path = directory / "main.py"
    path.write_text(source, encoding="utf-8")
    app.config.value.plugins.append(PluginConfig(id="example", enabled=True))
    ctx = Context("web:owner", "owner", "agent", True)
    await app.plugins.reload("example")
    assert json.loads(await app.registry.execute(ctx, "example", {})) == "FIRST"
    stale = app.registry.tools["example"]
    path.write_text(source.replace("FIRST", "OTHER"), encoding="utf-8")
    await app.plugins.reload("example")
    assert json.loads(await app.registry.execute(ctx, "example", {})) == "OTHER"
    with pytest.raises(PublicError):
        await stale.handler(ctx, {})
    path.write_text("syntax error : :", encoding="utf-8")
    with pytest.raises(SyntaxError):
        await app.plugins.reload("example")
    assert json.loads(await app.registry.execute(ctx, "example", {})) == "OTHER"
    app.config.value.plugins[-1].enabled = False
    await app.plugins.reload("example")
    assert "example" not in app.registry.tools


async def test_learn_bridge_uses_stdin_and_secrets_not_commandline(app):
    from shiritaibot.learn import OneLearnPlugin as Entry

    await app.vault.set("learn-pass", "test-secret")
    script = "import sys,json; r=json.load(sys.stdin); print(json.dumps({'ok':True,'data':{'operation':r['operation'],'owner':r['owner'],'has_password':bool(r['config']['password'])}}))"
    ctx = PluginContext(
        "learn",
        {"command": [sys.executable, "-c", script], "password": "secret:learn-pass"},
        app.home,
        app.vault.resolve,
        app.proactive.emit,
    )
    entry = Entry(ctx)
    await entry.start()
    try:
        result = await entry.invoke("courses", {})
        assert result == {
            "ok": True,
            "data": {"operation": "courses", "owner": "owner", "has_password": True},
        }
        assert "test-secret" not in str(await app.store.list("activity"))
    finally:
        await entry.close()


async def test_learn_first_sync_is_baseline_then_emits_only_changes(app):
    from shiritaibot.learn import OneLearnPlugin as Entry

    ctx = PluginContext("learn", {}, app.home, app.vault.resolve, app.proactive.emit)
    entry = Entry(ctx)
    await entry.start()
    data = [{"homework_id": "hw1", "title": "old"}]

    async def invoke(*args):
        return {"ok": True, "data": data}

    entry.invoke = invoke
    try:
        await entry.sync_once()
        assert await app.store.list("event") == []
        await entry.sync_once()
        assert await app.store.list("event") == []
        data[0]["title"] = "new"
        await entry.sync_once()
        await entry.sync_once()
        assert len(await app.store.list("event")) == 1
        assert (await app.store.list("event"))[0]["event"]["kind"] == "learn.assignment.updated"
    finally:
        await entry.close()


@pytest.mark.skipif(
    not Path("src/shiritaibot/plugin/thu_learn/main.py").is_file(),
    reason="Local THU plugin is installed separately",
)
async def test_thu_login_tools_flow_and_permission_boundaries(app):
    app.config.value.plugins = [PluginConfig(id="thu_learn", enabled=True)]
    await app.plugins.reload("thu_learn")
    plugin = app.plugins.loaded["thu_learn"]
    calls = []

    async def invoke(operation, params, owner="owner"):
        calls.append((operation, params, owner))
        if operation == "login":
            return {"ok": False, "status": "verification_required", "message": "请用户提供验证码"}
        return {"ok": True, "data": {"status": "authenticated"}}

    plugin.invoke = invoke
    owner = Context("web:owner", "owner", "agent", True)
    specs = {tool["function"]["name"] for tool in app.registry.specs(owner)}
    assert {"thu_learn_login", "thu_learn_verify_sms"} <= specs
    result = json.loads(await app.registry.execute(owner, "thu_learn_login", {}))
    assert result["status"] == "verification_required"
    result = json.loads(
        await app.registry.execute(owner, "thu_learn_verify_sms", {"code": "654321"})
    )
    assert result["data"]["status"] == "authenticated"
    assert calls == [
        ("login", {"resend": False}, "owner"),
        ("verify_sms", {"code": "654321"}, "owner"),
    ]
    invalid = json.loads(await app.registry.execute(owner, "thu_learn_verify_sms", {"code": "bad"}))
    assert "error" in invalid
    for ctx in [
        Context("group:qq:1", "owner", "agent", True, True),
        Context("private:qq:2", "friend", "chat"),
        Context("background:test", "owner", "agent", True, background=True),
    ]:
        for name in ["thu_learn_login", "thu_learn_verify_sms"]:
            assert app.permissions.decision(ctx, app.registry.tools[name]) == "deny"
            assert name not in {tool["function"]["name"] for tool in app.registry.specs(ctx)}
        with pytest.raises(PublicError):
            await plugin.login(ctx, {})
    assert len(calls) == 2
    await app.registry.execute(owner, "thu_learn_login", {"resend_sms": True})
    assert calls[-1] == ("login", {"resend": True}, "owner")
    assert "654321" not in str(await app.store.list("activity"))
