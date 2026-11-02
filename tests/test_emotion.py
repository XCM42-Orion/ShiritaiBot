import pytest
from pydantic import ValidationError
from test_permissions import eventually

from shiritaibot.config import ConfigService, EmotionConfig, ModelConfig, ProviderConfig, Settings
from shiritaibot.core import Context, Reply
from shiritaibot.emotion import EmotionManager
from shiritaibot.emotion.judge import LABELS, EmotionJudge
from shiritaibot.emotion.prompt import render
from shiritaibot.emotion.rules import Screen, screen
from shiritaibot.emotion.state import combine, decay, merge, read_key, scope_keys, threshold


def test_emotion_defaults_off():
    cfg = Settings.model_validate({})
    assert cfg.emotion.enabled is False
    assert cfg.emotion.developer is False
    assert (cfg.emotion.sensitivity, cfg.emotion.tone) == (0.5, 0.5)
    assert (cfg.emotion.initiative, cfg.emotion.persistence) == (0.3, 0.5)


@pytest.mark.parametrize("field", ["sensitivity", "tone", "initiative", "persistence"])
@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_emotion_ranges(field, value):
    with pytest.raises(ValidationError):
        EmotionConfig(**{field: value})


async def test_deadline_loss_and_venting_hit():
    hit = await screen("快点啊，我明天考试，烦死了！！", [], False)
    assert {"deadline", "loss", "venting"} <= set(hit.categories)
    assert 0.0 < hit.level <= 1.0
    assert hit.negativity > 0.5


async def test_plain_greeting_and_joke_do_not_hit():
    assert not (await screen("早，今天吃什么", [], False)).hit
    assert not (await screen("哈哈哈哈哈笑死我了", [], False)).hit


async def test_ordinary_phrases_do_not_hit():
    assert not (await screen("又见面了", [], False)).hit
    assert not (await screen("别急，慢慢来", [], False)).hit
    assert not (await screen("还是你懂我", [], False)).hit
    assert "blocked" in (await screen("又失败了", [], False)).categories
    assert "deadline" in (await screen("我好着急", [], False)).categories


async def test_loss_needs_concrete_phrases():
    assert "loss" in (await screen("今天加班到十点，太累了", [], False)).categories
    assert "loss" in (await screen("我真的受不了了，好想哭", [], False)).categories
    assert not (await screen("这个病句改一下", [], False)).hit
    assert not (await screen("我不累", [], False)).hit


async def test_common_polite_phrases_do_not_hit():
    assert not (await screen("麻烦你改一下这个病句", [], False)).hit
    assert not (await screen("在吗", [], False)).hit
    assert not (await screen("我马上到", [], False)).hit
    assert not (await screen("不是啦，我在开玩笑", [], False)).hit


async def test_valence_triggers_without_keywords():
    hit = await screen("有点郁闷", [], False)
    assert hit.categories == ()
    assert hit.hit and hit.negativity >= 0.4


async def test_negation_does_not_count_as_negative():
    assert (await screen("我不累，也不难受", [], False)).negativity == 0


async def test_repetition_needs_a_similar_recent_message():
    text = "导出又失败了，还是不行"
    assert "repetition" in (await screen(text, [text], False)).categories
    assert "repetition" not in (await screen(text, ["晚上吃火锅"], False)).categories


async def test_failure_signal_alone_hits():
    assert "failure" in (await screen("然后呢", [], True)).categories


async def test_long_first_person_narrative_hits():
    text = "我今天" + "真的很累" * 40 + "，我自己也不知道怎么办，我真的很想休息"
    assert "narrative" in (await screen(text, [], False)).categories


def test_scope_keys_cover_owner_user_and_group():
    assert scope_keys(Context("web:owner", "owner", owner=True)) == ["owner"]
    assert scope_keys(Context("private:qq:7", "7")) == ["user:qq:7"]
    assert scope_keys(Context("group:qq:9", "9", group=True)) == ["group:qq:9"]
    assert scope_keys(Context("group:qq:9", "1", owner=True, group=True)) == ["group:qq:9", "owner"]


def test_read_key_uses_group_atmosphere_in_groups():
    assert read_key(Context("group:qq:9", "1", owner=True, group=True)) == "group:qq:9"
    assert read_key(Context("private:qq:7", "1", owner=True)) == "owner"


def test_decay_half_life():
    assert decay(0.8, 0.0, 0.0, 6 * 3600) == pytest.approx(0.4)
    assert decay(0.8, 0.0, 1.0, 7 * 86400) == pytest.approx(0.4, rel=0.02)


def test_threshold_follows_sensitivity():
    assert threshold(EmotionConfig(sensitivity=0.0)) == pytest.approx(0.85)
    assert threshold(EmotionConfig(sensitivity=1.0)) == pytest.approx(0.15)


def test_combine_weights_and_renormalizes_available_signals():
    cfg = EmotionConfig()
    assert combine(0.8, 0.4, 0.2, cfg) == pytest.approx(0.6 * 0.8 + 0.25 * 0.4 + 0.15 * 0.2)
    assert combine(None, 0.4, 0.2, cfg) == pytest.approx((0.25 * 0.4 + 0.15 * 0.2) / 0.40)


def test_combine_honours_configured_weights():
    cfg = EmotionConfig(rules_weight=0.9, sentiment_weight=0.1)
    assert combine(None, 0.5, 1.0, cfg) == pytest.approx(0.9 * 0.5 + 0.1 * 1.0)


def test_combine_with_zero_weights_yields_nothing():
    cfg = EmotionConfig(judge_weight=0, rules_weight=0, sentiment_weight=0)
    assert combine(0.9, 0.9, 0.9, cfg) == 0.0


def test_merge_ignores_low_signal_without_history():
    assert (
        merge(
            None,
            0.2,
            affect="sad",
            impact=0.0,
            situation="",
            source="judge",
            cfg=EmotionConfig(),
            at=100,
        )
        is None
    )


def test_merge_fuses_and_keeps_situation():
    previous = {
        "value": 0.8,
        "updated": 0.0,
        "affect": "sad",
        "impact": 0.9,
        "situation": "考试压力",
        "source": "judge",
    }
    out = merge(
        previous,
        0.9,
        affect="frustrated",
        impact=0.8,
        situation="项目延期",
        source="judge",
        cfg=EmotionConfig(persistence=0.0, sensitivity=1.0),
        at=6 * 3600,
    )
    assert out["value"] == pytest.approx(0.6 * 0.4 + 0.4 * 0.9)
    assert out["situation"] == "项目延期"
    assert out["updated"] == 6 * 3600


def test_below_threshold_only_decays():
    previous = {
        "value": 0.8,
        "updated": 0.0,
        "affect": "sad",
        "impact": 0.9,
        "situation": "考试压力",
        "source": "judge",
    }
    out = merge(
        previous,
        0.1,
        affect="calm",
        impact=0.0,
        situation="",
        source="judge",
        cfg=EmotionConfig(sensitivity=0.0, persistence=0.0),
        at=6 * 3600,
    )
    assert out["value"] == pytest.approx(0.4)
    assert out["situation"] == "考试压力"


class Jev:
    def __init__(self, answers=None, error=None):
        self.answers, self.error, self.calls, self.routes = answers, error, [], []

    async def decide(self, route, state, questions, settings=None):
        self.routes.append(route)
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        return self.answers

    async def complete(self, route, system, messages, tools, settings=None, **kw):
        return Reply(text="周末考试没考好，压力很大")


def jev_settings(app, routes=("light",)):
    cfg = app.config.value
    cfg.providers.append(
        ProviderConfig(id="ts", protocol="systemone", base_url="https://jev.test/v1")
    )
    cfg.models.append(ModelConfig(id="jev", provider="ts", model="jev-1.14.0"))
    for route in routes:
        cfg.routes[route] = ["jev"]
    return cfg


def jev_service(app, routes=("light",)):
    """Private settings so routing light→jev does not drag the app's own background work onto the network."""
    service = ConfigService(app.home)
    cfg = app.config.value.model_copy(deep=True)
    cfg.providers.append(
        ProviderConfig(id="ts", protocol="systemone", base_url="https://jev.test/v1")
    )
    cfg.models.append(ModelConfig(id="jev", provider="ts", model="jev-1.14.0"))
    for route in routes:
        cfg.routes[route] = ["jev"]
    service.value = cfg
    return service


def jev_answers(dominant="frustrated", intensity=0.9, impact=0.8):
    result = {label: {"noul": 0.0} for label in LABELS}
    if dominant:
        result[dominant] = {"noul": 0.9}
    result["intensity"] = {"noul": intensity}
    result["impact"] = {"noul": impact}
    return result


async def test_jev_judgment_uses_multi_label_noul(app):
    settings = jev_settings(app)
    llm = Jev(jev_answers("frustrated", intensity=0.8, impact=0.7))
    result = await EmotionJudge(llm, app.config).judge(
        settings, "这都第几遍了", [], Screen(("blocked",), 0.5)
    )
    assert (result.affect, result.intensity, result.impact) == ("frustrated", 0.8, 0.7)
    assert result.situation == "周末考试没考好，压力很大"
    assert llm.routes == ["light"]
    _, questions = llm.calls[0]
    assert set(questions) == {*LABELS, "intensity", "impact"}
    assert all(question["type"] == "noul" for question in questions.values())


async def test_memory_route_is_used_when_light_has_no_decision_model(app):
    settings = jev_settings(app, routes=("memory",))
    llm = Jev(jev_answers("sad", intensity=0.6, impact=0.6))
    assert (
        await EmotionJudge(llm, app.config).judge(
            settings, "家里出了点事", [], Screen(("loss",), 0.6)
        )
    ) is not None
    assert llm.routes == ["memory"]


async def test_missing_answer_returns_none(app):
    settings = jev_settings(app)
    llm = Jev({"frustrated": {"noul": 0.5}, "intensity": {"noul": 0.6}})
    assert (
        await EmotionJudge(llm, app.config).judge(settings, "哎", [], Screen(("loss",), 0.6))
        is None
    )


async def test_decide_error_returns_none_and_usable_reports_true(app):
    settings = jev_settings(app)
    llm = Jev(error=RuntimeError("down"))
    judge = EmotionJudge(llm, app.config)
    assert judge.usable(settings) is True
    assert await judge.judge(settings, "哎", [], Screen(("loss",), 0.6)) is None


async def test_no_decision_model_is_unusable(app):
    settings = app.config.value
    judge = EmotionJudge(Jev(), app.config)
    assert judge.usable(settings) is False
    assert await judge.judge(settings, "哎", [], Screen(("loss",), 0.6)) is None


async def test_calm_judgment_skips_situation_call(app):
    settings = jev_settings(app)
    llm = FakeLLM(jev_answers(None, intensity=0.1, impact=0.0))
    result = await EmotionJudge(llm, app.config).judge(
        settings, "嗯", [], Screen(("blocked",), 0.5)
    )
    assert result.situation == ""
    assert llm.completes == 0


def block(**kw):
    return render(
        **{
            "group": False,
            "value": 0.5,
            "affect": "frustrated",
            "situation": "",
            "updated": 0.0,
            "tone": 0.5,
            "at": 0.0,
            **kw,
        }
    )


def test_calm_values_inject_nothing():
    assert block(value=0.1) == ""


def test_low_tone_speaks_only_above_point_six():
    assert block(tone=0.1, value=0.5) == ""
    low = block(tone=0.1, value=0.7)
    assert "烦躁" in low and "少开玩笑" in low


def test_mid_tone_allows_one_short_acknowledgement():
    text = block(tone=0.5)
    assert "烦躁" in text and "理解或确认" in text
    assert "真诚的关心" not in text


def test_high_tone_allows_one_sincere_caring_sentence():
    text = block(tone=0.9)
    assert "真诚的关心" in text and "最多一句" in text


def test_group_never_names_or_cares_publicly():
    text = block(group=True, tone=0.9)
    assert "不要点名" in text and "单独关心" in text


def test_situation_is_private_and_above_threshold():
    assert "考试" not in block(group=True, situation="考试", value=0.7)
    assert "考试" in block(situation="考试", value=0.7, updated=0, at=7200)
    assert "考试" not in block(situation="考试", value=0.3)


def test_affect_words():
    assert "低落" in block(affect="sad", value=0.7)
    assert "焦急" in block(affect="anxious", value=0.7)


class FakeLLM:
    def __init__(self, answers=None, error=None):
        self.answers, self.error, self.decides, self.completes = answers, error, 0, 0
        self.seen = []

    async def decide(self, route, state, questions, settings=None):
        self.decides += 1
        if self.error:
            raise self.error
        return self.answers

    async def complete(self, route, system, messages, tools, settings=None, **kw):
        self.completes += 1
        self.seen.append(list(messages))
        return Reply(text="收到")


async def test_disabled_never_calls_or_writes(app):
    app.config.value.emotion.enabled = False
    llm = FakeLLM()
    manager = EmotionManager(app.store, llm, app.config)
    await manager.process(Context("web:owner", "owner", "agent", True), "烦死了！！！")
    assert (llm.decides, llm.completes) == (0, 0)
    assert await app.store.get("emotion", "owner") is None
    assert (
        await manager.context(Context("web:owner", "owner", "agent", True), app.config.value) == ""
    )


async def test_plain_message_is_not_judged(app):
    app.config.value.emotion.enabled = True
    llm = FakeLLM()
    manager = EmotionManager(app.store, llm, app.config)
    await manager.process(Context("web:owner", "owner", "agent", True), "今天天气不错")
    assert llm.decides == 0
    assert await app.store.get("emotion", "owner") is None


async def test_judgment_updates_owner_and_next_context(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    jev_settings(app)
    llm = FakeLLM(jev_answers("frustrated"))
    manager = EmotionManager(app.store, llm, app.config)
    ctx = Context("web:owner", "owner", "agent", True)
    assert await manager.context(ctx, cfg) == ""
    await manager.process(ctx, "烦死了，作业又没做完！！")
    row = await app.store.get("emotion", "owner")
    assert row["source"] == "judge" and row["value"] > 0.3
    assert "<emotion>" in await manager.context(ctx, cfg)


async def test_context_decays_the_stored_value_on_read(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    manager = EmotionManager(app.store, FakeLLM(), app.config)
    await app.store.put(
        "emotion",
        "owner",
        {
            "value": 0.9,
            "updated": 0.0,
            "affect": "sad",
            "impact": 0.9,
            "situation": "",
            "source": "judge",
        },
        "web:owner",
    )
    ctx = Context("web:owner", "owner", "agent", True)
    assert await manager.context(ctx, cfg) == ""


@pytest.mark.parametrize("error", [RuntimeError("down"), TimeoutError("slow")])
async def test_judge_failure_keeps_state(app, error):
    cfg = app.config.value
    cfg.emotion.enabled = True
    jev_settings(app)
    manager = EmotionManager(app.store, FakeLLM(error=error), app.config)
    await manager.process(Context("web:owner", "owner", "agent", True), "烦死了！！")
    assert await app.store.get("emotion", "owner") is None


async def test_rules_only_when_no_decision_model(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    manager = EmotionManager(app.store, FakeLLM(), app.config)
    await manager.process(Context("web:owner", "owner", "agent", True), "烦死了，考试又砸了！！")
    row = await app.store.get("emotion", "owner")
    assert row["source"] == "rules" and row["value"] > 0


async def test_rule_level_and_valence_fuse_without_a_judge(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    manager = EmotionManager(app.store, FakeLLM(), app.config)
    await manager.process(Context("web:owner", "owner", "agent", True), "又失败了，有点郁闷")
    row = await app.store.get("emotion", "owner")
    # level 0.5 (blocked), negativity 1.0 → (0.25*0.5 + 0.15*1.0) / 0.40 = 0.6875
    assert row["value"] == pytest.approx(0.4 * 0.6875)
    assert row["source"] == "rules" and row["affect"] == "frustrated"


async def test_configured_weights_change_the_stored_value(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    cfg.emotion.rules_weight = 0.9
    cfg.emotion.sentiment_weight = 0.1
    manager = EmotionManager(app.store, FakeLLM(), app.config)
    await manager.process(Context("web:owner", "owner", "agent", True), "又失败了，有点郁闷")
    row = await app.store.get("emotion", "owner")
    # level 0.5, negativity 1.0 → (0.9*0.5 + 0.1*1.0) / 1.0 = 0.55
    assert row["value"] == pytest.approx(0.4 * 0.55)


async def test_calm_judge_outweighs_strong_local_signals(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    jev_settings(app)
    llm = FakeLLM(jev_answers(None, intensity=0.05, impact=0.0))
    manager = EmotionManager(app.store, llm, app.config)
    await manager.process(Context("web:owner", "owner", "agent", True), "家里出了点事，烦死了！！")
    assert await app.store.get("emotion", "owner") is None


async def test_judge_and_local_signals_fuse_by_weight(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    jev_settings(app)
    llm = FakeLLM(jev_answers("frustrated", intensity=0.9, impact=0.8))
    manager = EmotionManager(app.store, llm, app.config)
    await manager.process(Context("web:owner", "owner", "agent", True), "家里出了点事，烦死了！！")
    row = await app.store.get("emotion", "owner")
    # judge .865, level .95, negativity .80 → .6*.865 + .25*.95 + .15*.80 = .8765
    assert row["value"] == pytest.approx(0.4 * 0.8765)
    assert row["source"] == "judge" and row["affect"] == "frustrated"


async def test_scopes_follow_the_conversation(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    jev_settings(app)
    answers = jev_answers("frustrated")
    manager = EmotionManager(app.store, FakeLLM(answers), app.config)
    await manager.process(Context("group:qq:9", "7", group=True), "烦死了，又崩了！！")
    assert await app.store.get("emotion", "group:qq:9") is not None
    assert await app.store.get("emotion", "owner") is None
    await manager.process(Context("private:qq:7", "7"), "烦死了，又崩了！！")
    assert await app.store.get("emotion", "user:qq:7") is not None


async def test_observe_schedules_without_blocking(app):
    app.config.value.emotion.enabled = True
    manager = EmotionManager(app.store, FakeLLM(), app.config)
    await manager.observe(Context("web:owner", "owner", "agent", True), "烦死了！！")
    assert len(manager.tasks) == 1
    await manager.close()
    assert manager.tasks == set()


def turn_calls(llm):
    return [call for call in llm.seen if call and "_original_content" in call[-1]]


async def turn_seen(llm, count=1):
    return len(turn_calls(llm)) >= count


async def owner_idle(app):
    """A submit only enqueues; wait for the turn to finish before sending the next one."""
    actor = app.runtime.actors.get("web:owner")
    return bool(await app.store.list("reply")) and (actor is None or actor.current is None)


async def replies(app, count):
    return len(await app.store.list("reply")) >= count


async def test_emotion_reaches_the_next_turn_context(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    llm = FakeLLM(jev_answers("frustrated"))
    app.runtime.llm = llm
    app.emotion.llm = llm
    app.emotion.config = jev_service(app)
    await app.runtime.submit(
        Context("web:owner", "owner", "chat", True), "烦死了，作业又没做完！！"
    )
    await eventually(lambda: owner_idle(app))
    assert "<emotion>" not in turn_calls(llm)[-1][-1]["content"]
    await app.runtime.submit(Context("web:owner", "owner", "chat", True), "嗯")
    await eventually(lambda: replies(app, 2))
    assert "<emotion>" in turn_calls(llm)[-1][-1]["content"]


async def test_disabled_emotion_leaves_context_unchanged(app):
    llm = FakeLLM()
    app.runtime.llm = llm
    app.emotion.llm = llm
    await app.runtime.submit(
        Context("web:owner", "owner", "chat", True), "烦死了，作业又没做完！！"
    )
    await eventually(lambda: app.store.list("reply"))
    await eventually(lambda: turn_seen(llm))
    assert "<emotion>" not in turn_calls(llm)[-1][-1]["content"]


class Emitter:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)
        return "event-key"


async def test_care_needs_top_initiative_and_high_value(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    cfg.emotion.initiative = 0.8
    jev_settings(app)
    answers = jev_answers("sad", intensity=0.95, impact=0.95)
    emit = Emitter()
    manager = EmotionManager(app.store, FakeLLM(answers), app.config, emit)
    ctx = Context("web:owner", "owner", "agent", True)
    # 初值只取信号的 40%，需要连续几次强信号才越过 0.75。
    for _ in range(4):
        await manager.process(ctx, "家里出了点事，烦死了！！")
    assert len(emit.events) == 1
    event = emit.events[0]
    assert event.source == "情绪" and event.kind == "care"
    await manager.process(ctx, "还是很烦！！")
    assert len(emit.events) == 1  # throttle_seconds 内不重复


class FailingEmitter:
    def __init__(self):
        self.calls = 0

    async def __call__(self, event):
        self.calls += 1
        raise RuntimeError("down")


async def test_failed_care_does_not_throttle(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    cfg.emotion.initiative = 0.8
    jev_settings(app)
    answers = jev_answers("sad", intensity=0.95, impact=0.95)
    emit = FailingEmitter()
    manager = EmotionManager(app.store, FakeLLM(answers), app.config, emit)
    ctx = Context("web:owner", "owner", "agent", True)
    for _ in range(4):
        await manager.process(ctx, "家里出了点事，烦死了！！")
    assert emit.calls == 1
    assert await app.store.get("emotion_care", "owner") is None


async def test_low_initiative_never_emits(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    cfg.emotion.initiative = 0.79
    jev_settings(app)
    answers = jev_answers("sad", intensity=0.95, impact=0.95)
    emit = Emitter()
    manager = EmotionManager(app.store, FakeLLM(answers), app.config, emit)
    ctx = Context("web:owner", "owner", "agent", True)
    for _ in range(4):
        await manager.process(ctx, "家里出了点事，烦死了！！")
    assert emit.events == []


async def test_other_users_never_get_proactive_care(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    cfg.emotion.initiative = 1.0
    jev_settings(app)
    answers = jev_answers("sad", intensity=0.95, impact=0.95)
    emit = Emitter()
    manager = EmotionManager(app.store, FakeLLM(answers), app.config, emit)
    ctx = Context("private:qq:7", "7")
    for _ in range(4):
        await manager.process(ctx, "家里出了点事，烦死了！！")
    assert emit.events == []
    assert (await app.store.get("emotion", "user:qq:7"))["value"] > 0.75
