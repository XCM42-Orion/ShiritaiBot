import asyncio
from datetime import datetime, timedelta

import pytest
from test_permissions import eventually

from shiritaibot.app import Application
from shiritaibot.core import Context, ExternalEvent, now
from shiritaibot.proactive import quiet_hours
from shiritaibot.routine import RoutineItem


async def test_memory_chinese_search_and_forget(app):
    ctx = Context("web:owner", "owner", "agent", True, turn="t1")
    text = "学习电动力学时先解释基本原理"
    await app.store.append("web:owner", "t1", {"role": "user", "content": text})
    row = await app.memory.save_fact(ctx, text, text, "主人")
    assert (await app.memory.search("电动力学"))["results"][0]["id"] == row["id"]
    await app.memory.forget(row["id"], "主人要求忘掉")
    assert (await app.memory.search("电动力学"))["results"] == []


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


async def routine_tool(app, name, args):
    import json

    app.config.value.policy.owner_default.tools[name] = "allow"
    return json.loads(
        await app.registry.execute(Context("web:owner", "owner", "agent", True), name, args)
    )


async def test_routine_update_tool_edits_and_reschedules(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    row = await app.routine.create(
        RoutineItem(kind="reminder", title="交报告", due=now() - timedelta(seconds=5))
    )
    await app.routine.tick()
    assert (await app.store.get("routine", row["id"]))["status"] == "fired"
    later = (now() + timedelta(hours=2)).isoformat()
    result = await routine_tool(
        app, "routine_update", {"id": row["id"], "title": "交实验报告", "due": later}
    )
    assert result["title"] == "交实验报告" and result["kind"] == "reminder"
    # A new time re-arms an already fired reminder.
    assert result["status"] == "scheduled"
    assert result["next_fire"] == datetime.fromisoformat(later).timestamp()
    title_only = await routine_tool(app, "routine_update", {"id": row["id"], "title": "改名"})
    assert title_only["next_fire"] == result["next_fire"] and title_only["due"] == result["due"]

    job = await app.routine.create(RoutineItem(kind="job", title="周报", cron="0 9 * * 1"))
    moved = await routine_tool(app, "routine_update", {"id": job["id"], "cron": "30 20 * * 0"})
    assert moved["cron"] == "30 20 * * 0" and moved["next_fire"] != job["next_fire"]

    done = await app.routine.create(
        RoutineItem(kind="reminder", title="已完成", due=now() + timedelta(hours=1))
    )
    await app.routine.update_status(done["id"], "completed")
    kept = await routine_tool(app, "routine_update", {"id": done["id"], "due": later})
    assert kept["status"] == "completed" and kept["next_fire"] is None


@pytest.mark.parametrize(
    "args,error",
    [
        ({"id": "missing", "title": "x"}, "不存在"),
        ({"id": "ROW"}, "只能修改"),
        ({"id": "ROW", "kind": "todo"}, "参数"),  # kind is not in the schema
        ({"id": "ROW", "due": "2026-11-01T09:00:00"}, "无效"),  # no timezone
        ({"id": "ROW", "end": "2000-01-01T00:00:00+08:00"}, "无效"),  # end before due
    ],
)
async def test_routine_update_rejects_invalid_changes(app, args, error):
    row = await app.routine.create(
        RoutineItem(kind="event", title="组会", due=now() + timedelta(days=1))
    )
    args = {k: (row["id"] if v == "ROW" else v) for k, v in args.items()}
    result = await routine_tool(app, "routine_update", args)
    assert error in result["error"]
    assert (await app.store.get("routine", row["id"]))["title"] == "组会"


async def test_routine_delete_tool_removes_and_stops_reminders(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    row = await app.routine.create(
        RoutineItem(kind="reminder", title="会删掉", due=now() - timedelta(seconds=1))
    )
    result = await routine_tool(app, "routine_delete", {"id": row["id"]})
    assert result == {"deleted": row["id"], "kind": "reminder", "title": "会删掉"}
    assert await app.store.get("routine", row["id"]) is None
    await app.routine.tick()
    assert await app.store.list("event") == []
    again = await routine_tool(app, "routine_delete", {"id": row["id"]})
    assert "不存在" in again["error"]


async def test_finished_routines_are_hidden_then_pruned(app):
    from shiritaibot.routine import KEEP_FINISHED

    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    long_ago = now() - timedelta(days=60)
    todo = await app.routine.create(RoutineItem(kind="todo", title="还没做", due=long_ago))
    done = await app.routine.create(RoutineItem(kind="todo", title="做完了"))
    await app.routine.create(
        RoutineItem(kind="reminder", title="已提醒", due=now() - timedelta(seconds=1))
    )
    await app.routine.create(RoutineItem(kind="event", title="旧组会", due=long_ago))
    soon = await app.routine.create(
        RoutineItem(kind="event", title="明天组会", due=now() + timedelta(days=1))
    )
    await app.routine.tick()
    marked = await routine_tool(app, "routine_update", {"id": done["id"], "status": "completed"})
    assert marked["status"] == "completed"

    def titles(rows):
        return {row["title"] for row in rows}

    assert titles(await routine_tool(app, "routine_list", {})) == {"还没做", "旧组会", "明天组会"}
    assert len(await routine_tool(app, "routine_list", {"include_finished": True})) == 5

    # Recently finished items survive; only those finished past the window go.
    assert await app.routine.prune(now().timestamp()) == 1  # the event ended 60 days ago
    stamp = now().timestamp() + KEEP_FINISHED + 60
    assert await app.routine.prune(stamp) == 2  # the completed todo and the fired reminder
    # The overdue open todo and the upcoming event stay.
    assert {row["id"] for row in await app.store.list("routine")} == {todo["id"], soon["id"]}


async def test_routine_update_status_reports_missing_items(app):
    result = await routine_tool(app, "routine_update", {"id": "missing", "status": "cancelled"})
    assert "不存在" in result["error"]


async def test_routine_edit_tools_need_approval_and_stay_out_of_chat(app):
    owner = Context("web:owner", "owner", "agent", True)
    group = Context("group:a:1", "owner", "agent", True, True)
    for name in ("routine_update", "routine_delete"):
        assert app.permissions.decision(owner, app.registry.tools[name]) == "approve"
        assert app.permissions.decision(group, app.registry.tools[name]) == "deny"
