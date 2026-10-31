from __future__ import annotations

import asyncio
import heapq
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import AwareDatetime, Field, model_validator

from shiritaibot.core import ExternalEvent, Model, PublicError, now, uid
from shiritaibot.core.logs import brief, get_logger

log = get_logger("日程")

KINDS = {"todo": "待办", "event": "日程", "reminder": "提醒", "job": "定时任务"}


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


# Fields a routine_update call may change; kind is fixed once created.
EDITABLE = ("title", "due", "end", "cron", "timezone")


def schedule(item: RoutineItem) -> float | None:
    """Next fire time: reminders at their due time, jobs at the next cron match."""
    if item.kind == "reminder" and item.due:
        return item.due.timestamp()
    if item.kind == "job":
        return (
            croniter(item.cron, now().astimezone(ZoneInfo(item.timezone)))
            .get_next(datetime)
            .timestamp()
        )
    return None


class RoutineManager:
    def __init__(self, store, emit):
        self.store, self.emit = store, emit
        self.wake = asyncio.Event()

    async def create(self, item: RoutineItem) -> dict:
        key = uid()
        next_fire = schedule(item)
        body = {
            **item.model_dump(mode="json"),
            "status": "scheduled" if next_fire else "open",
            "next_fire": next_fire,
        }
        result = await self.store.put("routine", key, body, "owner")
        log.log(
            f"新建{KINDS[item.kind]}「{brief(item.title, 60)}」"
            + (f"，cron {item.cron}" if item.cron else "")
            + (
                f"，时间 {item.due.astimezone(ZoneInfo(item.timezone)):%Y-%m-%d %H:%M}"
                if item.due
                else ""
            )
        )
        self.wake.set()
        return result

    async def update_status(self, key: str, status: str) -> dict:
        if status not in {"completed", "cancelled", "open"}:
            raise ValueError("invalid status")
        row = await self.store.get("routine", key)
        if not row:
            raise ValueError("routine not found")
        row.update(status=status, next_fire=None)
        log.log(
            f"{KINDS.get(row['kind'], row['kind'])}「{brief(row['title'], 60)}」状态改为 {status}"
        )
        self.wake.set()
        return await self.store.put("routine", key, row, "owner")

    async def update(self, key: str, changes: dict) -> dict:
        """Edit an item in place. Changing the time of a reminder or job reschedules it,
        unless it was completed or cancelled."""
        row = await self.store.get("routine", key)
        if not row:
            raise PublicError("日程不存在或已删除")
        if not changes or set(changes) - set(EDITABLE):
            raise PublicError("只能修改 " + "、".join(EDITABLE))
        current = {field: row.get(field) for field in ("kind", *EDITABLE)}
        try:
            item = RoutineItem.model_validate({**current, **changes})
        except ValueError:
            raise PublicError(
                "修改后的日程无效：时间须带时区，结束须晚于开始，定时任务须为五段 cron"
            ) from None
        row.update({k: v for k, v in item.model_dump(mode="json").items() if k in EDITABLE})
        if {"due", "cron", "timezone"} & set(changes) and row["status"] not in {
            "completed",
            "cancelled",
        }:
            next_fire = schedule(item)
            row.update(status="scheduled" if next_fire else "open", next_fire=next_fire)
        log.log(
            f"修改{KINDS.get(row['kind'], row['kind'])}「{brief(row['title'], 60)}」："
            + "、".join(sorted(changes))
        )
        self.wake.set()
        return await self.store.put("routine", key, row, "owner")

    async def delete(self, key: str) -> dict:
        row = await self.store.get("routine", key)
        if not row:
            raise PublicError("日程不存在或已删除")
        await self.store.delete("routine", key)
        log.log(f"删除{KINDS.get(row['kind'], row['kind'])}「{brief(row['title'], 60)}」")
        self.wake.set()
        return {"deleted": key, "kind": row["kind"], "title": row["title"]}

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
            log.log(
                f"{KINDS.get(row['kind'], row['kind'])}触发：{brief(row['title'], 80)}"
                + (f"（延迟 {delay:.0f} 秒）" if delay > 60 else "")
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
                log.error(f"日程调度出错（{type(exc).__name__}），5 秒后重试", exc=exc)
                await self.store.activity("scheduler.failed", type(exc).__name__)
                delay = 5
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
            except TimeoutError:
                pass
