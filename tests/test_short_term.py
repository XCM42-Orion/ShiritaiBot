import asyncio
from datetime import timedelta

import pytest
from test_agent import ToolLLM
from test_permissions import OWNER, eventually
from test_proactive import Summarizer

from shiritaibot.core import Context, Reply, now
from shiritaibot.memory import ShortTermMemory
from shiritaibot.store import Store


@pytest.fixture
async def memory_app(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    app.short_term.llm = Summarizer()
    return app


async def test_main_turn_is_summarized_from_its_own_complete_tool_trace(memory_app):
    app = memory_app
    app.runtime.llm = ToolLLM()
    await app.runtime.submit(OWNER, "现在几点")
    jobs = await eventually(lambda: app.store.list("memory_summary"))
    assert jobs[0]["turn_status"] == "completed"
    assert not await app.store.list("short_term")
    await app.short_term.tick()
    rows = await app.short_term.recent()
    assert len(rows) == 1 and rows[0]["loop"] == "main"
    material = app.short_term.llm.inputs[0][0]["content"]
    assert "现在几点" in material and "current_time" in material and "工具调用完成" in material
    assert not await app.store.list("memory_summary")


async def test_long_turn_summary_includes_tail_and_bounds_merge_requests(memory_app):
    app = memory_app
    ctx = Context("web:owner", "owner", "agent", True, turn="long-turn")
    await app.store.append(
        ctx.session, ctx.turn, {"role": "user", "content": "x" * 80000 + "最后的关键事实"}
    )
    await app.short_term.enqueue(ctx, "completed")
    await app.short_term.tick()
    inputs = [m[0]["content"] for m in app.short_term.llm.inputs]
    assert all(len(content) <= 16000 for content in inputs)
    assert any("最后的关键事实" in content for content in inputs)
    assert (await app.short_term.recent())[0]["id"] == "long-turn"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_failed_and_cancelled_main_turns_also_enqueue_summary(memory_app, status):
    app = memory_app
    entered = asyncio.Event()

    class Model:
        async def complete(self, *args, **kwargs):
            entered.set()
            if status == "failed":
                raise RuntimeError("model unavailable")
            await asyncio.Event().wait()

    app.runtime.llm = Model()
    await app.runtime.submit(OWNER, "开始")
    await entered.wait()
    if status == "cancelled":
        await app.runtime.submit(OWNER, "/stop")
    jobs = await eventually(lambda: app.store.list("memory_summary"))
    assert jobs[0]["turn_status"] == status
    await app.short_term.tick()
    assert (await app.short_term.recent())[0]["turn_status"] == status


async def test_retry_is_durable_and_never_uses_audit_log_as_summary(memory_app):
    app = memory_app

    class Broken:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("offline")

    ctx = Context("web:owner", "owner", "agent", True, turn="retry-turn")
    await app.store.append(ctx.session, ctx.turn, {"role": "user", "content": "需要保留"})
    await app.short_term.enqueue(ctx, "completed")
    app.short_term.llm = Broken()
    await app.short_term.tick()
    assert not await app.store.list("short_term")
    pending = (await app.store.list("memory_summary"))[0]
    assert pending["error"] == "RuntimeError" and pending["status"] == "pending"
    pending["retry_at"] = 0
    await app.store.put("memory_summary", pending["id"], pending)
    restored = ShortTermMemory(app.store, Summarizer())
    await restored.tick()
    assert (await restored.recent())[0]["id"] == "retry-turn"


async def test_restart_recovers_turn_that_never_enqueued_its_summary(tmp_path):
    from shiritaibot.core import EventBus

    store = Store(tmp_path, EventBus())
    await store.open()
    await store.put(
        "turn",
        "crashed-turn",
        {
            "actor": "owner",
            "status": "running",
            "memory_scope": "owner",
            "loop": "main",
        },
        "web:owner",
    )
    await store.append("web:owner", "crashed-turn", {"role": "user", "content": "崩溃前的请求"})
    await store.close()
    restored = Store(tmp_path, EventBus())
    await restored.open()
    try:
        memory = ShortTermMemory(restored, Summarizer())
        await memory.recover()
        await memory.recover()  # Recovery is idempotent.
        assert len(await restored.list("memory_summary")) == 1
        await memory.tick()
        row = (await memory.recent())[0]
        assert row["id"] == "crashed-turn" and row["turn_status"] == "interrupted"
    finally:
        await restored.close()


async def test_expiry_legacy_audit_and_chat_isolation(memory_app):
    app = memory_app
    for ctx in [
        Context("web:owner", "owner", "agent", True, turn="owner-turn"),
        Context("group:a:1", "member", "chat", group=True, turn="chat-turn"),
    ]:
        await app.store.append(ctx.session, ctx.turn, {"role": "user", "content": ctx.session})
        await app.short_term.enqueue(ctx, "completed")
    await app.short_term.tick()
    assert len(await app.store.list("short_term")) == 2
    assert [r["id"] for r in await app.short_term.recent()] == ["owner-turn"]
    await app.store.put(
        "short_term",
        "expired",
        {"created": (now() - timedelta(days=8)).isoformat(), "summary": "过期"},
        "owner",
    )
    await app.store.put(
        "short_term", "old-audit", {"created": now().isoformat(), "steps": []}, "owner"
    )
    assert [r["id"] for r in await app.short_term.recent()] == ["owner-turn"]


async def test_handoff_waits_for_main_turn_without_breaking_tool_pairs(memory_app):
    app = memory_app
    tool_result_seen, release = asyncio.Event(), asyncio.Event()

    class Waiting(ToolLLM):
        async def complete(self, route, system, messages, tools, **kwargs):
            if any(m["role"] == "tool" for m in messages):
                tool_result_seen.set()
                await release.wait()
            return await super().complete(route, system, messages, tools, **kwargs)

    app.runtime.llm = Waiting()
    await app.runtime.submit(OWNER, "查时间")
    await tool_result_seen.wait()
    notification = asyncio.create_task(app.adapters.notify_owner("主动通知最终消息"))
    # An approval prompt must remain immediate while the main turn is active.
    await app.permissions.notify("请确认工具审批")
    assert not notification.done()
    assert (await app.store.list("reply"))[0]["text"] == "请确认工具审批"
    release.set()
    await notification
    history = await app.store.history(OWNER.session)
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant", "assistant"]
    assert history[-1]["content"] == "主动通知最终消息" and history[-1]["_proactive"]

    class Followup:
        async def complete(self, route, system, messages, tools, **kwargs):
            assert any(m.get("_proactive") and m["content"] == "主动通知最终消息" for m in messages)
            return Reply(text="接着聊")

    app.runtime.llm = Followup()
    await app.runtime.submit(OWNER, "好")

    async def replied():
        return any(r["text"] == "接着聊" for r in await app.store.list("reply"))

    await eventually(replied)
