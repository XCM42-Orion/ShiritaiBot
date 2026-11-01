"""Rule gate in front of the emotion judge (docs/superpowers/specs/2026-11-01-emotion-design.md).

Cheap keyword and shape checks decide whether a message is worth a decision-model
call. A message that hits nothing is ignored, so ordinary chatter costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiritaibot.memory.longterm import keywords, tokenize

CATEGORY_WEIGHTS = {
    "correction": 0.5,
    "urgency": 0.5,
    "emphasis": 0.35,
    "repetition": 0.4,
    "failure": 0.45,
    "hardship": 0.6,
    "narrative": 0.35,
}

CORRECTION = (
    "不对", "不是", "又错", "又没", "又失败", "又不行", "又崩", "还是不行",
    "还是没", "还是不对", "怎么还", "没听懂", "没看懂", "理解错", "不是这个意思",
    "错了", "重来", "再来一次",
)
URGENCY = (
    "快点", "赶紧", "马上", "还在吗", "在吗", "尽快", "来不及", "立刻",
    "怎么还没", "急死", "急用", "着急", "很急", "没时间", "赶时间", "截止",
    "等不了", "现在就要",
)
CONSOLING = ("别着急", "不要着急", "不用着急", "别急", "不急", "慢慢来")
HARDSHIP = (
    "很累", "好累", "太累", "心累", "累了", "撑不住", "顶不住", "受不了", "绷不住",
    "生病", "病了", "住院", "看病", "头疼", "心疼", "失眠", "熬夜", "通宵",
    "加班", "压力", "考试", "挂科", "分手", "吵架", "失业", "裁员", "被裁",
    "难过", "委屈", "想哭", "哭死", "难受", "焦虑", "抑郁", "绝望",
    "家里出了", "出事了", "去世",
)
HEAVY = (
    "我操", "我靠", "卧槽", "特么", "妈的", "烦死", "烦透", "无语", "服了",
    "离谱", "破防", "崩溃", "麻了", "摆烂", "心塞", "气死",
)


@dataclass(frozen=True, slots=True)
class Screen:
    categories: tuple[str, ...] = ()
    level: float = 0.0

    @property
    def hit(self) -> bool:
        return bool(self.categories)


def _has(words: tuple[str, ...], text: str) -> bool:
    return any(word in text for word in words)


async def _similar(text: str, other: str) -> bool:
    left = set(keywords(await tokenize(text)))
    right = set(keywords(await tokenize(other)))
    if len(left) < 2 or len(right) < 2:
        return False
    return len(left & right) / len(left | right) >= 0.6


async def screen(text: str, recent: list[str], failed: bool) -> Screen:
    found: set[str] = set()
    if _has(CORRECTION, text):
        found.add("correction")
    if _has(URGENCY, text) and not _has(CONSOLING, text):
        found.add("urgency")
    if text.count("!") + text.count("！") >= 2 or _has(HEAVY, text):
        found.add("emphasis")
    if any([await _similar(text, before) for before in recent[-3:]]):
        found.add("repetition")
    if failed:
        found.add("failure")
    if _has(HARDSHIP, text):
        found.add("hardship")
    if len(text) >= 150 and text.count("我") >= 3:
        found.add("narrative")
    categories = tuple(name for name in CATEGORY_WEIGHTS if name in found)
    level = min(1.0, sum(CATEGORY_WEIGHTS[name] for name in categories))
    return Screen(categories=categories, level=level)
