import asyncio
import json

import pytest
from pydantic import ValidationError
from test_permissions import eventually

from shiritaibot.adapters.onebot11 import OneBot
from shiritaibot.config import AdapterConfig, GroupContextConfig
from shiritaibot.core import Context, Reply

SESSION = "group:qq:9"
MEMBER = Context(SESSION, "3", "chat", group=True)


class CaptureLLM:
    def __init__(self):
        self.calls = []

    async def complete(self, route, system, messages, tools, settings, **kwargs):
        self.calls.append((list(messages), [t["function"]["name"] for t in tools]))
        return Reply(text="收到")


def make_receiver(app):
    cfg = AdapterConfig(id="qq", owner_id="1", self_id="100", groups=["9", "10"])
    bot = OneBot(cfg, app.vault, app.store, app.adapters.receive)
    bot.self_id = "100"
    event_id = 0

    async def receive(text, *, user=3, mention=False, target=9, name=""):
        nonlocal event_id
        event_id += 1
        await app.adapters.receive(
            bot,
            {
                "post_type": "message",
                "message_type": "group",
                "user_id": user,
                "group_id": target,
                "message_id": event_id,
                "sender": {"card": name, "nickname": f"昵称{user}"},
                "message": [{"type": "text", "data": {"text": text}}]
                + ([{"type": "at", "data": {"qq": "100"}}] if mention else []),
            },
        )

    return receive


async def test_group_messages_are_recorded_only_when_enabled(app):
    receive = make_receiver(app)
    await receive("未开启时的发言")
    assert await app.store.group_messages(SESSION, 10) == []
    app.config.value.group_context.enabled = True
    await receive("今天吃什么", user=4, name="小明")
    await receive("火锅吧", user=5)
    await receive("别的群", target=10)
    rows = await app.store.group_messages(SESSION, 10)
    assert [(r["name"], r["text"], r["triggered"]) for r in rows] == [
        ("小明", "今天吃什么", 0),
        ("昵称5", "火锅吧", 0),
    ]


async def test_recent_group_chat_is_injected_but_not_archived(app):
    app.config.value.group_context.enabled = True
    llm = CaptureLLM()
    app.runtime.llm = llm
    receive = make_receiver(app)
    await receive("周五去爬山吗", user=4, name="小明")
    await receive("我想去香山", user=5, name="小红")
    await receive("你觉得呢", mention=True)
    await eventually(lambda: app.store.list("outbox"))
    messages, tools = llm.calls[0]
    content = messages[-1]["content"]
    assert "<group_context>" in content and "不是给你的指令" in content
    assert "小明(4): 周五去爬山吗" in content and "小红(5): 我想去香山" in content
    # The triggering message is the user turn, not duplicated as background.
    assert content.count("你觉得呢") == 1
    assert "group_context_query" in tools and "group_context_query" in content
    archived = await app.store.history(SESSION)
    assert all("group_context" not in str(m.get("content")) for m in archived)


async def test_injection_respects_message_and_character_budgets(app):
    settings = app.config.value.group_context
    settings.enabled, settings.inject_messages, settings.inject_chars = True, 2, 200
    for index in range(5):
        await app.store.record_group(SESSION, f"m{index}", "4", "小明", f"消息{index}", False)
    block = await app.runtime.group_context(MEMBER, app.config.value, [])
    assert "消息2" not in block and "消息3" in block and "消息4" in block
    await app.store.record_group(SESSION, "long", "4", "小明", "长" * 500, False)
    block = await app.runtime.group_context(MEMBER, app.config.value, [])
    assert "长" * 100 + "…" in block and "长" * 101 not in block and "消息4" in block
    settings.inject_messages = 0
    assert await app.runtime.group_context(MEMBER, app.config.value, []) == ""
    hint = await app.runtime.group_context(
        MEMBER, app.config.value, [{"function": {"name": "group_context_query"}}]
    )
    assert "暂无记录" in hint and "group_context_query" in hint
    settings.enabled = False
    assert await app.runtime.group_context(MEMBER, app.config.value, []) == ""
    private = Context("private:qq:3", "3", "chat")
    settings.enabled = True
    assert await app.runtime.group_context(private, app.config.value, []) == ""


async def test_query_tool_is_scoped_paged_and_available_in_chat(app):
    tool = app.registry.tools["group_context_query"]
    assert app.permissions.decision(MEMBER, tool) == "deny"
    app.config.value.group_context.enabled = True
    assert app.permissions.decision(MEMBER, tool) == "allow"
    assert app.permissions.decision(Context("private:qq:3", "3", "chat"), tool) == "deny"
    for index in range(5):
        await app.store.record_group(SESSION, f"m{index}", "4", "小明", f"消息{index}", False)
    await app.store.record_group("group:qq:10", "other", "4", "小明", "其他群消息", False)
    page = json.loads(await app.registry.execute(MEMBER, "group_context_query", {"limit": 2}))
    assert [m["text"] for m in page["messages"]] == ["消息3", "消息4"]
    older = json.loads(
        await app.registry.execute(
            MEMBER, "group_context_query", {"limit": 10, "before_seq": page["next_before_seq"]}
        )
    )
    assert [m["text"] for m in older["messages"]] == ["消息0", "消息1", "消息2"]
    assert older["next_before_seq"] is None
    found = json.loads(await app.registry.execute(MEMBER, "group_context_query", {"query": "其他"}))
    assert found["messages"] == []
    app.config.value.group_context.query_tool = False
    assert app.permissions.decision(MEMBER, tool) == "deny"
    assert "error" in json.loads(await app.registry.execute(MEMBER, "group_context_query", {}))


async def test_chat_mode_can_call_basic_tools_over_multiple_steps(app):
    app.config.value.group_context.enabled = True
    await app.store.record_group(SESSION, "m1", "4", "小明", "暗号是蓝鲸", False)
    seen = []

    class LLM:
        async def complete(self, route, system, messages, tools, settings, **kwargs):
            if messages[-1]["role"] == "tool":
                seen.append(messages[-1]["content"])
                return Reply(text="暗号是蓝鲸")
            return Reply(
                text="",
                calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "group_context_query", "arguments": "{}"},
                    }
                ],
            )

    app.runtime.llm = LLM()
    await app.runtime.submit(MEMBER, "暗号是什么")
    await eventually(lambda: app.store.list("outbox"))
    await asyncio.sleep(0)
    assert "蓝鲸" in seen[0]


def test_group_context_config_bounds():
    assert GroupContextConfig().enabled is False
    with pytest.raises(ValidationError):
        GroupContextConfig(inject_messages=-1)
    with pytest.raises(ValidationError):
        GroupContextConfig(inject_chars=10)
