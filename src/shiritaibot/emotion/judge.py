"""Decision-model (Jev) emotion judgment.

System One classifies; it never generates text. Each emotion label is a noul
question, so one call returns a small label vector whose maximum becomes the
dominant affect. The text model is asked only for a short situation summary, and
only when the semantic signal is strong enough to matter.

Instructions are written in English (Jev's primary language); the state stays in
the original Chinese.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiritaibot.core import canonical
from shiritaibot.emotion.rules import Screen

ROUTES = ("light", "memory")
LABELS = ("frustrated", "anxious", "sad", "angry", "tired")
DOMINANT_FLOOR = 0.4
SITUATION_FLOOR = 0.2

SITUATION_PROMPT = (
    "根据用户最近的消息，用不超过 40 字概括对方眼下的近况或烦心事。只写概括，不抄原话，"
    "不写姓名、联系方式等隐私；没有值得记住的近况就输出空字符串。只输出概括本身。"
)

QUESTIONS = {
    "frustrated": {
        "type": "noul",
        "instructions": "How strongly does `message` show irritation or impatience?",
    },
    "anxious": {
        "type": "noul",
        "instructions": "How strongly does `message` show urgency or worry about something coming up?",
    },
    "sad": {
        "type": "noul",
        "instructions": "How strongly does `message` show low mood or distress about a personal hardship?",
    },
    "angry": {
        "type": "noul",
        "instructions": "How strongly does `message` show anger or outrage rather than mere irritation?",
    },
    "tired": {
        "type": "noul",
        "instructions": "How strongly does `message` show exhaustion, overload or burnout?",
    },
    "intensity": {
        "type": "noul",
        "instructions": "How strongly does `message` express the emotion?",
    },
    "impact": {
        "type": "noul",
        "instructions": "How much would what the user describes affect their life, "
        "even if said calmly?",
    },
}


@dataclass(frozen=True, slots=True)
class Judgment:
    affect: str
    intensity: float
    impact: float
    situation: str
    source: str

    @property
    def semantic(self) -> float:
        return 0.65 * self.intensity + 0.35 * self.impact


def _noul(answer) -> float:
    if not isinstance(answer, dict) or not isinstance(answer.get("noul"), (int, float)):
        raise ValueError("malformed noul")
    return min(1.0, max(0.0, float(answer["noul"])))


class EmotionJudge:
    def __init__(self, llm, config):
        self.llm, self.config = llm, config

    def _served(self, settings, route: str) -> bool:
        for model_id in settings.routes.get(route, []):
            model = next((m for m in settings.models if m.id == model_id and m.enabled), None)
            if model and any(
                p.id == model.provider and p.enabled and p.protocol == "systemone"
                for p in settings.providers
            ):
                return True
        return False

    def _route(self, settings) -> str | None:
        return next((route for route in ROUTES if self._served(settings, route)), None)

    def usable(self, settings) -> bool:
        return self._route(settings) is not None

    async def judge(
        self, settings, text: str, recent: list[str], screen: Screen
    ) -> Judgment | None:
        route = self._route(settings)
        if route is None:
            return None
        state = {
            "message": text[:2000],
            "recent": recent[-3:],
            "signals": list(screen.categories),
        }
        try:
            answers = await self.llm.decide(route, state, QUESTIONS, settings)
            scores = {label: _noul(answers[label]) for label in LABELS}
            intensity = _noul(answers["intensity"])
            impact = _noul(answers["impact"])
        except Exception:
            return None
        best = max(scores, key=lambda label: scores[label])
        affect = best if scores[best] >= DOMINANT_FLOOR else "calm"
        judgment = Judgment(affect, intensity, impact, "", "judge")
        if judgment.semantic < SITUATION_FLOOR:
            return judgment
        situation = await self._situation(settings, text, recent)
        return Judgment(affect, intensity, impact, situation, "judge")

    async def _situation(self, settings, text: str, recent: list[str]) -> str:
        if not settings.routes.get("light"):
            return ""
        message = canonical({"message": text[:2000], "recent": recent[-3:]})
        try:
            reply = await self.llm.complete(
                "light",
                SITUATION_PROMPT,
                [{"role": "user", "content": message}],
                [],
                settings=settings,
            )
        except Exception:
            return ""
        return reply.text.strip()[:40]
