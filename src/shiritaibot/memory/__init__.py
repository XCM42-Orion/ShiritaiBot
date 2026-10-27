from __future__ import annotations

import asyncio

import jieba

from shiritaibot.core import Context, PublicError, now, uid


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
