from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

from shiritaibot.core import EventBus, canonical, now, uid

log = logging.getLogger("shiritaibot.activity")
GROUP_MESSAGES_KEPT = 2000


class Store:
    def __init__(self, home: Path, bus: EventBus):
        self.home, self.bus = home, bus
        self.lock = asyncio.Lock()
        self.db: aiosqlite.Connection

    async def open(self) -> None:
        await asyncio.to_thread(self.home.mkdir, parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.home / "state.sqlite3")
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        sql = await asyncio.to_thread(
            (Path(__file__).parent / "migrations/001.sql").read_text, encoding="utf-8"
        )
        await self.db.executescript(sql)
        await self.db.commit()
        for kind in ["turn", "subagent_run", "approval"]:
            while rows := await self.with_status(
                kind, ("running", "waiting_approval", "pending", "queued")
            ):
                for row in rows:
                    row["status"] = "interrupted"
                    await self.put(kind, row["id"], row, row.get("scope", ""))

    async def close(self) -> None:
        await self.db.close()

    async def execute(self, sql: str, args: tuple = ()) -> int:
        async with self.lock:
            cur = await self.db.execute(sql, args)
            await self.db.commit()
            return cur.rowcount

    async def query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        async with self.lock:
            async with self.db.execute(sql, args) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def put(self, kind: str, key: str, data: dict, scope: str = "") -> dict:
        stamp = now().timestamp()
        body = {**data, "id": key, "scope": scope}
        await self.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,scope=excluded.scope,updated=excluded.updated",
            (kind, key, scope, canonical(body), stamp, stamp),
        )
        self.bus.emit({"kind": kind, "id": key})
        return body

    async def insert(self, kind: str, key: str, data: dict, scope: str = "") -> bool:
        stamp = now().timestamp()
        return bool(
            await self.execute(
                "INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?)",
                (kind, key, scope, canonical({**data, "id": key, "scope": scope}), stamp, stamp),
            )
        )

    async def get(self, kind: str, key: str) -> dict | None:
        rows = await self.query("SELECT data FROM records WHERE kind=? AND id=?", (kind, key))
        return json.loads(rows[0]["data"]) if rows else None

    async def list(self, kind: str, scope: str | None = None, limit: int = 500) -> list[dict]:
        sql, args = "SELECT data FROM records WHERE kind=?", (kind,)
        if scope is not None:
            sql += " AND scope=?"
            args += (scope,)
        rows = await self.query(sql + " ORDER BY updated DESC LIMIT ?", (*args, limit))
        return [json.loads(row["data"]) for row in rows]

    async def delete(self, kind: str, key: str) -> None:
        await self.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, key))

    async def with_status(
        self, kind: str, statuses: tuple[str, ...], limit: int = 1000
    ) -> list[dict]:
        marks = ",".join("?" for _ in statuses)
        rows = await self.query(
            f"SELECT data FROM records WHERE kind=? AND json_extract(data,'$.status') IN ({marks}) ORDER BY created LIMIT ?",
            (kind, *statuses, limit),
        )
        return [json.loads(row["data"]) for row in rows]

    async def append(self, session: str, turn: str, message: dict) -> None:
        await self.execute(
            "INSERT INTO messages(session,turn,data,created) VALUES(?,?,?,?)",
            (session, turn, canonical(message), now().timestamp()),
        )

    async def history(
        self, session: str, limit: int = 200, *, include_commands: bool = True
    ) -> list[dict]:
        epoch = await self.get("session_epoch", session)
        condition = "" if include_commands else " AND COALESCE(json_extract(data,'$._command'),0)=0"
        rows = await self.query(
            "SELECT data FROM (SELECT seq,data FROM messages WHERE session=? AND seq>?"
            + condition
            + " ORDER BY seq DESC LIMIT ?) ORDER BY seq",
            (session, epoch["seq"] if epoch else 0, limit),
        )
        return [json.loads(row["data"]) for row in rows]

    async def reset_history(self, session: str) -> None:
        rows = await self.query(
            "SELECT COALESCE(MAX(seq),0) AS seq FROM messages WHERE session=?", (session,)
        )
        await self.put("session_epoch", session, {"seq": rows[0]["seq"]})
        await self.delete("context_state", session)

    async def record_group(
        self, session: str, message_id: str, sender: str, name: str, text: str, triggered: bool
    ) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO group_messages"
            "(session,message_id,sender,name,text,triggered,created) VALUES(?,?,?,?,?,?,?)",
            (session, message_id, sender, name, text, int(triggered), now().timestamp()),
        )
        # Bounded per-group buffer; the full chat log is not an archive target.
        await self.execute(
            "DELETE FROM group_messages WHERE session=? AND seq<=(SELECT seq FROM group_messages "
            "WHERE session=? ORDER BY seq DESC LIMIT 1 OFFSET ?)",
            (session, session, GROUP_MESSAGES_KEPT),
        )

    async def group_messages(
        self,
        session: str,
        limit: int,
        *,
        before_seq: int = 0,
        query: str = "",
        exclude: str = "",
    ) -> list[dict]:
        rows = await self.query(
            "SELECT seq,sender,name,text,triggered,created FROM group_messages WHERE session=? "
            "AND (?=0 OR seq<?) AND message_id!=? AND instr(lower(text),lower(?))>0 "
            "ORDER BY seq DESC LIMIT ?",
            (session, before_seq, before_seq, exclude, query, limit),
        )
        return rows[::-1]

    async def activity(self, kind: str, detail: str, scope: str = "") -> None:
        log.info("%s %s%s", kind, detail, f" [{scope}]" if scope else "")
        await self.put(
            "activity", uid(), {"kind": kind, "detail": detail, "at": now().isoformat()}, scope
        )
