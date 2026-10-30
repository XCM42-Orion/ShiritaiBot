import json

import pytest
from test_permissions import eventually

from shiritaibot.config import Settings
from shiritaibot.core import Context, Reply, now
from shiritaibot.memory import MemoryRecall


@pytest.fixture(autouse=True)
def allow_memory_tools(request):
    # Write tools are "normal" risk; these tests are about memory, not approvals.
    if "app" in request.fixturenames:
        from shiritaibot.config import SessionPolicy, ToolPolicy

        app = request.getfixturevalue("app")
        app.config.value.policy.owner_default = SessionPolicy(preset="full_access")
        app.config.value.policy.proactive_default = ToolPolicy(preset="full_access")


QUESTION = "帮我查一下上次梦寻提的Shiritaibot issue的处理情况"
EMAIL = "昨天梦寻发邮件说 ShiritaiBot 的 issue #3 已经处理完成了"


async def say(app, text, *, session="web:owner", turn="t1", role="user", background=False):
    """Persist a message in the turn, as the agent loop does before tools run."""
    await app.store.append(session, turn, {"role": role, "content": text})
    return Context(session, "owner", "agent", True, background=background, turn=turn)


async def call(app, ctx, tool, **args):
    return json.loads(await app.registry.execute(ctx, tool, args))


async def seed(app):
    intro = "梦寻就是赵紫芮，也叫 Yumehiro，某某大学的大二学生，在帮我做 ShiritaiBot"
    ctx = await say(app, intro + "。" + EMAIL)
    for kind, name, extra in [
        ("person", "赵紫芮", {"aliases": ["梦寻", "Yumehiro"]}),
        ("thing", "ShiritaiBot", {"summary": "主人正在开发的 Bot 项目"}),
    ]:
        result = await call(
            app, ctx, "memory_save", kind="entity", name=name, entity_kind=kind, **extra
        )
        assert "error" not in result, result
    for content in ["某某大学的大二学生", "ShiritaiBot 的协助开发"]:
        result = await call(
            app, ctx, "memory_save", kind="fact", subject="梦寻", content=content, evidence=intro
        )
        assert result["authority"] == "owner", result
    event = await call(
        app,
        ctx,
        "memory_save",
        kind="event",
        content="梦寻（赵紫芮）给主人发了一封邮件，说 ShiritaiBot 的 issue #3 已处理完成",
        time="2026-10-29",
        participants=["梦寻", "ShiritaiBot"],
        evidence=EMAIL,
    )
    assert "error" not in event, event
    return ctx


async def test_acceptance_example_injects_people_things_and_absolute_events(app):
    await seed(app)
    block = await MemoryRecall(app.memory).render(
        Context("web:owner", "owner", "agent", True, turn="t2"), QUESTION, 1500
    )
    lines = block.splitlines()
    person = next(line for line in lines if line.startswith("[人物]"))
    assert "赵紫芮（别名：梦寻、Yumehiro）" in person
    assert "某某大学的大二学生" in person and "（截至 " in person
    assert any(line.startswith("[事物] ShiritaiBot：主人正在开发的 Bot 项目") for line in lines)
    event = next(line for line in lines if line.startswith("[事件]"))
    assert event.startswith("[事件] 2026-10-29（周四） 梦寻（赵紫芮）给主人发了一封邮件")
    assert "〔主人告知〕" in event
    # Sections come in a fixed order: people, things, events.
    assert lines.index(person) < lines.index(event)
    assert "昨天" not in block


async def test_injection_is_gated_and_not_repeated_while_in_context(app):
    await seed(app)
    recall = MemoryRecall(app.memory)
    assert (
        await recall.render(
            Context("web:owner", "owner", "agent", True, turn="x"), "今天天气不错", 1500
        )
        == ""
    )
    first = await recall.render(await say(app, QUESTION, turn="t2"), QUESTION, 1500)
    assert "[人物]" in first
    # The same items are still in the model's context, so nothing is repeated.
    assert await recall.render(await say(app, QUESTION, turn="t3"), QUESTION, 1500) == ""
    await app.store.reset_history("web:owner")
    assert "[人物]" in await recall.render(await say(app, QUESTION, turn="t4"), QUESTION, 1500)


async def test_agent_turn_carries_memory_only_in_private_agent_sessions(app):
    await seed(app)

    class LLM:
        def __init__(self):
            self.inputs = []

        async def complete(self, route, system, messages, tools, **kwargs):
            self.inputs.append(messages[-1]["content"])
            return Reply(text="好的")

    llm = app.runtime.llm = LLM()
    await app.runtime.submit(Context("web:owner", "owner", "agent", True), QUESTION)
    await eventually(lambda: app.store.list("reply"))
    assert "<memory" in llm.inputs[0] and "issue #3" in llm.inputs[0]
    await app.runtime.submit(Context("group:qq:1", "owner", "chat", True, True), QUESTION)

    async def answered():
        return len(llm.inputs) == 2

    await eventually(answered)
    assert "<memory" not in llm.inputs[1]


async def test_writes_require_absolute_time_and_real_evidence(app):
    ctx = await say(app, "我下周三要去北京开会")
    await app.store.append(
        "web:owner", "t1", {"role": "assistant", "content": "好的，主人周三去北京"}
    )
    relative = await call(
        app,
        ctx,
        "memory_save",
        kind="event",
        content="主人下周三去北京开会",
        time="2026-11-04",
        phase="planned",
        evidence="下周三要去北京开会",
    )
    assert "相对时间" in relative["error"]
    vague = await call(
        app,
        ctx,
        "memory_save",
        kind="event",
        content="主人去北京开会",
        time="下周三",
        phase="planned",
        evidence="下周三要去北京开会",
    )
    assert "绝对时间" in vague["error"]
    own_words = await call(
        app,
        ctx,
        "memory_save",
        kind="fact",
        subject="主人",
        content="主人周三去北京",
        evidence="好的，主人周三去北京",
    )
    assert "evidence" in own_words["error"]
    future = await call(
        app,
        ctx,
        "memory_save",
        kind="event",
        content="主人去北京开会",
        time="2099-01-01",
        evidence="下周三要去北京开会",
    )
    assert "planned" in future["error"]
    planned = await call(
        app,
        ctx,
        "memory_save",
        kind="event",
        content="主人去北京开会",
        time="2026-11-04",
        phase="planned",
        evidence="下周三要去北京开会",
    )
    assert planned["text"].startswith("[事件] 计划于 2026-11-04（周三）")


async def test_owner_corrections_supersede_and_weaker_sources_raise_conflicts(app):
    ctx = await seed(app)
    first = (await app.memory.search(entity="梦寻", kind="fact"))["results"]
    grade = next(r["id"] for r in first if "大二" in r["text"])
    ctx = await say(app, "梦寻已经大三了", turn="t2")
    newer = await call(
        app,
        ctx,
        "memory_save",
        kind="fact",
        subject="e2",
        content="某某大学的大三学生",
        evidence="梦寻已经大三了",
        replaces=grade,
    )
    assert f"取代 {grade}" in newer["result"]
    detail = await call(app, ctx, "memory_get", id=grade)
    assert detail["status"] == "superseded" and detail["superseded_by"] == newer["id"]
    # A tool result cannot overrule the owner; the conflict goes back to the owner.
    tool = await say(app, "签名：赵紫芮，某某大学研一", turn="t3", role="tool", background=True)
    proactive = Context(
        "background:proactive:1", "owner", "agent", True, background=True, turn="t3"
    )
    await app.store.append(
        proactive.session, "t3", {"role": "tool", "content": "签名：赵紫芮，某某大学研一"}
    )
    clash = await call(
        app,
        proactive,
        "memory_save",
        kind="fact",
        subject="梦寻",
        content="某某大学的研一学生",
        evidence="某某大学研一",
        replaces=newer["id"],
    )
    assert "冲突" in clash["result"]
    conflict = clash["result"].split("（")[1].split("）")[0]
    block = await MemoryRecall(app.memory).render(
        await say(app, "梦寻现在读几年级", turn="t4"), "梦寻现在读几年级", 1500
    )
    assert f"[待确认] #{conflict}" in block and "memory_resolve" in block
    listed = await app.memory.browse("conflict")
    assert listed[0]["id"] == conflict and listed[0]["status"] == "open"
    again = await MemoryRecall(app.memory).render(
        await say(app, "梦寻在哪读书", session="web:x", turn="t5"), "梦寻在哪读书", 1500
    )
    assert "不要追问" in again
    answer = await say(app, "她是大三，签名是旧的", turn="t6")
    resolved = await call(
        app,
        answer,
        "memory_resolve",
        conflict=conflict,
        decision=f"keep:{newer['id']}",
        owner_quote="她是大三",
    )
    assert resolved["result"] == "已处理"
    assert (await call(app, answer, "memory_get", id=clash["id"]))["status"] == "superseded"
    assert tool  # The tool turn was only used to place evidence.


async def test_entities_resolve_ambiguity_and_merge(app):
    ctx = await say(app, "小李是我的室友李明，另一个小李是同事李华")
    await call(
        app, ctx, "memory_save", kind="entity", name="李明", entity_kind="person", aliases=["小李"]
    )
    second = await call(
        app, ctx, "memory_save", kind="entity", name="李华", entity_kind="person", aliases=["小李"]
    )
    assert "小李" in second["note"]
    ambiguous = await call(
        app,
        ctx,
        "memory_save",
        kind="fact",
        subject="小李",
        content="主人的室友",
        evidence="小李是我的室友李明",
    )
    assert "多个实体" in ambiguous["error"]
    merged = await call(app, ctx, "memory_save", kind="entity", name="李明", merge=["李华"])
    assert merged["merged"] == ["e3"]
    assert (await call(app, ctx, "memory_get", id="e2"))["aliases"] == ["小李", "李华"]


async def test_proactive_event_text_is_derived_evidence(app):
    loop = Context("background:proactive:e1", "owner", "agent", True, background=True, turn="p1")
    await app.store.append(
        loop.session, "p1", {"role": "user", "content": "新邮件：issue #4 已关闭"}
    )
    saved = await call(
        app,
        loop,
        "memory_save",
        kind="event",
        content="issue #4 已关闭",
        time="2026-10-30T10:00",
        evidence="issue #4 已关闭",
    )
    assert saved["authority"] == "derived" and "〔外部事件〕" in saved["text"]


async def test_forget_leaves_a_tombstone_against_relearning(app):
    ctx = await say(app, "记住我喜欢喝冰美式")
    saved = await call(
        app,
        ctx,
        "memory_save",
        kind="fact",
        subject="主人",
        content="主人喜欢喝冰美式",
        evidence="我喜欢喝冰美式",
    )
    await call(app, ctx, "memory_forget", id=saved["id"], reason="主人说记错了")
    assert (await app.memory.search("冰美式"))["results"] == []
    tool = Context("background:proactive:2", "owner", "agent", True, background=True, turn="t9")
    await app.store.append(tool.session, "t9", {"role": "tool", "content": "订单：冰美式"})
    relearn = await call(
        app,
        tool,
        "memory_save",
        kind="fact",
        subject="主人",
        content="主人喜欢喝冰美式",
        evidence="冰美式",
    )
    assert "忘掉" in relearn["error"]


async def test_legacy_memories_migrate_once(tmp_path):
    from shiritaibot.app import Application

    first = Application(tmp_path)
    await first.start()
    await first.store.execute(
        "INSERT INTO memory VALUES('old','private','knowledge','讲数学先举例','active',?)",
        (now().timestamp(),),
    )
    await first.store.delete("memory_migration", "legacy")
    await first.close()
    for _ in range(2):
        app = Application(tmp_path)
        await app.start()
        rows = await app.store.query("SELECT content,authority FROM mem_fact")
        await app.close()
        assert rows == [{"content": "讲数学先举例", "authority": "owner"}]


async def test_embeddings_add_a_semantic_route(app):
    from shiritaibot.config import ModelConfig, ProviderConfig

    cfg = app.config.value
    cfg.providers.append(ProviderConfig(id="emb", base_url="https://emb.test/v1", api_key="k"))
    cfg.models.append(ModelConfig(id="vec", provider="emb", model="v1", kind="embedding"))
    cfg.routes["embedding"] = ["vec"]
    calls = []

    async def embed(texts, *, query=False, settings=None, route="embedding"):
        calls.append(query)
        # A toy space: anything about coffee points one way, everything else another.
        return "emb:v1", [[1.0, 0.0] if "咖啡" in t or "美式" in t else [0.0, 1.0] for t in texts]

    app.llm.embed = embed
    ctx = await say(app, "我喜欢喝冰美式")
    await call(
        app,
        ctx,
        "memory_save",
        kind="fact",
        subject="主人",
        content="主人喜欢喝冰美式",
        evidence="我喜欢喝冰美式",
    )
    await app.memory.vectors.sync(await app.memory.documents())
    assert (await app.memory.vectors.status(await app.memory.documents()))["pending"] == 0
    found = await app.memory.vectors.search("来杯咖啡吧")
    assert found[0][1] > 0.99 and True in calls
    block = await MemoryRecall(app.memory).render(
        await say(app, "来杯咖啡吧", turn="t2"), "来杯咖啡吧", 1500
    )
    assert "冰美式" in block


def test_route_rules_for_embedding_and_decision_models():
    providers = [
        {"id": "oa", "base_url": "https://api.test/v1"},
        {"id": "an", "protocol": "anthropic", "base_url": "https://an.test"},
        {"id": "ts", "protocol": "systemone", "base_url": "https://jev.test/v1"},
    ]
    models = [
        {"id": "text", "provider": "oa", "model": "m"},
        {"id": "vec", "provider": "oa", "model": "e", "kind": "embedding"},
        {"id": "jev", "provider": "ts", "model": "jev-latest"},
    ]
    ok = Settings(
        providers=providers, models=models, routes={"memory": ["jev", "text"], "embedding": ["vec"]}
    )
    assert ok.routes["memory"] == ["jev", "text"]
    for routes in [{"agent": ["vec"]}, {"embedding": ["text"]}, {"agent": ["jev"]}]:
        with pytest.raises(ValueError):
            Settings(providers=providers, models=models, routes=routes)
    with pytest.raises(ValueError, match="OpenAI-compatible"):
        Settings(
            providers=providers,
            models=[{"id": "v", "provider": "an", "model": "e", "kind": "embedding"}],
        )
