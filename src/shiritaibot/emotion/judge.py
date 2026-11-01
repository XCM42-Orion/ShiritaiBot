"""Decision-model (Jev) emotion judgment.

System One classifies; it never generates text. `intensity` and `impact` are noul
questions, `affect` is a choice. The text model is asked only for a short situation
summary, and only when the light route has a text model. Any failure returns None so
the caller can fall back to rules.

Instructions are written in English (Jev's primary language); the state stays in the
original Chinese.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiritaibot.core import canonical
from shiritaibot.emotion.rules import Screen
from shiritaibot.emotion.state import threshold

ROUTES = ("light", "memory")
AFFECTS = ("frustrated", "anxious", "sad", "calm")

SITUATION_PROMPT = (
    "根据用户最近的消息，用不超过 40 字概括对方眼下的近况或烦心事。只写概括，不抄原话，"
    "不写姓名、联系方式等隐私；没有值得记住的近况就输出空字符串。只输出概括本身。"
)

QUESTIONS = {
    "affect": {
        "type": "choice",
        "instructions": "Which emotion does `message` most show?",
        "criteria": {
            "frustrated": "Irritated or impatient, often after repeated failure or not being understood.",
            "anxious": "Urgent or worried about something coming up.",
            "sad": "Low or distressed about a personal hardship or bad news.",
            "calm": "Neutral, ordinary or joking, with no notable emotion.",
        },
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


def _noul(answer) -> float:
    if not isinstance(answer, dict) or not isinstance(answer.get("noul"), (int, float)):
        raise ValueError("malformed noul")
    return min(1.0, max(0.0, float(answer["noul"])))


def _choice(answer) -> str:
    if not isinstance(answer, dict) or not isinstance(answer.get("choice"), str):
        raise ValueError("malformed choice")
    picked = answer["choice"]
    return picked if picked in AFFECTS else "calm"


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
            affect = _choice(answers["affect"])
            intensity = _noul(answers["intensity"])
            impact = _noul(answers["impact"])
        except Exception:
            return None
        if 0.65 * intensity + 0.35 * impact < threshold(settings.emotion):
            return Judgment(affect, intensity, impact, "", "judge")
        situation = await self._situation(settings, text, recent)
        return Judgment(affect, intensity, impact, situation, "judge")

    async def _situation(self, settings, text: str, recent: list[str]) -> str:
        if not settings.routes.get("light"):
            return ""
        message = canonical({"message": text[:2000], "recent": recent[-3:]})
        try:
            reply = await self.llm.complete(
                "light", SITUATION_PROMPT, [{"role": "user", "content": message}], [],
                settings=settings,
            )
        except Exception:
            return ""
        return reply.text.strip()[:40]
