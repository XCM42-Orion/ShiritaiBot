"""Emotion state: scope keys, the activation threshold, decay, and fusion.

Values are stored per scope in the store's `emotion` kind (docs/superpowers/specs/
2026-11-01-emotion-design.md): the owner, one per private user, and one per group.
"""

from __future__ import annotations

from shiritaibot.config import EmotionConfig
from shiritaibot.core import Context

KIND = "emotion"
HALF_LIFE = 6 * 3600


def scope_keys(ctx: Context) -> list[str]:
    if ctx.group:
        return [ctx.session, "owner"] if ctx.owner else [ctx.session]
    if ctx.owner:
        return ["owner"]
    return ["user:" + ctx.session.removeprefix("private:")]


def read_key(ctx: Context) -> str:
    if ctx.group:
        return ctx.session
    return scope_keys(ctx)[0]


def threshold(cfg: EmotionConfig) -> float:
    return 0.85 - 0.7 * cfg.sensitivity


def decay(value: float, updated: float, persistence: float, at: float) -> float:
    half_life = HALF_LIFE * 2 ** (4.8 * persistence)
    return value * 0.5 ** ((at - updated) / half_life)


def merge(
    previous: dict | None,
    raw: float,
    *,
    affect: str,
    impact: float,
    situation: str,
    source: str,
    cfg: EmotionConfig,
    at: float,
) -> dict | None:
    decayed = (
        decay(
            float(previous.get("value", 0.0)),
            float(previous.get("updated", 0.0)),
            cfg.persistence,
            at,
        )
        if previous
        else 0.0
    )
    if raw < threshold(cfg):
        if previous is None:
            return None
        return {**previous, "value": decayed, "updated": at}
    kept = previous.get("situation", "") if previous else ""
    return {
        "value": min(1.0, max(0.0, 0.6 * decayed + 0.4 * raw)),
        "affect": affect or (previous.get("affect", "") if previous else ""),
        "impact": impact,
        "situation": situation if situation and raw >= decayed else kept,
        "updated": at,
        "source": source,
    }
