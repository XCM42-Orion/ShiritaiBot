import asyncio

from test_permissions import eventually

from shiritaibot.agent import repair_history
from shiritaibot.core import Context, Reply


class ToolLLM:
    def __init__(self):
        self.inputs = []

    async def complete(self, route, system, messages, tools, **kwargs):
        self.inputs.append(list(messages))
        if not any(m["role"] == "tool" for m in messages):
            return Reply(
                calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "current_time", "arguments": "{}"},
                    }
                ]
            )
        return Reply(text="工具调用完成")


async def test_agent_tool_loop_persists_and_delivers(app):
    llm = ToolLLM()
    app.runtime.llm = llm
    await app.runtime.submit(Context("web:owner", "owner", "agent", True), "现在几点")
    replies = await eventually(lambda: app.store.list("reply"))
    assert replies[0]["text"] == "工具调用完成"
    history = await app.store.history("web:owner")
    assert [x["role"] for x in history] == ["user", "assistant", "tool", "assistant"]
    assert len(llm.inputs) == 2
    rows = await eventually(lambda: app.store.list("turn"))
    assert rows[0]["status"] == "completed"


async def test_steering_and_stop(app):
    entered, release = asyncio.Event(), asyncio.Event()

    class LLM(ToolLLM):
        async def complete(self, *args, **kwargs):
            if not self.inputs:
                entered.set()
                await release.wait()
            return await super().complete(*args, **kwargs)

    llm = LLM()
    app.runtime.llm = llm
    ctx = Context("web:owner", "owner", "agent", True)
    await app.runtime.submit(ctx, "开始")
    await asyncio.wait_for(entered.wait(), 2)
    assert await app.runtime.submit(ctx, "补充要求") == "steering"
    release.set()
    await eventually(lambda: app.store.list("reply"))
    assert any("补充要求" in m.get("content", "") for m in llm.inputs[-1])
    entered.clear()
    release.clear()
    llm.inputs.clear()
    ctx2 = Context("web:second", "owner", "agent", True)
    await app.runtime.submit(ctx2, "开始")
    await asyncio.wait_for(entered.wait(), 2)
    await app.runtime.submit(ctx2, "/stop")

    async def cancelled():
        return [r for r in await app.store.list("turn") if r["status"] == "cancelled"]

    assert await eventually(cancelled)


async def test_repetition_guard(app):
    class Repeating:
        async def complete(self, *args, **kwargs):
            return Reply(
                calls=[
                    {
                        "id": "repeat",
                        "type": "function",
                        "function": {"name": "current_time", "arguments": "{}"},
                    }
                ]
            )

    app.runtime.llm = Repeating()
    await app.runtime.submit(Context("web:owner", "owner", "agent", True), "go")
    reply = await eventually(lambda: app.store.list("reply"))
    assert "重复" in reply[0]["text"]


def test_history_repairs_dangling_calls_and_trims_complete_pairs():
    history = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "user", "content": "new"},
    ]
    repaired = repair_history(history, 10000)
    assert repaired[2]["role"] == "tool"
    assert repaired[2]["tool_call_id"] == "1"
    assert repair_history(history, 20) == [{"role": "user", "content": "new"}]


async def test_private_memory_does_not_enter_chat_prompt(app):
    owner = Context("web:owner", "owner", "agent", True)
    await app.memory.add(owner, "我最喜欢的密码提示是银河")
    group = Context("group:qq:1", "owner", "chat", True, True)
    llm = ToolLLM()
    app.runtime.llm = llm
    await app.runtime.submit(group, "银河")
    await eventually(lambda: app.store.list("outbox"))
    assert "密码提示" not in str(llm.inputs)
