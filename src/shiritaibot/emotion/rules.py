"""Layered rule gate in front of the emotion judge.

Three cheap local signals decide whether a message is worth a decision-model call
and feed the weighted fusion:

- stressor categories (appraisal): why the message reads as pressure;
- valence lexicon: how negative the wording is, with simple negation handling;
- structural triggers: repetition, tool failure, long first-person narrative.

Anything below the trigger line is ignored, so ordinary chatter costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiritaibot.memory.longterm import keywords, tokenize

CATEGORY_WEIGHTS = {
    "blocked": 0.5,
    "deadline": 0.5,
    "venting": 0.35,
    "repetition": 0.4,
    "failure": 0.45,
    "loss": 0.6,
    "narrative": 0.35,
}

# Phrases, never bare characters: 不是 / 马上 / 在吗 / 烦 / 累 / 病 are ordinary
# words in chat, and a single character also matches inside unrelated words
# (麻烦, 积累, 病句).
BLOCKED = (
    "不对",
    "又错",
    "又没",
    "又失败",
    "又不行",
    "又崩",
    "还是不行",
    "还是没",
    "还是不对",
    "怎么还",
    "没听懂",
    "没看懂",
    "理解错",
    "不是这个意思",
    "不是这样",
    "错了",
    "重来",
    "再来一次",
)
DEADLINE = (
    "快点",
    "赶紧",
    "尽快",
    "来不及",
    "立刻",
    "怎么还没",
    "急死",
    "急用",
    "着急",
    "很急",
    "没时间",
    "赶时间",
    "截止",
    "等不了",
    "现在就要",
)
CONSOLING = ("别着急", "不要着急", "不用着急", "别急", "不急", "慢慢来")
LOSS = (
    "很累",
    "好累",
    "太累",
    "心累",
    "累了",
    "撑不住",
    "顶不住",
    "受不了",
    "绷不住",
    "生病",
    "病了",
    "住院",
    "看病",
    "头疼",
    "心疼",
    "失眠",
    "熬夜",
    "通宵",
    "加班",
    "压力",
    "考试",
    "挂科",
    "分手",
    "吵架",
    "失业",
    "裁员",
    "被裁",
    "难过",
    "委屈",
    "想哭",
    "哭死",
    "难受",
    "焦虑",
    "抑郁",
    "绝望",
    "家里出了",
    "出事了",
    "去世",
)
VENTING = (
    "我操",
    "我靠",
    "卧槽",
    "特么",
    "妈的",
    "烦死",
    "烦透",
    "无语",
    "服了",
    "离谱",
    "破防",
    "崩溃",
    "麻了",
    "摆烂",
    "心塞",
    "气死",
)

NEGATIVE = {
    "崩溃": 1.0,
    "绝望": 1.0,
    "受不了": 1.0,
    "撑不住": 1.0,
    "哭死": 1.0,
    "想死": 1.0,
    "气死": 0.9,
    "分手": 0.9,
    "失业": 0.9,
    "挂科": 0.9,
    "出事": 0.9,
    "烦死": 0.8,
    "难受": 0.8,
    "难过": 0.8,
    "委屈": 0.8,
    "焦虑": 0.8,
    "失眠": 0.8,
    "生气": 0.8,
    "恶心": 0.8,
    "生病": 0.8,
    "病了": 0.8,
    "吵架": 0.8,
    "心累": 0.8,
    "累死": 0.8,
    "疼死": 0.8,
    "压力": 0.7,
    "失败": 0.7,
    "破防": 0.7,
    "好累": 0.7,
    "太累": 0.7,
    "很累": 0.7,
    "烦躁": 0.7,
    "心烦": 0.7,
    "郁闷": 0.7,
    "加班": 0.6,
    "心塞": 0.6,
    "头疼": 0.6,
    "心疼": 0.6,
    "累了": 0.6,
    "无语": 0.5,
    "离谱": 0.5,
    "麻了": 0.5,
    "服了": 0.4,
    "唉": 0.3,
}
NEGATORS = ("不", "没", "无", "未", "甭")
VALENCE_TRIGGER = 0.4

STRESSOR_AFFECT = {
    "blocked": "frustrated",
    "deadline": "anxious",
    "loss": "sad",
    "venting": "frustrated",
    "repetition": "frustrated",
    "failure": "frustrated",
    "narrative": "sad",
}


@dataclass(frozen=True, slots=True)
class Screen:
    categories: tuple[str, ...] = ()
    level: float = 0.0
    negativity: float = 0.0

    @property
    def hit(self) -> bool:
        return bool(self.categories) or self.negativity >= VALENCE_TRIGGER


def _has(words: tuple[str, ...], text: str) -> bool:
    return any(word in text for word in words)


async def _tokens(text: str) -> set[str]:
    return set(keywords(await tokenize(text)))


def _similar(left: set[str], right: set[str]) -> bool:
    if len(left) < 2 or len(right) < 2:
        return False
    return len(left & right) / len(left | right) >= 0.6


def valence(text: str) -> float:
    """Negative wording strength in 0..1, skipping negated mentions ("不累")."""
    taken: list[tuple[int, int]] = []
    total = 0.0
    for word, weight in sorted(NEGATIVE.items(), key=lambda item: -len(item[0])):
        start = 0
        while (index := text.find(word, start)) != -1:
            end = index + len(word)
            negated = any(n in text[max(0, index - 3) : index] for n in NEGATORS)
            if not negated and not any(index < right and left < end for left, right in taken):
                taken.append((index, end))
                total += weight
            start = end
    return min(1.0, total)


def expected_affect(hit: Screen) -> str:
    for name in hit.categories:
        if name in STRESSOR_AFFECT:
            return STRESSOR_AFFECT[name]
    return ""


async def screen(text: str, recent: list[str], failed: bool) -> Screen:
    found: set[str] = set()
    if _has(BLOCKED, text):
        found.add("blocked")
    if _has(DEADLINE, text) and not _has(CONSOLING, text):
        found.add("deadline")
    if text.count("!") + text.count("！") >= 2 or _has(VENTING, text):
        found.add("venting")
    recent = recent[-3:]
    if recent:
        left = await _tokens(text)
        for before in recent:
            if _similar(left, await _tokens(before)):
                found.add("repetition")
                break
    if failed:
        found.add("failure")
    if _has(LOSS, text):
        found.add("loss")
    if len(text) >= 150 and text.count("我") >= 3:
        found.add("narrative")
    categories = tuple(name for name in CATEGORY_WEIGHTS if name in found)
    level = min(1.0, sum(CATEGORY_WEIGHTS[name] for name in categories))
    return Screen(categories=categories, level=level, negativity=valence(text))
