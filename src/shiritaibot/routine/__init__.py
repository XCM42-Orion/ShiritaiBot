from __future__ import annotations

import asyncio
import heapq
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import AwareDatetime, Field, model_validator

from shiritaibot.core import ExternalEvent, Model, now, uid


class RoutineItem(Model):
    kind: str = Field(pattern=r"^(todo|event|reminder|job)$")
    title: str = Field(min_length=1, max_length=2000)
    due: AwareDatetime | None = None
    end: AwareDatetime | None = None
    cron: str = ""
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def valid(self):
        ZoneInfo(self.timezone)
        if self.kind == "job" and (not croniter.is_valid(self.cron) or len(self.cron.split()) != 5):
            raise ValueError("job requires a five-field cron expression")
        if self.kind in {"event", "reminder"} and not self.due:
            raise ValueError("due time with timezone required")
        if self.end and (not self.due or self.end <= self.due):
            raise ValueError("end must follow due")
        return self


class RoutineManager:
    def __init__(self, store, emit):
        self.store, self.emit = store, emit
        self.wake = asyncio.Event()

    async def create(self, item: RoutineItem) -> dict:
        key = uid()
        next_fire = item.due.timestamp() if item.kind == "reminder" and item.due else None
        if item.kind == "job":
            next_fire = (
                croniter(item.cron, now().astimezone(ZoneInfo(item.timezone)))
                .get_next(datetime)
                .timestamp()
            )
        body = {
            **item.model_dump(mode="json"),
            "status": "scheduled" if next_fire else "open",
            "next_fire": next_fire,
        }
        result = await self.store.put("routine", key, body, "owner")
        self.wake.set()
        return result

    async def update_status(self, key: str, status: str) -> dict:
        if status not in {"completed", "cancelled", "open"}:
            raise ValueError("invalid status")
        row = await self.store.get("routine", key)
        if not row:
            raise ValueError("routine not found")
        row.update(status=status, next_fire=None)
        self.wake.set()
        return await self.store.put("routine", key, row, "owner")

    async def tick(self) -> float:
        heap = [
            (r["next_fire"], r["id"], r)
            for r in await self.store.with_status("routine", ("scheduled",), limit=10000)
            if r.get("next_fire") is not None and r["status"] == "scheduled"
        ]
        heapq.heapify(heap)
        stamp = now().timestamp()
        while heap and heap[0][0] <= stamp:
            fire, key, row = heapq.heappop(heap)
            delay = stamp - fire
            event = ExternalEvent(
                source="routine",
                kind=f"routine.{row['kind']}",
                dedup_key=f"{key}:{fire}",
                title=("（延迟提醒）" if delay > 60 else "") + row["title"],
                payload={
                    "routine_id": key,
                    "scheduled_at": datetime.fromtimestamp(fire, UTC).isoformat(),
                    "late_seconds": delay,
                },
            )
            await self.emit(event)
            if row["kind"] == "job":
                row["next_fire"] = (
                    croniter(row["cron"], now().astimezone(ZoneInfo(row["timezone"])))
                    .get_next(datetime)
                    .timestamp()
                )
            else:
                row.update(status="fired", next_fire=None)
            await self.store.put("routine", key, row, "owner")
        return min(30, max(0.1, heap[0][0] - stamp)) if heap else 30

    async def run(self) -> None:
        while True:
            self.wake.clear()
            try:
                delay = await self.tick()
            except Exception as exc:
                await self.store.activity("scheduler.failed", type(exc).__name__)
                delay = 5
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
            except TimeoutError:
                pass
