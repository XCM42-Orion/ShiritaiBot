# 情绪指标系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在后台判定用户情绪与叙述影响，按四个维度影响 Bot 的语气，并只在主人身上允许主动关心。

**Architecture:** 新增 `shiritaibot.emotion` 包：规则筛 → Jev 决策模型判定 → 惰性衰减的 0–1 状态 → 纯函数拼装的 `<context>` 片段。`AgentRuntime` 在每轮开头读取一次状态快照并注入用户消息的 `<context>`，同时异步启动下一轮才生效的判定任务。活跃状态存进现有 `records` 表，不新增表、不新增路由。

**Tech Stack:** Python 3.13 / Pydantic 2 / SQLite（现有 `Store`）/ asyncio / pytest-asyncio / React + TypeScript 配置表单。

**Spec:** `docs/superpowers/specs/2026-11-01-emotion-design.md`

## 实现决策（评审时可推翻）

- 取样点是 `AgentRuntime._turn`：覆盖全部私聊用户、群里被唤起/触发的消息和 WebUI。未进入轮次的群消息不参与群气氛，避免为每条群消息调用模型。
- 群聊判定只喂当前消息，不回看群内其他人的原文（与设计文档“不含群内其他人的原文”一致）；私聊回看该用户最近 3 条。
- `intensity` / `impact` 只由 Jev 判定；没有可用决策模型时用规则值，文本模型只负责 `situation`。
- 主动关心的值门槛固定为 `0.75`，与 `initiative >= 0.8` 同时成立才发；初值公式让这个门槛需要连续几次强信号才能达到，符合“持续不好才打扰”。

## Global Constraints

- 情绪判定只用 Jev（System One 决策模型）：`intensity`、`impact` 用 `noul`，`affect` 用 `choice`。判定路由先 `light`、空则 `memory`；不新增路由。
- 没有可用决策模型时只用规则值；判定调用失败、超时、坏答案一律“本轮无判定”，不写状态。
- 总开关 `enabled=false` 时：不做规则筛、不调模型、不读写 `emotion` 记录、不注入任何文本。提示词与当前版本逐字一致。
- 判定异步执行，结果从下一轮开始生效；任务异常不得影响本轮回复。
- 不保存消息原文：`emotion` 记录只存派生值和概括后的近况；日志不写原文。
- 群聊只维护群气氛值，片段里不得点名或评论个人；主动关心只发给主人。
- 提示注入只写进用户消息的 `<context>` 块；`Settings.system_prompt()` 保持不变。
- 不新增第三方依赖；复用 `jieba` 分词（`shiritaibot.memory.longterm.tokenize` / `keywords`）。
- 实现者不执行 `git commit` / `git push`：每个任务结束时列出改动文件与测试结果，交给用户提交。
- 测试命令：`uv run pytest -q`；单文件 `uv run pytest tests/test_emotion.py -q`。

## File Structure

| 文件 | 责任 |
|---|---|
| `src/shiritaibot/emotion/__init__.py` | `EmotionManager`：调度判定任务、读写状态、产出注入片段、主动关心 |
| `src/shiritaibot/emotion/rules.py` | 纯规则筛：命中类别与粗粒度强度 |
| `src/shiritaibot/emotion/judge.py` | Jev 判定 + `light` 文本模型补近况 |
| `src/shiritaibot/emotion/state.py` | 作用域键、衰减、门槛、融合 |
| `src/shiritaibot/emotion/prompt.py` | 纯函数：按作用域和语气档位拼 `<emotion>` 片段 |
| `src/shiritaibot/config/__init__.py` | `EmotionConfig` 与 `Settings.emotion` |
| `src/shiritaibot/agent/__init__.py` | 每轮快照注入 + 启动判定任务 + 关闭 |
| `src/shiritaibot/app.py` | 构造 `EmotionManager`，接 `proactive.emit` |
| `web/src/api/types.ts`、`web/src/config/schema.ts`、`web/src/pages/SettingsPage.tsx` | 配置表单 |
| `tests/test_emotion.py` | 单元与集成测试 |

## Review Focus

以下输入/场景最容易被真实使用撞上，每个都要有测试，写在对应任务里：

1. 情绪功能开着、用户只是正常打招呼 → 不调用任何模型，`<context>` 不出现 `<emotion>`（Task 6、7）。
2. 用户发“哈哈哈哈哈笑死我了”这类玩笑/反讽 → 规则不命中、不判定；即使规则兜底命中，也不产生“低落”标签（Task 2、6）。
3. 群成员诉说个人困难 → 只更新群气氛值，群里回复不点名、不公开关心；同一条消息不落到主人值（Task 3、6）。
4. 用户反复重发同一条失败消息 → 重复信号会命中，但主动关心在 `throttle_seconds` 内最多一次，不重复发（Task 8）。
5. 旧配置文件没有 `emotion` 字段 → 默认关闭、应用正常启动；范围越界的配置被拒绝（Task 1）。

---

### Task 1: EmotionConfig

**Files:**
- Modify: `src/shiritaibot/config/__init__.py`（`ProactiveConfig` 之后新增类；`Settings` 的 `vision` 字段后新增字段）
- Test: `tests/test_emotion.py`

**Interfaces:**
- Consumes: 现有 `Model` 基类。
- Produces: `EmotionConfig`，字段 `enabled: bool=False`、`sensitivity: float=0.5`、`tone: float=0.5`、`initiative: float=0.3`、`persistence: float=0.5`、`developer: bool=False`（四个数值均 `ge=0, le=1`）；`Settings.emotion: EmotionConfig`。

- [ ] **Step 1: 写失败测试**

```python
import pytest
from pydantic import ValidationError

from shiritaibot.config import EmotionConfig, Settings


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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: FAIL — `ImportError: cannot import name 'EmotionConfig'`

- [ ] **Step 3: 实现配置**

在 `src/shiritaibot/config/__init__.py` 的 `ProactiveConfig` 后面新增：

```python
class EmotionConfig(Model):
    enabled: bool = False
    sensitivity: float = Field(default=0.5, ge=0, le=1)
    tone: float = Field(default=0.5, ge=0, le=1)
    initiative: float = Field(default=0.3, ge=0, le=1)
    persistence: float = Field(default=0.5, ge=0, le=1)
    developer: bool = False
```

`Settings` 中 `vision` 之后新增：`emotion: EmotionConfig = Field(default_factory=EmotionConfig)`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 确认旧配置相关测试不回归**

Run: `uv run pytest tests/test_context_settings.py tests/test_provider_config.py -q`
Expected: PASS

---

### Task 2: 规则筛

**Files:**
- Create: `src/shiritaibot/emotion/__init__.py`（先留空）
- Create: `src/shiritaibot/emotion/rules.py`
- Test: `tests/test_emotion.py`

**Interfaces:**
- Consumes: `shiritaibot.memory.longterm.tokenize`、`keywords`。
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class Screen:
      categories: tuple[str, ...] = ()
      level: float = 0.0
      @property
      def hit(self) -> bool: ...

  async def screen(text: str, recent: list[str], failed: bool) -> Screen
  ```

- [ ] **Step 1: 写失败测试**

```python
from shiritaibot.emotion.rules import screen


async def test_urgency_hardship_and_emphasis_hit():
    hit = await screen("快点啊，我明天考试，烦死了！！", [], False)
    assert {"urgency", "hardship", "emphasis"} <= set(hit.categories)
    assert 0.0 < hit.level <= 1.0


async def test_plain_greeting_and_joke_do_not_hit():
    assert not (await screen("早，今天吃什么", [], False)).hit
    assert not (await screen("哈哈哈哈哈笑死我了", [], False)).hit


async def test_repetition_needs_a_similar_recent_message():
    text = "导出又失败了，还是不行"
    assert "repetition" in (await screen(text, [text], False)).categories
    assert "repetition" not in (await screen(text, ["晚上吃火锅"], False)).categories


async def test_failure_signal_alone_hits():
    assert "failure" in (await screen("然后呢", [], True)).categories


async def test_long_first_person_narrative_hits():
    text = "我今天" + "真的很累" * 40 + "，我自己也不知道怎么办，我真的很想休息"
    assert "narrative" in (await screen(text, [], False)).categories
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: FAIL — `ModuleNotFoundError: shiritaibot.emotion.rules`

- [ ] **Step 3: 实现规则**

`emotion/rules.py`，词表与权重固定如下：

```python
CATEGORY_WEIGHTS = {
    "correction": 0.5, "urgency": 0.5, "emphasis": 0.35, "repetition": 0.4,
    "failure": 0.45, "hardship": 0.6, "narrative": 0.35,
}
CORRECTION = ("不对", "不是", "又", "还是", "怎么还", "没听懂", "理解错", "错了")
URGENCY = ("快点", "赶紧", "马上", "急", "还在吗", "尽快", "来不及", "立刻", "怎么还没")
HARDSHIP = ("累", "病", "疼", "失眠", "加班", "压力", "考试", "分手", "家里",
            "难过", "焦虑", "抑郁", "失业", "住院", "吵架")
HEAVY = ("操", "靠", "特么", "妈的", "烦死", "无语", "崩溃")
```

- `emphasis`：`text.count("!") + text.count("！") >= 2`，或包含 `HEAVY` 中任一词。
- `repetition`：与新消息最近 3 条已存用户消息比较；两侧都先 `keywords(await tokenize(x))`，Jaccard `len(a & b) / len(a | b) >= 0.6`，且两侧 token 数都不少于 2。
- `narrative`：`len(text) >= 150` 且 `text.count("我") >= 3`。
- `failure`：直接使用传入的 `failed`。
- `level = min(1.0, sum(CATEGORY_WEIGHTS[c] for c in categories))`；类别按上述固定顺序去重。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: PASS

---

### Task 3: 状态与数值模型

**Files:**
- Create: `src/shiritaibot/emotion/state.py`
- Test: `tests/test_emotion.py`

**Interfaces:**
- Consumes: `Context`（`session`、`owner`、`group`）。
- Produces:
  ```python
  KIND = "emotion"

  def scope_keys(ctx: Context) -> list[str]      # 需要更新的作用域
  def read_key(ctx: Context) -> str              # 本轮读取/注入的作用域
  def threshold(cfg: EmotionConfig) -> float
  def decay(value: float, updated: float, persistence: float, at: float) -> float
  def merge(previous: dict | None, raw: float, *, affect: str, impact: float,
            situation: str, source: str, cfg: EmotionConfig, at: float) -> dict | None
  ```

- [ ] **Step 1: 写失败测试**

```python
from shiritaibot.core import Context
from shiritaibot.emotion.state import decay, merge, read_key, scope_keys, threshold


def test_scope_keys_cover_owner_user_and_group():
    assert scope_keys(Context("web:owner", "owner", owner=True)) == ["owner"]
    assert scope_keys(Context("private:qq:7", "7")) == ["user:qq:7"]
    assert scope_keys(Context("group:qq:9", "9", group=True)) == ["group:qq:9"]
    assert scope_keys(Context("group:qq:9", "1", owner=True, group=True)) == ["group:qq:9", "owner"]


def test_read_key_uses_group_atmosphere_in_groups():
    assert read_key(Context("group:qq:9", "1", owner=True, group=True)) == "group:qq:9"
    assert read_key(Context("private:qq:7", "1", owner=True)) == "owner"


def test_decay_half_life():
    assert decay(0.8, 0.0, 0.5, 6 * 3600) == pytest.approx(0.4)
    assert decay(0.8, 0.0, 1.0, 7 * 86400) == pytest.approx(0.4, rel=0.02)


def test_threshold_follows_sensitivity():
    assert threshold(EmotionConfig(sensitivity=0.0)) == pytest.approx(0.85)
    assert threshold(EmotionConfig(sensitivity=1.0)) == pytest.approx(0.15)


def test_merge_ignores_low_signal_without_history():
    assert merge(None, 0.2, affect="sad", impact=0.0, situation="", source="judge",
                 cfg=EmotionConfig(), at=100) is None


def test_merge_fuses_and_keeps_situation():
    previous = {"value": 0.8, "updated": 0.0, "affect": "sad", "impact": 0.9,
                "situation": "考试压力", "source": "judge"}
    out = merge(previous, 0.9, affect="frustrated", impact=0.8, situation="项目延期",
                source="judge", cfg=EmotionConfig(persistence=0.5, sensitivity=1.0), at=6 * 3600)
    assert out["value"] == pytest.approx(0.6 * 0.4 + 0.4 * 0.9)
    assert out["situation"] == "项目延期"
    assert out["updated"] == 6 * 3600


def test_below_threshold_only_decays():
    previous = {"value": 0.8, "updated": 0.0, "affect": "sad", "impact": 0.9,
                "situation": "考试压力", "source": "judge"}
    out = merge(previous, 0.1, affect="calm", impact=0.0, situation="",
                source="judge", cfg=EmotionConfig(sensitivity=0.0), at=6 * 3600)
    assert out["value"] == pytest.approx(0.4)
    assert out["situation"] == "考试压力"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: FAIL — `ModuleNotFoundError: shiritaibot.emotion.state`

- [ ] **Step 3: 实现**

- `scope_keys`：群聊 → `[ctx.session]`，`ctx.owner` 时再追加 `"owner"`（顺序固定，群在前）；非群 `owner` → `["owner"]`；其余 → `["user:" + ctx.session.removeprefix("private:")]`。
- `read_key`：群聊一律返回 `ctx.session`；否则同 `scope_keys` 的单键。
- `threshold(cfg) = 0.85 - 0.7 * cfg.sensitivity`。
- `decay`：`value * 0.5 ** ((at - updated) / (6 * 3600 * 2 ** (4.8 * persistence)))`。
- `merge`：先算 `decayed`（无 previous 为 0.0）。`raw < threshold` 时，无 previous 返回 `None`，有则返回 `{**previous, "value": decayed, "updated": at}`。否则
  `value = min(1.0, max(0.0, 0.6 * decayed + 0.4 * raw))`；`situation` 在新值非空且 `raw >= decayed` 时替换，否则沿用；`affect` 新值非空时替换；`impact`、`source` 用本次值；`updated = at`。
- 记录字段固定：`{"value", "affect", "impact", "situation", "updated", "source"}`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: PASS

---

### Task 4: Jev 情绪判定

**Files:**
- Create: `src/shiritaibot/emotion/judge.py`
- Test: `tests/test_emotion.py`

**Interfaces:**
- Consumes: `ProviderManager.decide(route, state, questions, settings)`、`ProviderManager.complete(route, system, messages, tools, settings=...)`、`Reply.text`、`Screen`。
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class Judgment:
      affect: str      # frustrated | anxious | sad | calm | ""
      intensity: float
      impact: float
      situation: str
      source: str      # judge | rules

  class EmotionJudge:
      def __init__(self, llm, config): ...
      def usable(self, settings) -> bool
      async def judge(self, settings, text: str, recent: list[str], screen: Screen) -> Judgment | None
  ```

- [ ] **Step 1: 写失败测试**

```python
from shiritaibot.core import Reply
from shiritaibot.emotion.judge import EmotionJudge
from shiritaibot.emotion.rules import Screen


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
    cfg.providers.append(ProviderConfig(id="ts", protocol="systemone", base_url="https://jev.test/v1"))
    cfg.models.append(ModelConfig(id="jev", provider="ts", model="jev-1.14.0"))
    for route in routes:
        cfg.routes[route] = ["jev"]
    return cfg


async def test_jev_judgment_uses_noul_and_choice(app):
    settings = jev_settings(app)
    llm = Jev({"affect": {"choice": "frustrated", "confidence": 0.9},
               "intensity": {"noul": 0.8}, "impact": {"noul": 0.7}})
    result = await EmotionJudge(llm, app.config).judge(
        settings, "这都第几遍了", [], Screen(("correction",), 0.5))
    assert result == Judgment("frustrated", 0.8, 0.7, "周末考试没考好，压力很大", "judge")
    assert llm.routes == ["light"]
    _, questions = llm.calls[0]
    assert set(questions) == {"affect", "intensity", "impact"}
    assert questions["intensity"]["type"] == "noul"
    assert questions["affect"]["type"] == "choice"


async def test_memory_route_is_used_when_light_has_no_decision_model(app):
    settings = jev_settings(app, routes=("memory",))
    llm = Jev({"affect": {"choice": "sad", "confidence": 0.9},
               "intensity": {"noul": 0.6}, "impact": {"noul": 0.6}})
    assert (await EmotionJudge(llm, app.config).judge(
        settings, "家里出了点事", [], Screen(("hardship",), 0.6))) is not None
    assert llm.routes == ["memory"]


async def test_missing_answer_returns_none(app):
    settings = jev_settings(app)
    llm = Jev({"affect": {"choice": "sad"}, "intensity": {"noul": 0.6}})
    assert await EmotionJudge(llm, app.config).judge(
        settings, "哎", [], Screen(("hardship",), 0.6)) is None


async def test_decide_error_returns_none_and_usable_reports_true(app):
    settings = jev_settings(app)
    llm = Jev(error=RuntimeError("down"))
    judge = EmotionJudge(llm, app.config)
    assert judge.usable(settings) is True
    assert await judge.judge(settings, "哎", [], Screen(("hardship",), 0.6)) is None


async def test_no_decision_model_is_unusable(app):
    settings = app.config.value
    judge = EmotionJudge(Jev(), app.config)
    assert judge.usable(settings) is False
    assert await judge.judge(settings, "哎", [], Screen(("hardship",), 0.6)) is None
```

`tests/test_emotion.py` 顶部补齐：`from shiritaibot.config import EmotionConfig, ModelConfig, ProviderConfig`，`from shiritaibot.emotion.judge import EmotionJudge, Judgment`。

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: FAIL — `ModuleNotFoundError: shiritaibot.emotion.judge`

- [ ] **Step 3: 实现**

- `usable(settings)`：遍历 `("light", "memory")`，路由候选里存在 `enabled` 模型且其 provider `enabled` 且 `protocol == "systemone"` 即为 True。
- `judge()` 选路由顺序 `light` → `memory`，用第一个 `usable` 命中的路由；都不命中直接返回 `None`。
- `state = {"message": text[:2000], "recent": recent[-3:], "signals": list(screen.categories)}`。
- questions（指令用英文，与 `memory/judge.py` 一致）：
  - `affect`：`choice`，criteria 四项——`frustrated`（irritated/impatient，反复失败或没被理解）、`anxious`（urgent/worried）、`sad`（low/distressed，个人难处或坏消息）、`calm`（neutral/ordinary/joking）。
  - `intensity`：`noul`，“How strongly does `message` express the emotion?”
  - `impact`：`noul`，“How much would what the user describes affect their life, even if said calmly?”
- 调 `llm.decide(route, state, questions, settings)`；任何异常或字段缺失/类型错误 → 返回 `None`。`intensity`/`impact` 截到 `[0, 1]`；`affect` 不在四值内时用 `"calm"`。
- `situation`：仅当 `settings.routes.get("light")` 非空时调一次 `llm.complete("light", SITUATION_PROMPT, [{"role": "user", "content": canonical({"message": text[:2000], "recent": recent[-3:]})}], [], settings=settings)`，取 `.text.strip()[:40]`；异常或空路由 → `""`。
  `SITUATION_PROMPT = "根据用户最近的消息，用不超过 40 字概括对方眼下的近况或烦心事。只写概括，不抄原话，不写姓名、联系方式等隐私；没有值得记住的近况就输出空字符串。只输出概括本身。"`

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: PASS

---

### Task 5: 提示片段

**Files:**
- Create: `src/shiritaibot/emotion/prompt.py`
- Test: `tests/test_emotion.py`

**Interfaces:**
- Produces:
  ```python
  def render(*, group: bool, value: float, affect: str, situation: str,
             updated: float, tone: float, at: float) -> str
  ```

- [ ] **Step 1: 写失败测试**

```python
from shiritaibot.emotion.prompt import render


def block(**kw):
    return render(**{"group": False, "value": 0.5, "affect": "frustrated", "situation": "",
                     "updated": 0.0, "tone": 0.5, "at": 0.0, **kw})


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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: FAIL — `ModuleNotFoundError: shiritaibot.emotion.prompt`

- [ ] **Step 3: 实现**

- `value < 0.25` → `""`。
- 情绪词：`{"frustrated": "烦躁", "anxious": "焦急", "sad": "低落"}`，其它/空 → `"情绪波动"`。强度前缀：`>= 0.85` 为 `"非常"`，`>= 0.6` 为 `""`，否则 `"有些"`。
- 私聊、`tone < 0.2`：只有 `value >= 0.6` 才输出
  `<emotion>\n对方当前：{强度}{情绪}。\n语气收敛，少开玩笑。\n</emotion>`，否则 `""`。
- 私聊、`tone >= 0.2`：
  ```
  <emotion>
  对方当前：{强度}{情绪}。
  语气放稳，先回应用户真正要处理的事。
  可以有一句简短的理解或确认，但不要展开安慰。
  </emotion>
  ```
  `tone > 0.6` 时在结束标签前插入：`如果合适，可以说一句真诚的关心，最多一句；不要每轮都关心，也不要让话题偏离开用户的问题。`
  `situation` 非空且 `value >= 0.4` 时再插入一行：`此前提到：{situation}（{age}）。可以自然接上，但不要主动追问细节。`
- 群聊：`tone < 0.2` 且 `value < 0.6` → `""`；否则
  ```
  <emotion>
  本群当前的气氛：{强度}{情绪}。
  回复时整体语气放稳，先解决大家在问的事，少开玩笑。不要点名或评论任何人的状态，也不要公开表示关心。
  </emotion>
  ```
  高语气档（`tone > 0.6`）追加：`可以简短表达理解，但不要单独关心某个人，也不要追问隐私。` 群聊不输出 `situation`。
- `age`：`at - updated` 小于 60 秒 → `"刚刚"`，小于 1 小时 → `"{n} 分钟前"`，小于 1 天 → `"{n} 小时前"`，否则 `"{n} 天前"`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: PASS

---

### Task 6: EmotionManager

**Files:**
- Modify: `src/shiritaibot/emotion/__init__.py`
- Test: `tests/test_emotion.py`

**Interfaces:**
- Consumes: `Store.get/put/history`、`ProviderManager`、`ConfigService`、`Context`、`Screen`、`EmotionJudge`、`state`、`prompt`。
- Produces:
  ```python
  class EmotionManager:
      def __init__(self, store, llm, config, emit=None): ...
      async def observe(self, ctx: Context, text: str) -> None
      async def process(self, ctx: Context, text: str,
                        recent: list[str] | None = None, failed: bool | None = None) -> None
      async def context(self, ctx: Context, cfg) -> str
      async def close(self) -> None
  ```

- [ ] **Step 1: 写失败测试**

```python
import asyncio
from shiritaibot.emotion import EmotionManager


class FakeLLM:
    def __init__(self, answers=None, error=None):
        self.answers, self.error, self.decides, self.completes = answers, error, 0, 0

    async def decide(self, route, state, questions, settings=None):
        self.decides += 1
        if self.error:
            raise self.error
        return self.answers

    async def complete(self, route, system, messages, tools, settings=None, **kw):
        self.completes += 1
        return Reply(text="项目一直做不好")


async def test_disabled_never_calls_or_writes(app):
    app.config.value.emotion.enabled = False
    llm = FakeLLM()
    manager = EmotionManager(app.store, llm, app.config)
    await manager.process(Context("web:owner", "owner", "agent", True), "烦死了！！！")
    assert (llm.decides, llm.completes) == (0, 0)
    assert await app.store.get("emotion", "owner") is None
    assert await manager.context(Context("web:owner", "owner", "agent", True), app.config.value) == ""


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
    llm = FakeLLM({"affect": {"choice": "frustrated"},
                   "intensity": {"noul": 0.9}, "impact": {"noul": 0.8}})
    manager = EmotionManager(app.store, llm, app.config)
    ctx = Context("web:owner", "owner", "agent", True)
    assert await manager.context(ctx, cfg) == ""
    await manager.process(ctx, "烦死了，作业又没做完！！")
    row = await app.store.get("emotion", "owner")
    assert row["source"] == "judge" and row["value"] > 0.3
    assert "<emotion>" in await manager.context(ctx, cfg)


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


async def test_scopes_follow_the_conversation(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    jev_settings(app)
    answers = {"affect": {"choice": "frustrated"}, "intensity": {"noul": 0.9}, "impact": {"noul": 0.9}}
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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: FAIL — `ImportError: cannot import name 'EmotionManager'`

- [ ] **Step 3: 实现**

- `_signals(ctx) -> tuple[list[str], bool]`：读一次 `history = await self.store.history(ctx.session, limit=12, include_commands=False)`；`recent = [m.get("_original_content") or m.get("content", "") for m in history if m.get("role") == "user"][-3:]`，`ctx.group` 为真时改为 `[]`（不把其他群成员的原文交给判定）；`failed` 为任一 `role == "tool"` 且 `json.loads(content)` 是只含 `error` 键的 dict。
- `async def observe(ctx, text)`：`enabled` 为假或 `ctx.background` 为真时直接返回；`recent, failed = await self._signals(ctx)` 后 `asyncio.create_task(self.process(ctx, text, recent, failed))` 放进 `self.tasks`，`add_done_callback(self.tasks.discard)`。历史必须在调度前读：此时本轮用户消息还没写进 history，判定不会把自己的消息当成“重复”。
- `process(ctx, text, recent=None, failed=None)`：
  1. `cfg = self.config.value.model_copy(deep=True)`；`enabled` 为假直接返回。
  2. `recent is None` 时 `recent, failed = await self._signals(ctx)`。
  3. `screen = await rules.screen(text, recent, failed)`；未命中返回。
  4. `judge = EmotionJudge(self.llm, self.config)`；在 `async with asyncio.timeout(JUDGE_TIMEOUT)`（模块常量 `JUDGE_TIMEOUT = 30`，整段判定共用）里 `judgment = await judge.judge(cfg, text, recent, screen)`。为 `None` 且 `judge.usable(cfg)` 为真 → 返回（本轮无判定）；`usable` 为假 → `Judgment("", screen.level, 0.0, "", "rules")`。超时抛出的 `TimeoutError` 由第 7 步的兜底捕获。
  5. `raw = 0.65 * judgment.intensity + 0.35 * judgment.impact`；`at = now().timestamp()`。
  6. 对 `state.scope_keys(ctx)` 中每个键：`previous = await self.store.get(state.KIND, key)`；`updated = state.merge(previous, raw, affect=judgment.affect, impact=judgment.impact, situation=judgment.situation, source=judgment.source, cfg=cfg.emotion, at=at)`；`None` 跳过；`await self.store.put(state.KIND, key, updated, ctx.session)`；`cfg.emotion.developer` 为真时 `log.log(f"情绪判定 {key}: value={...:.2f} source={...} affect={...} categories={...}")`（不写消息原文）。
  7. 整个函数体用 `try/except Exception` 包住，异常只记 `log.debug`，不向外抛。
- `context(ctx, cfg)`：`cfg.emotion.enabled` 为假返回 `""`；按 `state.read_key(ctx)` 读记录；无记录返回 `""`；否则调用 `prompt.render(...)`，`at=now().timestamp()`。
- `close()`：取消 `self.tasks` 中所有任务并 `await asyncio.gather(..., return_exceptions=True)`，清空集合。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: PASS

---

### Task 7: 接入 Agent 与 Application

**Files:**
- Modify: `src/shiritaibot/agent/__init__.py`（`AgentRuntime.__init__`、`_turn`、`close`）
- Modify: `src/shiritaibot/app.py`（构造与传参）
- Test: `tests/test_emotion.py`

**Interfaces:**
- Consumes: `EmotionManager.observe/context/close`；`Context`。
- Produces: `AgentRuntime(..., short_term=None, emotion=None)`；`Application.emotion: EmotionManager`。

- [ ] **Step 1: 写失败测试**

```python
async def test_emotion_reaches_the_next_turn_context(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    jev_settings(app)
    llm = FakeLLM({"affect": {"choice": "frustrated"},
                   "intensity": {"noul": 0.9}, "impact": {"noul": 0.8}})
    app.runtime.llm = llm
    app.emotion.llm = llm
    await app.runtime.submit(Context("web:owner", "owner", "chat", True), "烦死了，作业又没做完！！")
    await eventually(lambda: app.store.get("emotion", "owner"))
    assert "<emotion>" not in llm.seen[0][-1]["content"]
    await app.runtime.submit(Context("web:owner", "owner", "chat", True), "嗯")
    assert "<emotion>" in llm.seen[-1][-1]["content"]


async def test_disabled_emotion_leaves_context_unchanged(app):
    llm = FakeLLM()
    app.runtime.llm = llm
    app.emotion.llm = llm
    await app.runtime.submit(Context("web:owner", "owner", "chat", True), "烦死了，作业又没做完！！")
    await eventually(lambda: app.store.list("reply"))
    assert "<emotion>" not in llm.seen[-1][-1]["content"]
```

`FakeLLM` 增加 `self.seen = []` 并在 `complete` 里 `self.seen.append(list(messages))`、`return Reply(text="收到")`；`decide` 在未配置 `answers` 时返回 `None`（这样路由与记忆判定走默认路径）。测试文件顶部加 `from test_permissions import eventually`。

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: FAIL — `AttributeError: 'Application' object has no attribute 'emotion'` 或 `<emotion>` 不在上下文里

- [ ] **Step 3: 实现**

`app.py`：
- `from shiritaibot.emotion import EmotionManager`
- `self.emotion = EmotionManager(self.store, self.llm, self.config, self.proactive.emit)`，放在 `self.proactive` 构造之后。
- `AgentRuntime(..., self.short_term, emotion=self.emotion)`。

`agent/__init__.py`：
- `AgentRuntime.__init__(self, config, store, llm, registry, memory, deliver, short_term=None, emotion=None)`，保存 `self.emotion = emotion`。
- `_turn` 中 `cfg` 与 `system` 之后、`route = await self.route(...)` 之前：
  ```python
  emotion_block = await self.emotion.context(ctx, cfg) if self.emotion else ""
  if self.emotion:
      await self.emotion.observe(ctx, text)
  ```
  先取快照再启动判定，保证本轮判定结果本轮不可见；`observe` 在这里读历史，当前消息尚未写入，不会被当成重复。
- 组装用户消息时把原 `recalled` 改成 `blocks = [emotion_block] if emotion_block else []`，原有记忆与近况行继续 append，`content` 用 `"\n".join(block for block in blocks if block)`。
- `close()` 末尾（`await asyncio.gather(...)` 之后）：`if self.emotion: await self.emotion.close()`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: PASS

- [ ] **Step 5: 现有 Agent 测试不回归**

Run: `uv run pytest tests/test_agent.py tests/test_group_context.py -q`
Expected: PASS

---

### Task 8: 主动关心（仅主人）

**Files:**
- Modify: `src/shiritaibot/emotion/__init__.py`（`_care`）
- Test: `tests/test_emotion.py`

**Interfaces:**
- Consumes: `emit` 回调（`ProactiveEngine.emit` 的签名：`async def emit(event: ExternalEvent) -> str`）、`ExternalEvent`、`Settings.proactive.throttle_seconds`、`Settings.emotion.initiative`。
- Produces: 记录 kind `emotion_care`，键 `"owner"`，值 `{"at": float}`。

- [ ] **Step 1: 写失败测试**

```python
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
    answers = {"affect": {"choice": "sad"}, "intensity": {"noul": 0.95}, "impact": {"noul": 0.95}}
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


async def test_low_initiative_never_emits(app):
    cfg = app.config.value
    cfg.emotion.enabled = True
    cfg.emotion.initiative = 0.79
    jev_settings(app)
    answers = {"affect": {"choice": "sad"}, "intensity": {"noul": 0.95}, "impact": {"noul": 0.95}}
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
    answers = {"affect": {"choice": "sad"}, "intensity": {"noul": 0.95}, "impact": {"noul": 0.95}}
    emit = Emitter()
    manager = EmotionManager(app.store, FakeLLM(answers), app.config, emit)
    ctx = Context("private:qq:7", "7")
    for _ in range(4):
        await manager.process(ctx, "家里出了点事，烦死了！！")
    assert emit.events == []
    assert (await app.store.get("emotion", "user:qq:7"))["value"] > 0.75
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: FAIL — `Emitter` 从未被调用

- [ ] **Step 3: 实现**

`_care(cfg, updated, at)`，只在 `key == "owner"` 时调用：
- `cfg.emotion.initiative < 0.8` 或 `updated["value"] < 0.75` 或 `self.emit is None` → 返回。
- `last = await self.store.get("emotion_care", "owner")`；`at - last.get("at", 0) < cfg.proactive.throttle_seconds` → 返回。
- 先写 `{"at": at}`，再 `await self.emit(ExternalEvent(source="情绪", kind="care", dedup_key=f"emotion:{int(at // 600)}", title=f"主人最近的情绪状态不好（强度 {updated['value']:.2f}），可以主动私聊简单关心一句，不要追问隐私。", payload={"scope": "owner"}, urgency="low"))`。
- 事件失败由 `ProactiveEngine.emit` 自己记录；这里只需不让异常逃出 `process`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_emotion.py -q`
Expected: PASS

- [ ] **Step 5: 主动事件管道不受影响**

Run: `uv run pytest tests/test_proactive.py -q`
Expected: PASS

---

### Task 9: WebUI 配置

**Files:**
- Modify: `web/src/api/types.ts`（`EmotionConfig` 与 `Settings.emotion`）
- Modify: `web/src/config/schema.ts`（`EMOTION_FIELDS`）
- Modify: `web/src/pages/SettingsPage.tsx`（“情绪（开发者选项）”分组）

**Interfaces:**
- Consumes: `FieldForm`、`FieldSpec`。
- Produces: 后端 `GET /api/config` 已有 `emotion`（Pydantic `model_dump`），前端类型与表单对齐。

- [ ] **Step 1: 加类型**

`types.ts`：

```ts
export interface EmotionConfig {
  enabled: boolean;
  sensitivity: number;
  tone: number;
  initiative: number;
  persistence: number;
  developer: boolean;
}
```

`Settings` 增加 `emotion: EmotionConfig;`。

- [ ] **Step 2: 加字段规格**

`schema.ts`：

```ts
export const EMOTION_FIELDS: FieldSpec[] = [
  {
    key: "enabled",
    label: "启用情绪指标",
    kind: "boolean",
    help: "识别用户说话时的情绪和近况，影响回复语气。关闭时不调用任何模型，也不会改变提示词。",
  },
  {
    key: "sensitivity",
    label: "敏感度",
    kind: "number", min: 0, max: 1, step: 0.05,
    help: "多小的信号才会更新情绪值。越高越容易被触发。",
  },
  {
    key: "tone",
    label: "语气幅度",
    kind: "number", min: 0, max: 1, step: 0.05,
    help: "情绪对语气的改变幅度；高档允许一句真诚的关心。",
  },
  {
    key: "initiative",
    label: "主动性",
    kind: "number", min: 0, max: 1, step: 0.05,
    help: "回复里主动关心的程度。只有 0.8 及以上、且主人状态很差时，才允许主动发一条关心消息。",
  },
  {
    key: "persistence",
    label: "延续性",
    kind: "number", min: 0, max: 1, step: 0.05,
    help: "情绪值的衰减速度：0 约 6 小时减半，1 约 7 天减半。",
  },
  {
    key: "developer",
    label: "开发者日志",
    kind: "boolean",
    help: "把判定结果与当前值写入日志。界面不显示情绪值，也不记录消息原文。",
  },
];
```

- [ ] **Step 3: 加分组**

`SettingsPage.tsx` 导入 `EMOTION_FIELDS`，在“主动性与打扰策略”`</section>` 之后追加：

```tsx
<section className="panel">
  <h2>情绪（开发者选项）</h2>
  <p className="muted">
    情绪值只在后台使用，不显示在界面上。群聊只调整整体语气，不会点名或公开关心任何人；
    主动关心只发给主人。
  </p>
  <FieldForm
    value={config.emotion}
    fields={EMOTION_FIELDS}
    onChange={(emotion) => setConfig({ ...config, emotion })}
  />
</section>
```

- [ ] **Step 4: 构建确认**

Run: `npm --prefix web run build`
Expected: `tsc -b` 与 `vite build` 均成功，无类型错误。

- [ ] **Step 5: 前端现有测试**

Run: `npm --prefix web test`
Expected: PASS

---

### Task 10: 全量验证

- [ ] **Step 1: 后端全量测试**

Run: `uv run pytest -q`
Expected: 现有 337 项与新增测试全部通过，无 warning 级任务泄漏。

- [ ] **Step 2: 静态检查**

Run: `uv run ruff check src tests`
Expected: `All checks passed!`

- [ ] **Step 3: 跑一次真实流程（可选但推荐）**

用 `Application(tmp_path)` 手动脚本（不进测试套件）：开启 `emotion.enabled`、配置假 Jev，提交“快点，又失败了！！”两次，确认第一轮上下文无 `<emotion>`、第二轮有，且 `emotion` 记录只含派生字段。

- [ ] **Step 4: 交付**

列出本计划涉及的全部改动文件与测试输出，交给用户提交；不执行 `git commit` / `git push`。
