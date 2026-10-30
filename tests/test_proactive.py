import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from test_permissions import OWNER, eventually

from shiritaibot.agent import repair_history
from shiritaibot.config import PersonaConfig
from shiritaibot.core import Context, ExternalEvent, Reply, now
from shiritaibot.proactive import PREPARE_SESSION, Decision, grade, parse_loop_result
from shiritaibot.web import create_app

MAIL = ExternalEvent(
    source="email",
    kind="email.received",
    dedup_key="hw3",
    title="作业截止提醒",
    payload={"deadline": "周五"},
)


class Judge:
    def __init__(self, **decision):
        self.decision, self.calls, self.systems = decision, 0, []

    async def complete(self, route, system, messages, tools, **kwargs):
        assert route == "light" and tools == []
        self.calls += 1
        self.systems.append(system)
        return Reply(text=json.dumps(self.decision, ensure_ascii=False))

    async def decide(self, *args, **kwargs):
        return None


class Agent:
    def __init__(self, notify=True):
        self.notify, self.inputs, self.systems = notify, [], []

    async def complete(self, route, system, messages, tools, **kwargs):
        assert route == "agent"
        self.inputs.append((list(messages), tools))
        self.systems.append(system)
        if not any(m["role"] == "tool" for m in messages):
            return Reply(
                text="我先查一下日程。",
                calls=[
                    {
                        "id": "check",
                        "type": "function",
                        "function": {"name": "routine_list", "arguments": "{}"},
                    }
                ],
            )
        return Reply(
            text=json.dumps(
                {
                    "notify": self.notify,
                    "message": "作业周五截止，已查过日程。",
                    "reason": "核查完成",
                },
                ensure_ascii=False,
            )
        )


class Summarizer:
    def __init__(self):
        self.inputs = []

    async def complete(self, route, system, messages, tools, **kwargs):
        assert route == "light" and not tools
        self.inputs.append(messages)
        return Reply(text="收到作业提醒，核查日程后结束本轮。")


@pytest.fixture
async def loop(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    app.config.value.proactive.enabled = True
    app.config.value.proactive.quiet_start = app.config.value.proactive.quiet_end = "00:00"
    app.proactive.llm = Judge(
        importance=3, decision="notify", message="分类摘要", reason="截止时间"
    )
    app.runtime.llm = Agent()
    app.short_term.llm = Summarizer()
    return app


async def finished(app, key):
    async def check():
        row = await app.proactive.get_event(key)
        return row if row["status"] in {"processed", "discarded"} else None

    return await eventually(check)


@pytest.mark.parametrize("importance", range(5))
def test_importance_controls_admission(importance):
    assert grade(Decision(importance=importance, decision="ignore", reason="r")) == (
        "drop" if importance == 0 else "run"
    )


async def test_tool_loop_final_message_handoff_and_model_summary(loop):
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    await finished(loop, key)
    assert await loop.store.list("reply") == []  # Loop completion does not send directly.
    await loop.proactive.tick()
    reply = (await loop.store.list("reply"))[0]["text"]
    assert reply == "作业周五截止，已查过日程。" and "分类摘要" not in reply
    history = await loop.store.history("web:owner")
    assert history == [{"role": "assistant", "content": reply, "_proactive": True}]
    repaired = repair_history(history, 10000)
    assert repaired[0]["role"] == "user" and repaired[1:] == history
    tools = [t["function"]["name"] for t in loop.runtime.llm.inputs[0][1]]
    assert {"routine_list", "shell", "memory_save"} <= set(tools)
    snapshot = (await loop.store.list("session"))[0]["tool_policy"]
    assert snapshot["preset"] == "safe_manual"

    await eventually(lambda: loop.store.list("memory_summary"))
    await loop.short_term.tick()
    recalled = json.loads(await loop.registry.execute(OWNER, "proactive_recall", {"query": "作业"}))
    assert recalled[0]["loop"] == "proactive" and recalled[0]["summary"]
    transcript = loop.short_term.llm.inputs[0][0]["content"]
    assert (
        "routine_list" in transcript and '"role":"tool"' in transcript and "核查完成" in transcript
    )
    group = Context("group:qq:1", "owner", "agent", True, True)
    assert "error" in json.loads(await loop.registry.execute(group, "proactive_recall", {}))


async def test_spam_is_discarded_before_agent_and_keeps_dedup(loop):
    loop.proactive.llm = Judge(importance=0, decision="notify", reason="广告")
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    assert (await loop.proactive.get_event(key))["status"] == "discarded"
    assert not loop.runtime.llm.inputs
    assert not await loop.store.list("notification")
    assert not await loop.store.list("memory_summary")
    assert not await loop.store.list("short_term")
    assert await loop.proactive.emit(MAIL) == key


@pytest.mark.parametrize("named_persona", [False, True])
async def test_filter_omits_persona_and_proactive_loop_uses_it(loop, named_persona):
    prompt = "你是小知，语气温柔，称呼主人为老师。"
    if named_persona:
        loop.config.value.personas = [PersonaConfig(id="kind", name="温柔", prompt=prompt)]
        loop.config.value.default_persona = "kind"
    else:
        loop.config.value.persona = prompt
    loop.config.value.assistant_name = "小知"
    loop.config.value.owner_name = "老师"
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    await finished(loop, key)
    assert all(prompt not in system for system in loop.proactive.llm.systems)
    assert all(prompt in system for system in loop.runtime.llm.systems)
    assert all("老师" in system and "小知" in system for system in loop.runtime.llm.systems)


@pytest.mark.parametrize("importance", [1, 2, 3])
async def test_noncritical_events_can_end_silently_but_are_compressed(loop, importance):
    loop.proactive.llm = Judge(importance=importance, decision="notify", reason="待核查")
    loop.runtime.llm = Agent(notify=False)
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    assert not (await finished(loop, key))["notification_queued"]
    await eventually(lambda: loop.store.list("memory_summary"))
    await loop.short_term.tick()
    assert len(await loop.short_term.recent()) == 1
    assert not await loop.store.list("reply")
    assert not await loop.store.history("web:owner")


@pytest.mark.parametrize("suggestion", ["ignore", "log", "digest"])
async def test_highest_priority_overrides_silence_quiet_digest_and_throttle(loop, suggestion):
    loop.proactive.llm = Judge(importance=4, decision=suggestion, reason="必须通知")
    loop.runtime.llm = Agent(notify=False)
    loop.config.value.proactive.quiet_start = "00:00"
    loop.config.value.proactive.quiet_end = "23:59"
    await loop.store.put("notify_last", "email", {"at": now().timestamp()})
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    await finished(loop, key)
    await loop.proactive.tick()
    assert (await loop.store.get("notification", key))["status"] == "sent"
    assert (await loop.store.list("reply"))[0]["text"] == "作业周五截止，已查过日程。"


async def test_classification_failure_still_enters_loop(loop):
    judge = Judge(bad="invalid schema")
    loop.proactive.llm = judge
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    row = await finished(loop, key)
    assert judge.calls == 2 and row["decision"]["importance"] == 2
    assert len(loop.runtime.llm.inputs) == 2


async def test_priority_order_and_timeout_fallback_survives_late_result(loop):
    submitted = []

    class Silent:
        async def submit(self, ctx, text):
            submitted.append(ctx.session)
            return "queued"

    class Classifier:
        async def decide(self, *args, **kwargs):
            return None

        async def complete(self, route, system, messages, tools, **kwargs):
            event = json.loads(messages[0]["content"])
            return Reply(
                text=json.dumps(
                    {
                        "importance": event["payload"]["level"],
                        "decision": "ignore",
                        "reason": "level",
                    }
                )
            )

    loop.proactive.runtime, loop.proactive.llm = Silent(), Classifier()
    keys = await loop.proactive.emit_many(
        [MAIL.model_copy(update={"dedup_key": str(i), "payload": {"level": i}}) for i in [1, 4, 3]]
    )
    await loop.proactive.tick()
    assert submitted == [PREPARE_SESSION + keys[i] for i in [1, 2, 0]]
    row = await loop.proactive.get_event(keys[1])
    row["loop_started"] = 0
    await loop.store.put("event", row["id"], row)
    await loop.proactive.tick()
    assert "主动处理未完成" in (await loop.store.list("reply"))[0]["text"]
    await loop.proactive.prepared(
        PREPARE_SESSION + keys[1], json.dumps({"notify": True, "message": "late", "reason": "late"})
    )
    await loop.proactive.tick()
    assert len(await loop.store.list("reply")) == 1


async def test_concurrent_ticks_and_emits_do_not_start_duplicate_turns(loop):
    keys = await asyncio.gather(*(loop.proactive.emit(MAIL) for _ in range(4)))
    assert len(set(keys)) == 1
    await asyncio.gather(loop.proactive.tick(), loop.proactive.tick())
    await finished(loop, keys[0])
    await loop.proactive.tick()
    assert len(await loop.store.list("turn")) == 1
    assert len(await loop.store.list("reply")) == 1


async def test_emit_wakes_running_loop(app):
    key = await app.proactive.emit(MAIL)
    assert (await finished(app, key))["action"] == "record"


def test_http_ingress_batch_dedup_status_and_validation(tmp_path):
    with TestClient(create_app(tmp_path, token="test-token")) as client:
        assert client.post("/api/events/batch", json=[]).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        event = MAIL.model_dump(mode="json")
        event["source"] = " email "
        key = client.post("/api/events", json=event).json()["id"]
        assert client.post("/api/events/batch", json=[event, event]).json() == {"ids": [key, key]}
        stored = client.get(f"/api/events/{key}").json()["event"]
        assert stored["id"] == MAIL.id and stored["source"] == "email"
        assert client.get("/api/events/absent").status_code == 404
        assert client.post("/api/events", json={**event, "source": ""}).status_code == 422
        assert client.post("/api/events", json={**event, "source": "   "}).status_code == 422
        assert (
            client.post(
                "/api/events", json={**event, "occurred_at": "2026-10-28T12:00:00"}
            ).status_code
            == 422
        )


@pytest.mark.parametrize(
    "text",
    [
        '{"notify":true,"message":"第一行\n第二行","reason":"r"}',
        '核查完成。\n\n{"notify": true, "message": "第一行\\n第二行", "reason": "r"}',
        '{"additionalProperties":false,"message":"第一行\n第二行","notify":true,"reason":"r"}',
        '{"notify":true,"message":"存进你"户口"了\n第二行","reason":"r"}',
    ],
)
def test_parse_loop_result_tolerates_near_json(text):
    result = parse_loop_result(text)
    assert (
        result.notify and result.message.startswith(("第一行", "存进你")) and "\n" in result.message
    )


def test_parse_loop_result_defaults_reason_and_rejects_prose():
    assert parse_loop_result('{"notify":false,"message":"m"}').reason
    with pytest.raises(ValueError):
        parse_loop_result("只有分析文字")


async def test_quiet_hours_deferral_is_logged_once(loop, monkeypatch):
    monkeypatch.setattr("shiritaibot.proactive.quiet_hours", lambda *args: True)
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    await finished(loop, key)
    await loop.proactive.tick()
    await loop.proactive.tick()
    assert (await loop.store.get("notification", key))["status"] == "pending"
    deferred = [r for r in await loop.store.list("activity") if r["kind"] == "notify.deferred"]
    assert len(deferred) == 1
    steps = (await loop.store.get("proactive_trace", key))["steps"]
    assert [s["stage"] for s in steps].count("deferred") == 1


def test_judge_reply_tolerates_code_fence():
    from shiritaibot.proactive import first_object

    assert first_object('```json\n{"importance": 3, "reason": "r"}\n```')["importance"] == 3


class Reporter(Agent):
    async def complete(self, route, system, messages, tools, **kwargs):
        self.inputs.append((list(messages), tools))
        self.systems.append(system)
        return Reply(
            text="",
            calls=[
                {
                    "id": "done",
                    "type": "function",
                    "function": {
                        "name": "proactive_report",
                        "arguments": json.dumps(
                            {"notify": True, "message": "作业周五截止哦", "reason": "有截止"},
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        )


async def test_proactive_prompt_is_prose_and_result_comes_from_report_tool(loop):
    loop.runtime.llm = Reporter()
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    row = await finished(loop, key)
    assert row["result"]["message"] == "作业周五截止哦" and row["notification_queued"]
    messages, tools = loop.runtime.llm.inputs[0]
    prompt = messages[-1]["content"].split("\n<context")[0]
    assert "{" not in prompt and "作业截止提醒" in prompt and "deadline: 周五" in prompt
    assert "重要性 3/4" in prompt
    assert any(t["function"]["name"] == "proactive_report" for t in tools)
    assert len(loop.runtime.llm.inputs) == 1
