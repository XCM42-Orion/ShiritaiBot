from __future__ import annotations

import asyncio

import jieba

from shiritaibot.core import Context, PublicError, canonical, now, uid


class MemoryManager:
    def __init__(self, store):
        self.store = store

    async def add(
        self, ctx: Context, content: str, kind: str = "knowledge", visibility: str = "private"
    ) -> dict:
        if not ctx.owner or ctx.mode != "agent" or ctx.group or ctx.background:
            raise PublicError("当前上下文无权写入私人记忆")
        if visibility not in {"private", "public"}:
            raise PublicError("可见性只能是 private 或 public")
        key = uid()
        tokens = await asyncio.to_thread(lambda: " ".join(jieba.cut(content)))
        async with self.store.lock:
            try:
                await self.store.db.execute(
                    "INSERT INTO memory VALUES(?,?,?,?,?,?)",
                    (key, visibility, kind, content, "active", now().timestamp()),
                )
                await self.store.db.execute(
                    "INSERT INTO memory_fts(id,tokens) VALUES(?,?)", (key, tokens)
                )
                await self.store.db.commit()
            except BaseException:
                await self.store.db.rollback()
                raise
        return {"id": key, "content": content, "visibility": visibility, "kind": kind}

    async def search(self, ctx: Context, query: str) -> list[dict]:
        if not ctx.owner or ctx.mode != "agent" or ctx.group:
            return []
        tokens = await asyncio.to_thread(lambda: list(jieba.cut(query)))
        match = " OR ".join(
            '"' + word.replace('"', '""') + '"' for word in tokens[:32] if word.strip()
        )
        if not match:
            return []
        return await self.store.query(
            "SELECT m.* FROM memory m JOIN memory_fts f ON m.id=f.id WHERE memory_fts MATCH ? AND m.status='active' AND m.visibility IN ('private','public') ORDER BY bm25(memory_fts) LIMIT 8",
            (match,),
        )

    async def list(self) -> list[dict]:
        return await self.store.query("SELECT * FROM memory ORDER BY created DESC LIMIT 500")

    async def archive(self, key: str) -> None:
        await self.store.execute("UPDATE memory SET status='archived' WHERE id=?", (key,))


class ShortTermMemory:
    """Recent proactive episodes: what happened, what was decided and how far it got.

    Written by the proactive loop, read by the owner's main session; expires after `days`.
    """

    def __init__(self, store, days: int = 7):
        self.store, self.days = store, days

    async def record(self, key: str, stage: str, detail: str, **fields) -> dict:
        stamp = now().isoformat()
        row = await self.store.get("short_term", key) or {"steps": [], "created": stamp}
        row.update(fields, stage=stage, updated=stamp)
        row["steps"] = [*row["steps"], {"at": stamp, "stage": stage, "detail": detail[:1000]}]
        await self.store.execute(
            "DELETE FROM records WHERE kind='short_term' AND updated<?",
            (now().timestamp() - self.days * 86400,),
        )
        return await self.store.put("short_term", key, row, "owner")

    async def recent(self, query: str = "", limit: int = 20) -> list[dict]:
        rows = await self.store.list("short_term", scope="owner", limit=200)
        if query:
            rows = [r for r in rows if query.lower() in canonical(r).lower()]
        return rows[:limit]
