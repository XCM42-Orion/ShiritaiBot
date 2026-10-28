import asyncio
from datetime import datetime, timedelta

import pytest
from test_permissions import eventually

from shiritaibot.app import Application
from shiritaibot.core import Context, ExternalEvent, now
from shiritaibot.proactive import quiet_hours
from shiritaibot.routine import RoutineItem


async def test_memory_chinese_search_and_archive(app):
    ctx = Context("web:owner", "owner", "agent", True)
    row = await app.memory.add(ctx, "学习电动力学时先解释基本原理")
    assert (await app.memory.search(ctx, "电动力学"))[0]["id"] == row["id"]
    await app.memory.archive(row["id"])
    assert await app.memory.search(ctx, "电动力学") == []


async def test_vault_encrypts_and_does_not_expose_plaintext(app):
    await app.vault.set("test", "credential-value")
    assert await app.vault.resolve("secret:test") == "credential-value"
    assert "credential-value" not in str(await app.store.list("secret"))


async def test_restart_recovers_interrupted_turns_approvals_and_reminders(tmp_path):
    app = Application(tmp_path)
    await app.start()
    await app.store.put("turn", "t", {"status": "running"})
    await app.store.put("approval", "a", {"status": "pending"})
    row = await app.routine.create(
        RoutineItem(kind="reminder", title="persist", due=now() + timedelta(hours=1))
    )
    await app.close()
    restored = Application(tmp_path)
    await restored.start()
    try:
        assert (await restored.store.get("turn", "t"))["status"] == "interrupted"
        assert (await restored.store.get("approval", "a"))["status"] == "interrupted"
        assert (await restored.store.get("routine", row["id"]))["status"] == "scheduled"
    finally:
        await restored.close()


async def test_scheduler_dedup_and_quiet_hours(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    app.config.value.proactive.quiet_start = "00:00"
    app.config.value.proactive.quiet_end = "00:00"
    await app.routine.create(
        RoutineItem(kind="reminder", title="提醒测试", due=now() - timedelta(seconds=5))
    )
    await app.routine.tick()
    await app.routine.tick()
    assert len(await app.store.list("event")) == 1
    await app.proactive.tick()

    async def processed():
        return all(row["status"] == "processed" for row in await app.store.list("event"))

    await eventually(processed)
    await app.proactive.tick()
    assert len(await app.store.list("reply")) == 1
    assert "提醒测试" in (await app.store.list("reply"))[0]["text"]


async def test_event_dedup(app):
    event = ExternalEvent(source="test", kind="example", dedup_key="one", title="hello")
    assert await app.proactive.emit(event) == await app.proactive.emit(event)
    assert len(await app.store.list("event")) == 1


@pytest.mark.parametrize(
    "time,expected",
    [
        ("2026-10-27T23:45:00+08:00", True),
        ("2026-10-28T07:00:00+08:00", True),
        ("2026-10-28T12:00:00+08:00", False),
    ],
)
def test_overnight_quiet(time, expected):
    assert quiet_hours("23:30", "08:00", datetime.fromisoformat(time)) is expected


def test_routine_requires_timezone_and_valid_cron():
    with pytest.raises(ValueError):
        RoutineItem(kind="reminder", title="x", due="2026-10-27T12:00:00")
    with pytest.raises(ValueError):
        RoutineItem(kind="job", title="x", cron="invalid")
