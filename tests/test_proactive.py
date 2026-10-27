import asyncio
import json

import pytest
from test_permissions import OWNER, eventually

from shiritaibot.core import Context, ExternalEvent, Reply
from shiritaibot.proactive import Decision, grade

MAIL = ExternalEvent(
    source="email",
    kind="email.received",
    dedup_key="<hw3@x>",
    title="新邮件：作业截止提醒",
    payload={"from": "teacher@example.edu", "snippet": "数据结构作业 3 周五 23:59 截止"},
)


class Judge:
    """Fake light-route model returning a fixed triage decision."""

    def __init__(self, **decision):
        self.decision, self.calls = decision, 0

    async def complete(self, route, system, messages, tools, **kwargs):
        assert route == "light"
        self.calls += 1
        return Reply(text=json.dumps(self.decision, ensure_ascii=False))


@pytest.fixture
async def loop(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    app.config.value.proactive.enabled = True
    app.config.value.proactive.quiet_start = app.config.value.proactive.quiet_end = "00:00"
    return app


async def stages(app, key):
    return [step["stage"] for step in (await app.store.get("short_term", key))["steps"]]


@pytest.mark.parametrize(
    "decision,urgency,prepare,expected",
    [
        ("ignore", "normal", "", "drop"),
        ("log", "high", "查日程", "record"),
        ("digest", "low", "", "notify"),
        ("notify", "normal", "", "notify"),
        ("notify", "high", "查日程", "prepare"),
        ("notify", "low", "查日程", "notify"),
        ("notify", "critical", "查日程", "notify"),
    ],
)
def test_grade_separates_judgement_from_action(decision, urgency, prepare, expected):
    judged = Decision(decision=decision, urgency=urgency, prepare=prepare, message="m", reason="r")
    assert grade(judged) == expected


async def test_mail_event_judged_notified_and_recallable_from_main_session(loop):
    loop.proactive.llm = Judge(decision="notify", message="作业 3 周五截止", reason="有截止时间")
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    assert "作业 3 周五截止" in (await loop.store.list("reply"))[0]["text"]
    assert await stages(loop, key) == ["received", "judged", "queued", "notified"]

    recalled = json.loads(await loop.registry.execute(OWNER, "proactive_recall", {"query": "作业"}))
    assert recalled[0]["id"] == key
    assert recalled[0]["stage"] == "notified" and recalled[0]["action"] == "notify"
    assert recalled[0]["source"] == "email" and recalled[0]["title"] == MAIL.title
    # Short-term memory belongs to the owner's main session, never to chats.
    group = Context("group:qq:1", "owner", "agent", True, True)
    assert "error" in json.loads(await loop.registry.execute(group, "proactive_recall", {}))


@pytest.mark.parametrize("decision,stage", [("ignore", "ignored"), ("log", "logged")])
async def test_low_value_events_end_in_memory_without_notifying(loop, decision, stage):
    loop.proactive.llm = Judge(decision=decision, message="广告", reason="推广邮件")
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    await loop.proactive.tick()
    assert await loop.store.list("notification") == []
    assert await loop.store.list("reply") == []
    row = await loop.store.get("short_term", key)
    assert row["stage"] == stage and "推广邮件" in row["steps"][1]["detail"]


async def test_groundwork_runs_in_background_session_before_notifying(loop):
    loop.proactive.llm = Judge(
        decision="notify",
        urgency="high",
        message="作业 3 周五截止",
        reason="重要截止",
        prepare="查看周五前的日程空档",
    )
    prompts = []

    class Agent:
        async def complete(self, route, system, messages, tools, **kwargs):
            prompts.append((route, messages[-1]["content"], [t["function"]["name"] for t in tools]))
            return Reply(text="周四晚上有空，可以安排写作业")

    loop.runtime.llm = Agent()
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    assert (await loop.store.get("event", key))["status"] == "preparing"
    assert await loop.store.list("reply") == []

    async def prepared():
        return (await loop.store.get("short_term", key))["stage"] == "prepared"

    await eventually(prepared)
    route, prompt, tools = prompts[0]
    assert route == "agent" and "查看周五前的日程空档" in prompt and "<event>" in prompt
    # Background groundwork only sees read-only tools.
    assert "proactive_recall" in tools and "shell" not in tools and "memory_write" not in tools

    await loop.proactive.tick()
    text = [r for r in await loop.store.list("reply") if r["scope"] == "web:owner"][0]["text"]
    assert "作业 3 周五截止" in text and "【先期准备】" in text and "周四晚上有空" in text
    assert await stages(loop, key) == [
        "received",
        "judged",
        "preparing",
        "prepared",
        "notified",
    ]


async def test_lost_groundwork_still_notifies(loop):
    loop.proactive.llm = Judge(
        decision="notify", urgency="normal", message="请回复老师", reason="需回复", prepare="查记录"
    )

    class Silent:
        async def submit(self, ctx, text):
            return "queued"  # e.g. the process restarted before the turn ran

    loop.proactive.runtime = Silent()
    key = await loop.proactive.emit(MAIL)
    await loop.proactive.tick()
    row = await loop.store.get("event", key)
    row["prepare_started"] = 0
    await loop.store.put("event", key, row)
    await loop.proactive.tick()
    assert "请回复老师" in (await loop.store.list("reply"))[0]["text"]
    assert "先期准备未完成" in (await loop.store.list("reply"))[0]["text"]
    assert (await stages(loop, key))[-2:] == ["prepare_timeout", "notified"]


async def test_emit_wakes_the_running_loop(app):
    # The idle fallback is 10 s; a new event must be handled well before that.
    key = await app.proactive.emit(MAIL)

    async def handled():
        row = await app.store.get("event", key)
        return row["status"] == "processed"

    await eventually(handled)
    assert (await app.store.get("short_term", key))["stage"] == "logged"
