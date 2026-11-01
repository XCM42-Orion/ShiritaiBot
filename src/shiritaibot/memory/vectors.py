"""Embedding index: float32 BLOBs in SQLite, brute-force cosine in numpy.

Memory items use mem_vector; other libraries (memes) pass their own table.
"""

from __future__ import annotations

import asyncio

import numpy as np

from shiritaibot.core import PublicError, fingerprint
from shiritaibot.core.logs import get_logger

log = get_logger("记忆")
BATCH = 32


TABLES = ("mem_vector", "meme_vector")


class VectorIndex:
    def __init__(self, store, llm, table: str = "mem_vector"):
        if table not in TABLES:
            raise ValueError("unknown vector table")
        self.store, self.llm, self.table = store, llm, table
        self.wake = asyncio.Event()
        # model_key -> (refs, normalized matrix); rebuilt lazily after writes.
        self.cache: dict[str, tuple[list[str], np.ndarray]] = {}

    def key(self) -> str | None:
        selected = self.llm.embedding_model() if self.llm else None
        return self.llm.embedding_key(selected[0]) if selected else None

    async def stale(self, documents: dict[str, str], key: str) -> dict[str, str]:
        rows = await self.store.query(f"SELECT id,hash FROM {self.table} WHERE model=?", (key,))
        known = {row["id"]: row["hash"] for row in rows}
        return {ref: text for ref, text in documents.items() if known.get(ref) != fingerprint(text)}

    async def sync(self, documents: dict[str, str]) -> int:
        """Embed documents whose text changed for the current model; returns how many."""
        key = self.key()
        if key is None:
            return 0
        pending = await self.stale(documents, key)
        done = 0
        items = list(pending.items())
        for offset in range(0, len(items), BATCH):
            batch = items[offset : offset + BATCH]
            used, vectors = await self.llm.embed([text for _, text in batch])
            if used != key:
                return done  # The configuration changed mid-run; the next pass restarts.
            async with self.store.lock:
                await self.store.db.executemany(
                    f"INSERT INTO {self.table} VALUES(?,?,?,?) ON CONFLICT(id,model) DO UPDATE "
                    "SET hash=excluded.hash,vec=excluded.vec",
                    [
                        (ref, key, fingerprint(text), np.asarray(vec, np.float32).tobytes())
                        for (ref, text), vec in zip(batch, vectors)
                    ],
                )
                await self.store.db.commit()
            done += len(batch)
            self.cache.pop(key, None)
        return done

    async def status(self, documents: dict[str, str]) -> dict:
        key = self.key()
        if key is None:
            return {"model": None, "indexed": 0, "pending": len(documents)}
        pending = len(await self.stale(documents, key))
        return {"model": key, "indexed": len(documents) - pending, "pending": pending}

    async def search(self, text: str, limit: int = 20) -> list[tuple[str, float]]:
        """(ref, cosine) best first; empty when embeddings are unavailable or fail."""
        key = self.key()
        if key is None:
            return []
        try:
            used, vectors = await self.llm.embed([text], query=True)
        except PublicError:
            log.warning(
                "Embedding 不可用，本次"
                + ("记忆" if self.table == "mem_vector" else "表情")
                + "检索只用关键词"
            )
            return []
        if used not in self.cache:
            rows = await self.store.query(f"SELECT id,vec FROM {self.table} WHERE model=?", (used,))
            self.cache[used] = await asyncio.to_thread(self._matrix, rows)
        refs, matrix = self.cache[used]
        if not refs:
            return []
        query = np.asarray(vectors[0], np.float32)
        if query.shape[0] != matrix.shape[1]:
            return []
        scores = await asyncio.to_thread(self._score, matrix, query)
        best = np.argsort(-scores)[:limit]
        return [(refs[i], float(scores[i])) for i in best]

    async def forget(self, ref: str) -> None:
        await self.store.execute(f"DELETE FROM {self.table} WHERE id=?", (ref,))
        self.cache.clear()

    @staticmethod
    def _matrix(rows: list[dict]) -> tuple[list[str], np.ndarray]:
        vectors = [np.frombuffer(row["vec"], np.float32) for row in rows]
        if not vectors:
            return [], np.zeros((0, 0), np.float32)
        width = max(len(v) for v in vectors)
        kept = [(row["id"], v) for row, v in zip(rows, vectors) if len(v) == width]
        matrix = np.stack([v for _, v in kept])
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12
        return [ref for ref, _ in kept], matrix

    @staticmethod
    def _score(matrix: np.ndarray, query: np.ndarray) -> np.ndarray:
        return matrix @ (query / (np.linalg.norm(query) + 1e-12))
