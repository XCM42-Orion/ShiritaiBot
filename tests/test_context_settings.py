import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from test_permissions import OWNER, eventually

from shiritaibot.adapters.onebot11 import OneBot
from shiritaibot.agent.context import ContextManager
from shiritaibot.config import (
    AdapterConfig,
    ConfigService,
    ContextConfig,
    ModelConfig,
    PersonaConfig,
    ProviderConfig,
    Settings,
)
from shiritaibot.core import Context, Reply, canonical


async def seed(store, session, turns=4):
    for turn in range(turns):
        await store.append(session, f"turn-{turn}", {"role": "user", "content": f"问题{turn}"})
        await store.append(
            session,
            f"turn-{turn}",
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"call-{turn}"}],
            },
        )
        await store.append(
            session,
            f"turn-{turn}",
            {
                "role": "tool",
                "tool_call_id": f"call-{turn}",
                "content": f"结果{turn}",
            },
        )
        await store.append(session, f"turn-{turn}", {"role": "assistant", "content": f"答复{turn}"})


async def test_settings_round_trip_and_legacy_persona(tmp_path):
    legacy = Settings(persona="旧人设")
    assert "旧人设" in legacy.system_prompt()
    assert not legacy.private_require_keyword
    cfg = Settings(
        personas=[PersonaConfig(id="kind", name="温柔", prompt="隐藏的人格提示词")],
        default_persona="kind",
        assistant_name="小知",
        owner_name="老师",
        wake_keywords=[" 小知 ", "小知"],
    )
    service = ConfigService(tmp_path)
    await service.save(cfg)
    await service.load()
    assert service.value == cfg
    assert cfg.wake_keywords == ["小知"]
    assert all(
        word in cfg.system_prompt(owner=True) for word in ["隐藏的人格提示词", "小知", "老师"]
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"default_persona": "missing"},
        {"context": {"max_turns": 2, "keep_recent_turns": 2}},
        {"context": {"compression_model": "missing"}},
        {"wake_keywords": [" "]},
        {"private_require_keyword": True},
        {"assistant_name": " "},
        {"personas": [{"id": "default", "name": "默认", "prompt": "secret"}]},
        {"policy": {"debounce_seconds": 2, "debounce_max_seconds": 1}},
    ],
)
def test_invalid_settings_are_rejected(patch):
    with pytest.raises(ValidationError):
        Settings.model_validate(patch)


async def test_persona_commands_are_private_to_prompt_and_scoped_to_session(app):
    app.config.value.personas = [PersonaConfig(id="kind", name="温柔", prompt="隐藏人格")]
    app.config.value.assistant_name = "小知"
    app.config.value.owner_name = "老师"
    for command in ["/personas", "/persona kind", "/persona", "/status"]:
        await app.runtime.submit(OWNER, command)
        assert "隐藏人格" not in (await app.store.list("reply"))[0]["text"]
    stranger = Context("private:a:2", "2", "chat")
    await app.runtime.submit(stranger, "/persona kind")
    assert not await app.store.get("session_persona", stranger.session)
    seen = asyncio.Event()

    class Model:
        async def complete(self, route, system, messages, tools, **kwargs):
            assert all(word in system for word in ["隐藏人格", "小知", "老师"])
            assert "/persona" not in str(messages)
            seen.set()
            return Reply(text="好")

    app.runtime.llm = Model()
    await app.runtime.submit(OWNER, "你好")
    await asyncio.wait_for(seen.wait(), 3)
    await app.runtime.submit(OWNER, "/persona default")
    assert not (await app.store.get("session_persona", OWNER.session))["persona"]


async def test_discard_whole_turns_and_keep_original_archive(app):
    await seed(app.store, OWNER.session)
    cfg = Settings(context=ContextConfig(max_turns=3, keep_recent_turns=1))
    model = SimpleNamespace(complete=AsyncMock())
    messages = await ContextManager(app.store, model).prepare(
        OWNER.session,
        cfg,
        {"role": "user", "content": "新问题"},
        owner=True,
    )
    assert [m["content"] for m in messages if m["role"] == "user"] == ["问题2", "问题3"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"] * 2
    model.complete.assert_not_awaited()
    assert len(await app.store.history(OWNER.session)) == 16
    # A restarted manager uses the persisted projection, not the discarded messages.
    again = await ContextManager(app.store, model).prepare(
        OWNER.session,
        cfg,
        {"role": "user", "content": "新问题"},
        owner=True,
    )
    assert again == messages


async def test_compression_model_recent_turns_restart_and_reset(app):
    await seed(app.store, OWNER.session)
    cfg = Settings(
        providers=[ProviderConfig(id="p")],
        models=[ModelConfig(id="summary", provider="p", model="summary-model")],
        assistant_name="小知",
        owner_name="老师",
        context=ContextConfig(
            strategy="compress", max_turns=3, keep_recent_turns=1, compression_model="summary"
        ),
    )
    model = SimpleNamespace(complete=AsyncMock(return_value=Reply(text="旧对话摘要")))
    manager = ContextManager(app.store, model)
    incoming = {"role": "user", "content": "新问题"}
    messages = await manager.prepare(OWNER.session, cfg, incoming, owner=True)
    assert "旧对话摘要" in messages[0]["content"]
    assert messages[1]["content"] == "问题3"
    assert len(messages) == 5
    call = model.complete.call_args
    assert call.kwargs["settings"].routes["light"] == ["summary"]
    assert "小知" in call.args[1] and "老师" in call.args[1]
    assert "问题2" in call.args[2][0]["content"] and "问题3" not in call.args[2][0]["content"]
    model.complete.reset_mock()
    assert (
        await ContextManager(app.store, model).prepare(OWNER.session, cfg, incoming, owner=True)
        == messages
    )
    model.complete.assert_not_awaited()
    await app.store.reset_history(OWNER.session)
    assert await manager.prepare(OWNER.session, cfg, incoming, owner=True) == []
    assert (
        len(await app.store.query("SELECT seq FROM messages WHERE session=?", (OWNER.session,)))
        == 16
    )


async def test_failed_compression_keeps_checkpoint_and_original_input(app):
    await seed(app.store, OWNER.session)
    app.config.value.context = ContextConfig(strategy="compress", max_turns=3, keep_recent_turns=1)
    app.runtime.llm = SimpleNamespace(complete=AsyncMock(return_value=Reply(text="")))
    await app.runtime.submit(OWNER, "不能丢失的新问题")
    replies = await eventually(lambda: app.store.list("reply"))
    assert "压缩失败" in replies[0]["text"]
    assert not await app.store.get("context_state", OWNER.session)
    assert "不能丢失的新问题" in str(await app.store.history(OWNER.session))


async def test_character_limit_compression_is_bounded_and_keeps_tool_pairs(app):
    await app.store.append(
        OWNER.session, "long", {"role": "user", "content": "x" * 50000 + "尾部事实"}
    )
    cfg = Settings(context=ContextConfig(strategy="compress"))
    cfg.policy.max_context_chars = 4000
    model = SimpleNamespace(complete=AsyncMock(return_value=Reply(text="摘要")))
    messages = await ContextManager(app.store, model).prepare(
        OWNER.session,
        cfg,
        {"role": "user", "content": "新"},
        owner=True,
    )
    assert "摘要" in messages[0]["content"]
    assert all(len(call.args[2][0]["content"]) <= 16000 for call in model.complete.call_args_list)
    assert any("尾部事实" in call.args[2][0]["content"] for call in model.complete.call_args_list)
    assert len(canonical(messages)) < 4000


async def test_history_tool_search_pagination_long_message_and_isolation(app):
    await seed(app.store, OWNER.session)
    secret = Context("private:a:other", "other", "agent", True)
    await app.store.append(secret.session, "secret", {"role": "user", "content": "不该跨会话"})
    await app.store.append(OWNER.session, "long", {"role": "user", "content": "x" * 5000 + "尾部"})
    await app.store.reset_history(OWNER.session)

    async def query(**args):
        return json.loads(await app.registry.execute(OWNER, "history_query", args))

    result = await query(query="问题", limit=2)
    assert [m["content"] for m in result["messages"]] == ["问题2", "问题3"]
    page = await query(query="问题", limit=2, before_seq=result["next_before_seq"])
    assert [m["content"] for m in page["messages"]] == ["问题0", "问题1"]
    assert not (await query(query="不该跨会话"))["messages"]
    long = (await query(query="尾部"))["messages"][0]
    assert long["next_offset"] == 4000
    tail = (await query(message_seq=long["seq"], offset=4000))["messages"][0]
    assert tail["content"].endswith("尾部") and tail["next_offset"] is None
    denied = await app.registry.execute(
        Context("group:a:1", "owner", "chat", True, True), "history_query", {}
    )
    assert "error" in json.loads(denied)
    app.config.value.policy.denied_tools = ["history_query"]
    assert "error" in await app.registry.execute(OWNER, "history_query", {})


async def test_keyword_activation_and_private_controls(app):
    cfg = AdapterConfig(id="qq", owner_id="1", self_id="100", groups=["9"], friends=["2"])
    bot = OneBot(cfg, app.vault, app.store, app.adapters.receive)
    bot.self_id = "100"
    submit = AsyncMock()
    app.adapters.runtime = SimpleNamespace(submit=submit)
    event_id = 0

    async def receive(text, *, group=False, mention=False, user=1, target=9):
        nonlocal event_id
        event_id += 1
        await app.adapters.receive(
            bot,
            {
                "post_type": "message",
                "message_type": "group" if group else "private",
                "user_id": user,
                "group_id": target,
                "message_id": event_id,
                "message": [{"type": "text", "data": {"text": text}}]
                + ([{"type": "at", "data": {"qq": "100"}}] if mention else []),
            },
        )

    await receive("你好")
    assert submit.await_count == 1
    await receive("你好", group=True)
    assert submit.await_count == 1
    await receive("你好", group=True, mention=True)
    assert submit.await_count == 2
    app.config.value.wake_keywords = ["小知"]
    app.config.value.private_require_keyword = True
    await receive("你好")
    assert submit.await_count == 2
    for text in ["/help", "/stop", "小知，/persona kind"]:
        await receive(text)
    assert submit.await_count == 5 and submit.call_args.args[1] == "/persona kind"
    await receive("小知你好", group=True)
    assert submit.await_count == 6 and submit.call_args.args[0].mode == "chat"
    await receive("小知你好", group=True, target=999)
    await receive("小知你好", user=999)
    assert submit.await_count == 6


async def test_context_has_no_implicit_200_message_limit_and_steering_is_one_turn(app):
    await seed(app.store, OWNER.session, turns=60)
    await app.store.append(
        OWNER.session,
        "turn-59",
        {
            "role": "user",
            "content": "同一轮插话",
            "_original_content": "原文" * 10000,
        },
    )
    cfg = Settings(context=ContextConfig(max_turns=100, keep_recent_turns=10))
    messages = await ContextManager(app.store, None).prepare(
        OWNER.session,
        cfg,
        {"role": "user", "content": "新"},
        owner=True,
    )
    assert len(messages) == 241
    assert messages[0]["content"] == "问题0"
    assert not await app.store.get("context_state", OWNER.session)


async def test_memory_summary_uses_turn_names_after_configuration_changes(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    app.config.value.assistant_name = "新称呼"
    app.config.value.owner_name = "新主人称呼"
    await app.store.put(
        "turn",
        "named-turn",
        {
            "assistant_name": "小知",
            "owner_name": "老师",
        },
        OWNER.session,
    )
    await app.store.append(OWNER.session, "named-turn", {"role": "assistant", "content": "记住了"})
    ctx = Context(OWNER.session, OWNER.actor, "agent", True, turn="named-turn")
    await app.short_term.enqueue(ctx, "completed")
    app.short_term.llm = SimpleNamespace(
        complete=AsyncMock(return_value=Reply(text="小知记住了请求"))
    )
    await app.short_term.tick()
    call = app.short_term.llm.complete.call_args
    assert "小知" in call.args[1] and "老师" in call.args[1]
    assert '"speaker":"小知"' in call.args[2][0]["content"]
    assert (await app.short_term.recent())[0]["assistant_name"] == "小知"
