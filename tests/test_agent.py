import asyncio

import pytest
from test_permissions import eventually

from shiritaibot.agent import repair_history
from shiritaibot.core import Context, Reply
from shiritaibot.llm import parse_reply


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


@pytest.mark.parametrize("first_text", ["我先查一下时间。", "", " \n"])
async def test_main_loop_delivers_text_before_tools_without_reasoning(app, monkeypatch, first_text):
    entered, release = asyncio.Event(), asyncio.Event()
    intermediate = [first_text, "查到了，再确认一下。"]

    class LLM:
        async def complete(self, route, system, messages, tools, **kwargs):
            step = sum(m["role"] == "tool" for m in messages)
            if step == len(intermediate):
                return Reply(text="工具调用完成")
            return parse_reply(
                "chat_completions",
                "test",
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": intermediate[step],
                                "reasoning_content": "内部推理，不应发送",
                                "tool_calls": [
                                    {
                                        "id": f"call-{step}",
                                        "type": "function",
                                        "function": {"name": "current_time", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ]
                },
            )

    execute = app.registry.execute

    async def blocked_execute(*args, **kwargs):
        entered.set()
        await release.wait()
        return await execute(*args, **kwargs)

    app.runtime.llm = LLM()
    monkeypatch.setattr(app.registry, "execute", blocked_execute)
    await app.runtime.submit(Context("web:owner", "owner", "agent", True), "现在几点")
    await asyncio.wait_for(entered.wait(), 2)
    replies = await app.store.list("reply")
    assert [r["text"] for r in replies] == ([first_text] if first_text.strip() else [])
    release.set()

    async def completed():
        return [r for r in await app.store.list("turn") if r["status"] == "completed"]

    await eventually(completed)
    replies = await app.store.list("reply")
    assert [r["text"] for r in reversed(replies)] == [
        text for text in [*intermediate, "工具调用完成"] if text.strip()
    ]


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


@pytest.mark.parametrize("mode,route", [("chat", "chat"), ("agent", "agent")])
async def test_session_mode_overrides_transport_and_controls_tool_exposure(app, mode, route):
    from shiritaibot.config import SessionPolicy

    app.config.value.policy.sessions["group:qq:mode"] = SessionPolicy(mode=mode)
    done = asyncio.Event()

    class LLM:
        async def complete(self, selected_route, system, messages, tools, settings):
            assert selected_route == route
            names = {tool["function"]["name"] for tool in tools}
            assert "current_time" in names
            assert ("shell" in names) is (mode == "agent")
            done.set()
            return Reply(text="ok")

    app.runtime.llm = LLM()
    await app.runtime.submit(Context("group:qq:mode", "member", "chat", group=True), "hello")
    await asyncio.wait_for(done.wait(), 3)


class RoutedLLM:
    def __init__(self, verdict):
        self.verdict, self.routes = verdict, []

    async def complete(self, route, system, messages, tools, **kwargs):
        self.routes.append(route)
        return Reply(text=self.verdict if route == "router" else "好")


@pytest.mark.parametrize(
    ("split", "verdict", "expected"),
    [
        (True, "chat", ["router", "agent_chat"]),
        (True, "work", ["router", "agent"]),
        (False, "chat", ["agent"]),
    ],
)
async def test_agent_work_chat_split(app, split, verdict, expected):
    from shiritaibot.config import ModelConfig, ProviderConfig

    cfg = app.config.value
    cfg.providers = [ProviderConfig(id="p", base_url="http://x")]
    cfg.models = [ModelConfig(id=m, provider="p", model=m) for m in ("w", "c", "r")]
    cfg.routes.update(agent=["w"], agent_chat=["c"], router=["r"])
    cfg.agent_split = split
    llm = RoutedLLM(verdict)
    app.runtime.llm = llm
    await app.runtime.submit(Context("web:owner", "owner", "agent", True), "今天心情不好")
    await eventually(lambda: app.store.list("reply"))
    assert llm.routes == expected
    turn = (await app.store.list("turn"))[0]
    assert turn["route"] == expected[-1]


class EchoLLM:
    def __init__(self):
        self.inputs = []

    async def complete(self, route, system, messages, tools, **kwargs):
        self.inputs.append(list(messages))
        return Reply(text="收到")


async def test_debounce_merges_consecutive_messages(app):
    app.config.value.policy.debounce_seconds = 0.05
    app.config.value.policy.debounce_max_seconds = 0.2
    llm = EchoLLM()
    app.runtime.llm = llm
    ctx = Context("private:qq-main:1", "owner", "agent", True)

    assert await app.runtime.submit(ctx, "帮我看看") == "buffering"
    await app.runtime.submit(ctx, "明天")
    await app.runtime.submit(ctx, "的课表")

    sent = await eventually(lambda: app.store.list("outbox"))
    assert [message["text"] for message in sent] == ["收到"]
    assert len(llm.inputs) == 1
    content = llm.inputs[0][-1]["content"]
    assert "帮我看看\n明天\n的课表" in content


async def test_debounce_flushes_finished_sentence_immediately(app):
    app.config.value.policy.debounce_seconds = 5
    app.config.value.policy.debounce_max_seconds = 10
    app.runtime.llm = EchoLLM()
    ctx = Context("private:qq-main:1", "owner", "agent", True)

    await app.runtime.submit(ctx, "现在几点？")

    sent = await asyncio.wait_for(eventually(lambda: app.store.list("outbox")), timeout=2)
    assert [message["text"] for message in sent] == ["收到"]


async def test_debounce_skips_web_sessions(app):
    app.config.value.policy.debounce_seconds = 5
    app.config.value.policy.debounce_max_seconds = 10
    app.runtime.llm = EchoLLM()

    await app.runtime.submit(Context("web:owner", "owner", "agent", True), "你好")

    replies = await asyncio.wait_for(eventually(lambda: app.store.list("reply")), timeout=2)
    assert [reply["text"] for reply in replies] == ["收到"]


async def test_debounce_keeps_different_senders_apart(app):
    app.config.value.policy.debounce_seconds = 0.05
    app.config.value.policy.debounce_max_seconds = 0.2
    app.runtime.llm = EchoLLM()
    first = Context("group:qq:1", "1", "chat", group=True)
    second = Context("group:qq:1", "2", "chat", group=True)

    await app.runtime.submit(first, "第一条")
    await app.runtime.submit(second, "第二条")

    async def both():
        sent = await app.store.list("outbox")
        return sent if len(sent) >= 2 else None

    sent = await eventually(both)
    assert [message["text"] for message in sent] == ["收到", "收到"]


async def test_stop_discards_buffered_messages(app):
    app.config.value.policy.debounce_seconds = 5
    app.config.value.policy.debounce_max_seconds = 10
    app.runtime.llm = EchoLLM()
    ctx = Context("private:qq-main:1", "owner", "agent", True)

    await app.runtime.submit(ctx, "先别回答")
    await app.runtime.submit(ctx, "/stop")

    await asyncio.sleep(0.1)
    assert await app.store.list("turn") == []


async def test_debounce_can_be_disabled(app):
    app.config.value.policy.debounce_enabled = False
    app.config.value.policy.debounce_seconds = 5
    app.config.value.policy.debounce_max_seconds = 10
    app.runtime.llm = EchoLLM()
    ctx = Context("private:qq-main:1", "owner", "agent", True)

    assert await app.runtime.submit(ctx, "帮我看看") == "queued"
    sent = await asyncio.wait_for(eventually(lambda: app.store.list("outbox")), timeout=2)
    assert [message["text"] for message in sent] == ["收到"]


async def test_debounce_uses_configured_suffixes(app):
    app.config.value.policy.debounce_seconds = 5
    app.config.value.policy.debounce_max_seconds = 10
    app.config.value.policy.debounce_suffixes = ["~"]
    app.runtime.llm = EchoLLM()
    ctx = Context("private:qq-main:1", "owner", "agent", True)

    await app.runtime.submit(ctx, "现在几点？")
    await asyncio.sleep(0.1)
    assert await app.store.list("turn") == []
    await app.runtime.submit(ctx, "告诉我~")

    sent = await asyncio.wait_for(eventually(lambda: app.store.list("outbox")), timeout=2)
    assert [message["text"] for message in sent] == ["收到"]
