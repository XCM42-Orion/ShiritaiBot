import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from test_permissions import eventually
from test_plugins import email_settings, fake_imap, mail
from test_proactive import Agent, Judge, Summarizer

from shiritaibot.config import PluginConfig
from shiritaibot.plugin.email.main import EmailSettings

START = datetime(2026, 10, 28, 10, tzinfo=UTC)


@pytest.fixture
async def mailbox(app):
    await app.vault.set("mail-pass", "mail-secret-value")
    box = {"validity": 7, "messages": {}, "dates": {}}
    server = await fake_imap(box, [])
    port = server.sockets[0].getsockname()[1]
    app.config.value.plugins = [
        PluginConfig(id="email", enabled=True, settings=email_settings(port))
    ]
    await app.plugins.reload("email")
    plugin = app.plugins.loaded["email"]
    plugin.started_at = START
    try:
        async with server:
            yield app, plugin, box
    finally:
        server.close()
        await server.wait_closed()


def add_mail(box, uid, received_at):
    box["messages"][uid] = mail(f"邮件 {uid}", f"正文 {uid}", f"<mail-{uid}@x>")
    box["dates"][uid] = received_at


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("startup", [3, 4]), ("last_poll", [3, 4]), ("none", [1, 2, 3, 4]), ("custom", [2, 3, 4])],
)
async def test_time_filters_use_exact_server_receipt_time(mailbox, mode, expected):
    app, plugin, box = mailbox
    plugin.settings.time_filter = mode
    # Explicit offset is equivalent to START - 30 minutes.
    plugin.settings.filter_before = datetime.fromisoformat("2026-10-28T17:30:00+08:00")
    for uid, minutes in enumerate([-60, -30, 0, 1], 1):
        add_mail(box, uid, START + timedelta(minutes=minutes))
    assert await plugin.sync_once() == len(expected)
    assert await plugin.sync_once() == 0
    events = await app.store.list("event")
    assert len(events) == 1
    payload = events[0]["event"]["payload"]
    assert payload["count"] == len(expected)
    assert [m["uid"] for m in payload["messages"]] == expected
    assert all(m["received_at"] for m in payload["messages"])


async def test_multiple_emails_start_one_proactive_turn(mailbox):
    app, plugin, box = mailbox
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    app.config.value.proactive.enabled = True
    app.proactive.llm = Judge(importance=2, reason="待处理")
    app.runtime.llm = Agent()
    app.short_term.llm = Summarizer()
    for uid in range(1, 4):
        add_mail(box, uid, START + timedelta(minutes=uid))
    assert await plugin.sync_once() == 3
    await app.proactive.tick()

    async def completed():
        return [t for t in await app.store.list("turn") if t["status"] == "completed"]

    assert len(await eventually(completed)) == 1
    assert app.proactive.llm.calls == 1
    assert len(await app.store.list("event")) == 1
    content = app.runtime.llm.inputs[0][0][0]["content"]
    assert all(f"正文 {uid}" in content for uid in range(1, 4))


async def test_last_poll_cutoff_survives_restart_and_does_not_drop_backlog(mailbox):
    app, plugin, box = mailbox
    plugin.settings.time_filter = "last_poll"
    plugin.settings.max_per_poll = 1
    previous = datetime.now(UTC) - timedelta(hours=1)
    plugin.state_path.write_text(
        json.dumps(
            {
                "uidvalidity": 7,
                "last_uid": 0,
                "last_polled_at": previous.isoformat(),
            }
        )
    )
    for uid in range(1, 4):
        add_mail(box, uid, previous + timedelta(minutes=uid))
    assert await plugin.sync_once() == 1
    state = json.loads(plugin.state_path.read_text())
    assert state["pending_filter_before"] == previous.isoformat()
    # Reload retains the pending window, even though startup and poll times advanced.
    plugin.context.settings["time_filter"] = "last_poll"
    plugin.context.settings["max_per_poll"] = 1
    app.config.value.plugins[0].settings.update(plugin.context.settings)
    await app.plugins.reload("email")
    plugin = app.plugins.loaded["email"]
    assert await plugin.sync_once() == 1
    assert await plugin.sync_once() == 1
    assert await plugin.sync_once() == 0
    state = json.loads(plugin.state_path.read_text())
    assert "pending_filter_before" not in state
    assert state["last_uid"] == 3
    # A newly discovered UID whose receipt time predates the last poll is filtered.
    add_mail(box, 4, previous)
    assert await plugin.sync_once() == 0
    assert len(await app.store.list("event")) == 3


async def test_failed_emit_keeps_cursors_and_retry_deduplicates_batch(mailbox, monkeypatch):
    app, plugin, box = mailbox
    plugin.settings.time_filter = "none"
    for uid in range(1, 3):
        add_mail(box, uid, START)
    emit = plugin.context.emit

    async def emit_then_fail(event):
        await emit(event)
        raise RuntimeError("interrupted before cursor commit")

    monkeypatch.setattr(plugin.context, "emit", emit_then_fail)
    with pytest.raises(RuntimeError):
        await plugin.sync_once()
    assert not plugin.state_path.exists()
    monkeypatch.setattr(plugin.context, "emit", emit)
    assert await plugin.sync_once() == 2
    assert len(await app.store.list("event")) == 1
    assert await plugin.sync_once() == 0


async def test_failed_fetch_does_not_advance_last_poll(mailbox, monkeypatch):
    _, plugin, _ = mailbox
    previous = {"uidvalidity": 7, "last_uid": 0, "last_polled_at": START.isoformat()}
    plugin.state_path.write_text(json.dumps(previous))
    monkeypatch.setattr(plugin.context, "resolve_secret", AsyncMock(side_effect=TimeoutError))
    with pytest.raises(TimeoutError):
        await plugin.sync_once()
    assert json.loads(plugin.state_path.read_text()) == previous


def test_custom_time_filter_requires_aware_cutoff_and_accepts_empty_default():
    values = email_settings(993)
    assert EmailSettings(**values, filter_before="").filter_before is None
    for cutoff in [None, "", "invalid", "2026-10-28T09:00:00"]:
        with pytest.raises(ValidationError):
            EmailSettings(**values, time_filter="custom", filter_before=cutoff)
    cfg = EmailSettings(**values, time_filter="custom", filter_before="2026-10-28T09:00:00+08:00")
    assert cfg.filter_before.utcoffset() == timedelta(hours=8)
