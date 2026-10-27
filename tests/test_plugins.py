import asyncio
import base64
import json
import shlex
import sys
from email import policy
from email.message import EmailMessage
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


def mail(subject: str, body: str, message_id: str) -> bytes:
    message = EmailMessage()
    message["From"] = "teacher@example.edu"
    message["To"] = "owner@example.com"
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(body)
    return message.as_bytes()


async def fake_imap(box: dict, logins: list):
    """Minimal IMAP4rev1 peer: LOGIN, EXAMINE, UID SEARCH, UID FETCH, LOGOUT."""

    async def session(reader, writer):
        async def send(data: bytes) -> None:
            writer.write(data)
            await writer.drain()

        await send(b"* OK [CAPABILITY IMAP4rev1] fake\r\n")
        while line := (await reader.readline()).decode().rstrip("\r\n"):
            tag, command, *rest = line.split(" ", 2)
            args, done = (rest[0] if rest else ""), f"{tag} OK done\r\n".encode()
            command = command.upper()
            if command == "LOGIN":
                logins.append(shlex.split(args))
                await send(done)
            elif command in {"SELECT", "EXAMINE"}:
                head = f"* {len(box['messages'])} EXISTS\r\n* OK [UIDVALIDITY {box['validity']}] ok\r\n"
                await send(head.encode() + f"{tag} OK [READ-ONLY] done\r\n".encode())
            elif command == "UID" and args.upper().startswith("SEARCH"):
                first = int(args.split()[2].split(":")[0])
                uids = sorted(box["messages"])
                # IMAP: "n:*" also matches the highest UID even when it is below n.
                found = [u for u in uids if u >= first] or uids[-1:]
                await send(f"* SEARCH {' '.join(map(str, found))}\r\n".encode() + done)
            elif command == "UID" and args.upper().startswith("FETCH"):
                uid = int(args.split()[1])
                raw = box["messages"][uid]
                await send(
                    f"* 1 FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode()
                    + raw
                    + b")\r\n"
                    + done
                )
            elif command == "LOGOUT":
                await send(b"* BYE\r\n" + done)
                break
            else:
                await send(done)
        writer.close()

    return await asyncio.start_server(session, "127.0.0.1", 0)


def email_settings(port: int, **extra) -> dict:
    return {
        "imap_host": "127.0.0.1",
        "imap_port": port,
        "imap_security": "none",
        "username": "bot@example.com",
        "password": "secret:mail-pass",
        "poll": False,
        **extra,
    }


async def test_email_imap_new_mail_becomes_proactive_event(app):
    await app.vault.set("mail-pass", "mail-secret-value")
    box = {"validity": 7, "messages": {1: mail("旧邮件", "old", "<old@x>")}}
    logins: list = []
    server = await fake_imap(box, logins)
    port = server.sockets[0].getsockname()[1]
    app.config.value.plugins = [
        PluginConfig(id="email", enabled=True, settings=email_settings(port))
    ]
    await app.plugins.reload("email")
    plugin = app.plugins.loaded["email"]
    assert "email" not in app.plugins.notifiers  # SMTP channel is optional
    async with server:
        # The first sync is only a baseline: the existing inbox does not flood the owner.
        assert await plugin.sync_once() == 0
        assert await app.store.list("event") == []
        box["messages"][2] = mail("作业截止提醒", "数据结构作业 3 周五 23:59 截止", "<hw3@x>")
        assert await plugin.sync_once() == 1
        assert await plugin.sync_once() == 0
        # A changed UIDVALIDITY re-baselines instead of replaying the mailbox.
        box["validity"] = 8
        box["messages"][3] = mail("另一封", "body", "<other@x>")
        assert await plugin.sync_once() == 0
    events = await app.store.list("event")
    assert len(events) == 1
    event = events[0]["event"]
    assert event["source"] == "email" and event["kind"] == "email.received"
    assert event["title"] == "新邮件：作业截止提醒"
    assert event["payload"]["from"] == "teacher@example.edu"
    assert "周五 23:59" in event["payload"]["snippet"]
    assert logins[0] == ["bot@example.com", "mail-secret-value"]
    assert "mail-secret-value" not in str(events) + str(await app.store.list("activity"))


async def test_email_optional_smtp_channel_is_best_effort(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    app.config.value.proactive.quiet_start = app.config.value.proactive.quiet_end = "00:00"
    await app.vault.set("mail-pass", "mail-secret-value")
    received: list[dict] = []
    server = await fake_smtp(received)
    port = server.sockets[0].getsockname()[1]
    settings = email_settings(
        1, smtp_host="127.0.0.1", smtp_port=port, smtp_security="none", notify_to=["me@x.com"]
    )
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
    message = received[0]["message"]
    assert received[0]["auth"] == [b"bot@example.com", b"mail-secret-value"]
    assert received[0]["rcpt"] == ["me@x.com"]
    assert message["Subject"] == "[ShiritaiBot] 下午三点开组会"
    assert "来源：routine" in message.get_content()

    # A dead SMTP server neither blocks nor repeats the IM notification.
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
        PluginConfig(id="email", enabled=True, settings={"password": "secret:x", "poll": False})
    ]
    with pytest.raises(PublicError) as raised:
        await app.plugins.reload("email")
    message = str(raised.value)
    assert "imap_host" in message and "username" in message and "secret:x" not in message
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
