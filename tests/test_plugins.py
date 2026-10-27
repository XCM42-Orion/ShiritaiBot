import asyncio
import base64
import json
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from shiritaibot.config import PluginConfig
from shiritaibot.core import Context, ExternalEvent, PublicError
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


async def fake_smtp(received: list[dict]):
    """Minimal plaintext SMTP peer: EHLO, AUTH PLAIN, MAIL, RCPT, DATA, QUIT."""

    async def session(reader, writer):
        mail: dict = {"rcpt": []}

        async def reply(line: str) -> None:
            writer.write((line + "\r\n").encode())
            await writer.drain()

        await reply("220 fake ESMTP")
        while line := (await reader.readline()).decode().rstrip("\r\n"):
            verb = line.split(" ", 1)[0].upper()
            if verb == "EHLO":
                await reply("250-fake\r\n250 AUTH PLAIN")
            elif verb == "AUTH":
                mail["auth"] = base64.b64decode(line.split()[2]).split(b"\0")[1:]
                await reply("235 ok")
            elif verb == "MAIL":
                await reply("250 ok")
            elif verb == "RCPT":
                mail["rcpt"].append(line.split(":", 1)[1].strip("<> "))
                await reply("250 ok")
            elif verb == "DATA":
                await reply("354 go")
                lines = []
                while (data := await reader.readline()) != b".\r\n":
                    lines.append(data)
                mail["message"] = BytesParser(policy=policy.default).parsebytes(b"".join(lines))
                received.append(mail)
                await reply("250 queued")
            elif verb == "QUIT":
                await reply("221 bye")
                break
            else:
                await reply("250 ok")
        writer.close()

    return await asyncio.start_server(session, "127.0.0.1", 0)


async def test_email_plugin_mails_proactive_notifications(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    app.config.value.proactive.quiet_start = app.config.value.proactive.quiet_end = "00:00"
    await app.vault.set("smtp-pass", "smtp-secret-value")
    received: list[dict] = []
    server = await fake_smtp(received)
    port = server.sockets[0].getsockname()[1]
    settings = {
        "host": "127.0.0.1",
        "port": port,
        "security": "none",
        "username": "bot@example.com",
        "password": "secret:smtp-pass",
        "to": ["owner@example.com"],
    }
    app.config.value.plugins = [PluginConfig(id="email", enabled=True, settings=settings)]
    await app.plugins.reload("email")
    assert "email" in app.plugins.notifiers and app.proactive.channels is app.plugins.notifiers

    reminder = ExternalEvent(
        source="routine", kind="routine.reminder", dedup_key="r1", title="下午三点开组会"
    )
    await app.proactive.emit(reminder)
    async with server:
        await app.proactive.tick()
    assert "下午三点开组会" in (await app.store.list("reply"))[0]["text"]
    assert len(received) == 1
    mail = received[0]
    assert mail["auth"] == [b"bot@example.com", b"smtp-secret-value"]
    assert mail["rcpt"] == ["owner@example.com"]
    assert mail["message"]["Subject"] == "[ShiritaiBot] 下午三点开组会"
    assert mail["message"]["From"] == "bot@example.com"
    assert "来源：routine" in mail["message"].get_content()
    assert "smtp-secret-value" not in str(await app.store.list("activity"))

    # SMTP is a best-effort copy: a dead server neither blocks nor repeats the IM notification.
    await app.proactive.emit(reminder.model_copy(update={"dedup_key": "r2", "source": "other"}))
    await app.proactive.tick()
    await app.proactive.tick()
    assert len(await app.store.list("reply")) == 2
    assert len(received) == 1
    failures = [a for a in await app.store.list("activity") if a["kind"] == "notify.channel_failed"]
    assert len(failures) == 1 and failures[0]["detail"].startswith("email:")

    app.config.value.plugins[0].enabled = False
    await app.plugins.reload("email")
    assert "email" not in app.plugins.notifiers


async def test_email_plugin_rejects_incomplete_settings_without_echoing_values(app):
    app.config.value.plugins = [
        PluginConfig(id="email", enabled=True, settings={"password": "secret:x", "to": []})
    ]
    with pytest.raises(PublicError) as raised:
        await app.plugins.reload("email")
    assert "host" in str(raised.value) and "secret:x" not in str(raised.value)
    assert "email" not in app.plugins.loaded and "email" not in app.plugins.notifiers


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
