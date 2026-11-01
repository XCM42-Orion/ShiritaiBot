"""Emotion indicator: rule gate, decision-model judgment, state, and prompt fragment.

A turn snapshots the state into its `<context>`, then schedules judgment in the
background, so a judgment only shows up from the next turn and never delays a reply.
Failures are swallowed: the bot answers exactly as it would without emotion.
"""

from __future__ import annotations

import asyncio
import json

from shiritaibot.core import Context, ExternalEvent, now
from shiritaibot.core.logs import brief, get_logger
from shiritaibot.emotion import rules, state
from shiritaibot.emotion.judge import EmotionJudge, Judgment
from shiritaibot.emotion.prompt import render

JUDGE_TIMEOUT = 30
CARE_KIND = "emotion_care"

log = get_logger("情绪")


class EmotionManager:
    def __init__(self, store, llm, config, emit=None):
        self.store, self.llm, self.config, self.emit = store, llm, config, emit
        self.tasks: set[asyncio.Task] = set()

    async def _signals(self, ctx: Context) -> tuple[list[str], bool]:
        history = await self.store.history(ctx.session, limit=12, include_commands=False)
        recent: list[str] = []
        if not ctx.group:
            recent = [
                message.get("_original_content") or message.get("content", "")
                for message in history
                if message.get("role") == "user"
            ][-3:]
        failed = False
        for message in history:
            if message.get("role") != "tool":
                continue
            try:
                body = json.loads(message.get("content", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(body, dict) and set(body) == {"error"}:
                failed = True
        return recent, failed

    async def observe(self, ctx: Context, text: str) -> None:
        if not self.config.value.emotion.enabled or ctx.background:
            return
        try:
            recent, failed = await self._signals(ctx)
        except Exception as exc:
            log.debug("情绪信号读取失败", exc=exc)
            recent, failed = [], False
        task = asyncio.create_task(self.process(ctx, text, recent, failed))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def process(
        self,
        ctx: Context,
        text: str,
        recent: list[str] | None = None,
        failed: bool | None = None,
    ) -> None:
        try:
            cfg = self.config.value.model_copy(deep=True)
            if not cfg.emotion.enabled:
                return
            if recent is None:
                recent, failed = await self._signals(ctx)
            hit = await rules.screen(text, recent, bool(failed))
            if not hit.hit:
                return
            judge = EmotionJudge(self.llm, self.config)
            try:
                async with asyncio.timeout(JUDGE_TIMEOUT):
                    judgment = await judge.judge(cfg, text, recent, hit)
            except Exception as exc:
                log.debug("情绪判定失败", exc=exc)
                return
            if judgment is None:
                if judge.usable(cfg):
                    return
                judgment = Judgment("", hit.level, 0.0, "", "rules")
            raw = 0.65 * judgment.intensity + 0.35 * judgment.impact
            at = now().timestamp()
            for key in state.scope_keys(ctx):
                previous = await self.store.get(state.KIND, key)
                updated = state.merge(
                    previous,
                    raw,
                    affect=judgment.affect,
                    impact=judgment.impact,
                    situation=judgment.situation,
                    source=judgment.source,
                    cfg=cfg.emotion,
                    at=at,
                )
                if updated is None:
                    continue
                await self.store.put(state.KIND, key, updated, ctx.session)
                if key == "owner":
                    await self._care(cfg, updated, at)
                if cfg.emotion.developer:
                    log.log(
                        f"情绪判定 {key}: value={updated['value']:.2f} "
                        f"source={updated['source']} affect={updated['affect']} "
                        f"categories={brief(list(hit.categories))}"
                    )
        except Exception as exc:
            log.debug("情绪处理失败", exc=exc)

    async def _care(self, cfg, updated: dict, at: float) -> None:
        if self.emit is None or cfg.emotion.initiative < 0.8 or updated["value"] < 0.75:
            return
        last = await self.store.get(CARE_KIND, "owner")
        if last and at - float(last.get("at", 0)) < cfg.proactive.throttle_seconds:
            return
        await self.emit(
            ExternalEvent(
                source="情绪",
                kind="care",
                dedup_key=f"emotion:{int(at // 600)}",
                title=f"主人最近的情绪状态不好（强度 {updated['value']:.2f}），"
                "可以主动私聊简单关心一句，不要追问隐私。",
                payload={"scope": "owner"},
                urgency="low",
            )
        )
        await self.store.put(CARE_KIND, "owner", {"at": at})

    async def context(self, ctx: Context, cfg) -> str:
        if not cfg.emotion.enabled:
            return ""
        try:
            row = await self.store.get(state.KIND, state.read_key(ctx))
        except Exception as exc:
            log.debug("情绪状态读取失败", exc=exc)
            return ""
        if not row:
            return ""
        at = now().timestamp()
        value = state.decay(
            float(row.get("value", 0.0)),
            float(row.get("updated", 0.0)),
            cfg.emotion.persistence,
            at,
        )
        return render(
            group=ctx.group,
            value=value,
            affect=row.get("affect", ""),
            situation=row.get("situation", ""),
            updated=float(row.get("updated", 0.0)),
            tone=cfg.emotion.tone,
            at=at,
        )

    async def close(self) -> None:
        tasks, self.tasks = list(self.tasks), set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
