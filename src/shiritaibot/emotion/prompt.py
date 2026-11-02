"""Prompt fragment for the current emotion value.

Rendered from a state snapshot at the start of a turn and injected into the user
message's `<context>`. The tone tier decides how far the wording may go; a group
value is atmosphere only and never names or cares for anyone publicly.
"""

from __future__ import annotations

EMOTIONS = {
    "frustrated": "烦躁",
    "anxious": "焦急",
    "sad": "低落",
    "angry": "生气",
    "tired": "疲惫",
}
CALM_FLOOR = 0.25


def _strength(value: float) -> str:
    if value >= 0.85:
        return "非常"
    return "" if value >= 0.6 else "有些"


def _age(at: float, updated: float) -> str:
    delta = max(0.0, at - updated)
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    return f"{int(delta // 86400)} 天前"


def render(
    *,
    group: bool,
    value: float,
    affect: str,
    situation: str,
    updated: float,
    tone: float,
    at: float,
) -> str:
    if value < CALM_FLOOR:
        return ""
    mood = _strength(value) + EMOTIONS.get(affect, "情绪波动")
    if group:
        if tone < 0.2 and value < 0.6:
            return ""
        lines = [
            "<emotion>",
            f"本群当前的气氛：{mood}。",
            "回复时整体语气放稳，先解决大家在问的事，少开玩笑。"
            "不要点名或评论任何人的状态，也不要公开表示关心。",
        ]
        if tone > 0.6:
            lines.append("可以简短表达理解，但不要单独关心某个人，也不要追问隐私。")
        lines.append("</emotion>")
        return "\n".join(lines)
    if tone < 0.2:
        if value < 0.6:
            return ""
        return "\n".join(["<emotion>", f"对方当前：{mood}。", "语气收敛，少开玩笑。", "</emotion>"])
    lines = [
        "<emotion>",
        f"对方当前：{mood}。",
        "语气放稳，先回应用户真正要处理的事。",
        "可以有一句简短的理解或确认，但不要展开安慰。",
    ]
    if tone > 0.6:
        lines.append(
            "如果合适，可以说一句真诚的关心，最多一句；"
            "不要每轮都关心，也不要让话题偏离开用户的问题。"
        )
    if situation and value >= 0.4:
        lines.append(
            f"此前提到：{situation}（{_age(at, updated)}）。可以自然接上，但不要主动追问细节。"
        )
    lines.append("</emotion>")
    return "\n".join(lines)
