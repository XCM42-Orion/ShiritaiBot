"""Daily memory upkeep (docs/MEMORY.md §10 P3). Nothing is ever hard-deleted."""

from __future__ import annotations

import asyncio
from datetime import date

from shiritaibot.core import ExternalEvent, now
from shiritaibot.core.logs import get_logger
from shiritaibot.memory.longterm import MemoryManager, ref

log = get_logger("记忆")
DAY = 86400
# A name seen fewer than three times and not for this long was probably a passer-by.
CANDIDATE_IDLE = 90 * DAY
# Conflicts no conversation has touched for this long are asked about in a digest.
CONFLICT_IDLE = 14 * DAY


class MemoryMaintenance:
    def __init__(self, memory: MemoryManager, store, emit):
        self.memory, self.store, self.emit = memory, store, emit

    async def tick(self, *, force: bool = False) -> dict:
        last = await self.store.get("memory_maintenance", "last")
        stamp = now().timestamp()
        if not force and last and stamp - last["at"] < DAY:
            return {}
        result = {
            "archived": await self.archive_candidates(stamp),
            "conflicts": await self.remind_conflicts(stamp),
        }
        await self.memory.judge.prune()
        await self.store.put("memory_maintenance", "last", {"at": stamp, **result})
        if any(result.values()):
            log.log(
                f"记忆整理：归档 {result['archived']} 个久未出现的新名字，"
                f"汇总 {result['conflicts']} 个待确认问题"
            )
        return result

    async def archive_candidates(self, stamp: float) -> int:
        """Archive (not forget) candidates nobody mentioned again; mentions can revive them."""
        rows = await self.memory.rows(
            "SELECT id FROM mem_entity WHERE status='candidate' AND seen_count<3 "
            "AND COALESCE(last_seen,created)<?",
            (stamp - CANDIDATE_IDLE,),
        )
        if not rows:
            return 0
        keys = [row["id"] for row in rows]
        marks = ",".join("?" * len(keys))
        await self.store.execute(
            f"UPDATE mem_entity SET status='archived',updated=? WHERE id IN ({marks})",
            (stamp, *keys),
        )
        await self.memory.load_aliases()
        await self.memory.index([ref("e", key) for key in keys])
        return len(keys)

    async def remind_conflicts(self, stamp: float) -> int:
        """Hand long-unasked conflicts to the Proactive Engine as one low-priority digest."""
        rows = await self.memory.rows(
            "SELECT * FROM mem_conflict WHERE status='open' AND created<? "
            "AND (asked_at IS NULL OR asked_at<?) ORDER BY id LIMIT 10",
            (stamp - CONFLICT_IDLE, stamp - CONFLICT_IDLE),
        )
        if not rows:
            return 0
        await self.emit(
            ExternalEvent(
                source="memory",
                kind="memory.conflict",
                dedup_key=f"conflicts:{date.today().isoformat()}",
                title=f"记忆里有 {len(rows)} 个问题需要主人确认",
                payload={
                    "how": "在消息里顺带问主人；得到答复后调用 memory_resolve 记录",
                    "questions": [{"id": ref("c", row["id"]), "note": row["note"]} for row in rows],
                },
                urgency="low",
            )
        )
        marks = ",".join("?" * len(rows))
        await self.store.execute(
            f"UPDATE mem_conflict SET asked_at=? WHERE id IN ({marks})",
            (stamp, *(row["id"] for row in rows)),
        )
        return len(rows)

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:
                log.error(f"记忆整理出错（{type(exc).__name__}）", exc=exc)
            await asyncio.sleep(3600)
