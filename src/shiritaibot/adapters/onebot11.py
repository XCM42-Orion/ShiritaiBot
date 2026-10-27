from __future__ import annotations

import asyncio
import html
import json
import random
import re

from websockets.asyncio.client import connect

from shiritaibot.core import ApprovalPort, Context, PublicError, RuntimePort, canonical, now, uid


def segments(value) -> list[dict]:
    if isinstance(value, list):
        return value
    result, end = [], 0
    for match in re.finditer(r"\[CQ:([a-z_]+)((?:,[^\]]*)?)\]", str(value)):
        if match.start() > end:
            result.append(
                {"type": "text", "data": {"text": html.unescape(value[end : match.start()])}}
            )
        data = dict(part.split("=", 1) for part in match[2].lstrip(",").split(",") if "=" in part)
        result.append({"type": match[1], "data": {k: html.unescape(v) for k, v in data.items()}})
        end = match.end()
    if end < len(value):
        result.append({"type": "text", "data": {"text": html.unescape(value[end:])}})
    return result


class OneBot:
    def __init__(self, config, vault, store, sink):
        self.config, self.vault, self.store, self.sink = config, vault, store, sink
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.status = "disconnected"
        self.self_id = config.self_id
        self.events: asyncio.Queue = asyncio.Queue(maxsize=256)

    async def call(self, action: str, params: dict) -> dict:
        if not self.ws:
            raise PublicError("OneBot 未连接")
        key = uid()
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.ws.send(canonical({"action": action, "params": params, "echo": key}))
            async with asyncio.timeout(30):
                result = await future
            if result.get("status") != "ok" or result.get("retcode", 0) != 0:
                raise PublicError(f"OneBot 操作失败 (retcode={result.get('retcode')})")
            return result.get("data") or {}
        finally:
            self.pending.pop(key, None)

    async def consume(self) -> None:
        while True:
            data = await self.events.get()
            try:
                await self.sink(self, data)
            except Exception as exc:
                await self.store.activity(
                    "adapter.event_failed", f"{self.config.id}: {type(exc).__name__}"
                )

    async def identify(self) -> None:
        result = await self.call("get_login_info", {})
        actual = str(result.get("user_id", ""))
        if self.config.self_id and actual != self.config.self_id:
            if self.ws:
                await self.ws.close(code=1008, reason="self_id mismatch")
            return
        self.self_id = actual
        self.status = "connected"

    async def run(self) -> None:
        delay = 1
        while True:
            try:
                token = await self.vault.resolve(self.config.token)
                async with connect(
                    self.config.url,
                    additional_headers={"Authorization": f"Bearer {token}"} if token else {},
                    max_size=2**20,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    self.ws = ws
                    self.status = "connecting"
                    delay = 1
                    async with asyncio.TaskGroup() as group:
                        consumer = group.create_task(self.consume())
                        identity = group.create_task(self.identify())
                        try:
                            async for raw in ws:
                                data = json.loads(raw)
                                key = str(data.get("echo", ""))
                                if key in self.pending:
                                    future = self.pending[key]
                                    if not future.done():
                                        future.set_result(data)
                                elif data.get("post_type"):
                                    try:
                                        self.events.put_nowait(data)
                                    except asyncio.QueueFull:
                                        await self.store.activity(
                                            "adapter.overflow", self.config.id
                                        )
                        finally:
                            consumer.cancel()
                            identity.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.store.activity(
                    "adapter.disconnected", f"{self.config.id}: {type(exc).__name__}"
                )
            finally:
                self.ws, self.status = None, "disconnected"
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(PublicError("OneBot 连接断开"))
            await asyncio.sleep(delay + random.random())
            delay = min(60, delay * 2)


class AdapterHub:
    def __init__(self, config, vault, store):
        self.config, self.vault, self.store = config, vault, store
        self.instances: dict[str, OneBot] = {}
        self.tasks: list[asyncio.Task] = []
        self.runtime: RuntimePort | None = None
        self.permissions: ApprovalPort | None = None

    async def start(self) -> None:
        for cfg in self.config.value.adapters:
            if cfg.enabled:
                bot = OneBot(cfg, self.vault, self.store, self.receive)
                self.instances[cfg.id] = bot
                self.tasks.append(asyncio.create_task(bot.run()))
        self.tasks.append(asyncio.create_task(self.flush()))

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.instances.clear()

    async def receive(self, bot: OneBot, event: dict) -> None:
        assert self.runtime is not None and self.permissions is not None
        cfg = bot.config
        if event.get("post_type") != "message" or not bot.self_id:
            return
        user = str(event.get("user_id", ""))
        if user == bot.self_id or user in cfg.ignore_users:
            return
        group = event.get("message_type") == "group"
        target = str(event.get("group_id")) if group else user
        if group and target not in cfg.groups:
            return
        if not group and user != cfg.owner_id and user not in cfg.friends:
            return
        session = f"{'group' if group else 'private'}:{cfg.id}:{target}"
        parts = segments(event.get("message", []))
        text = "".join(
            str(s.get("data", {}).get("text", "")) for s in parts if s.get("type") == "text"
        ).strip()
        if group and not any(
            s.get("type") == "at" and str(s.get("data", {}).get("qq")) == bot.self_id for s in parts
        ):
            return
        message_id = f"{cfg.id}:{event.get('message_id')}"
        if not await self.store.execute(
            "INSERT OR IGNORE INTO seen_messages VALUES(?,?)", (message_id, now().timestamp())
        ):
            return
        if not group and user == cfg.owner_id:
            match = re.fullmatch(r"(同意|拒绝)\s+([a-f0-9]{10})", text)
            if match:
                try:
                    await self.permissions.resolve(match[2], match[1] == "同意")
                    await self.enqueue(session, "审批已处理。")
                except PublicError as exc:
                    await self.enqueue(session, str(exc))
                return
        for part in parts:
            if part.get("type") == "file":
                ref = uid()
                await self.store.put(
                    "qq_file",
                    ref,
                    {"adapter": cfg.id, "group_id": target if group else "", "data": part["data"]},
                    session,
                )
                text += f"\n[QQ 文件引用 {ref}: {part['data'].get('name', 'file')}]"
        if text:
            owner = user == cfg.owner_id
            await self.runtime.submit(
                Context(session, user, "agent" if owner and not group else "chat", owner, group),
                text,
            )

    async def enqueue(self, session: str, text: str) -> None:
        if session.startswith(("web:", "background:")):
            await self.store.put("reply", uid(), {"text": text, "at": now().isoformat()}, session)
            return
        for index in range(0, len(text), 3500):
            await self.store.put(
                "outbox",
                uid(),
                {
                    "text": text[index : index + 3500],
                    "status": "pending",
                    "expires": now().timestamp() + 86400,
                },
                session,
            )

    async def notify_owner(self, text: str) -> None:
        target = next((a for a in self.config.value.adapters if a.enabled and a.owner_id), None)
        session = f"private:{target.id}:{target.owner_id}" if target else "web:owner"
        await self.enqueue(session, text)
        await self.store.append(
            session, "handoff", {"role": "user", "content": "[后台交接：已通知主人] " + text}
        )

    async def flush(self) -> None:
        while True:
            for row in await self.store.with_status("outbox", ("pending",)):
                if row["status"] != "pending":
                    continue
                kind, adapter_id, target = row["scope"].split(":", 2)
                bot = self.instances.get(adapter_id)
                if row["expires"] < now().timestamp():
                    row["status"] = "expired"
                elif not bot or bot.status != "connected":
                    continue
                else:
                    try:
                        receipt = await bot.call(
                            "send_group_msg" if kind == "group" else "send_private_msg",
                            {
                                "group_id" if kind == "group" else "user_id": int(target),
                                "message": [{"type": "text", "data": {"text": row["text"]}}],
                            },
                        )
                        row.update(status="sent", receipt=receipt)
                    except Exception:
                        # An unacknowledged send may have succeeded. Do not automatically duplicate it.
                        row["status"] = "uncertain"
                    await asyncio.sleep(0.7)
                await self.store.put("outbox", row["id"], row, row["scope"])
            await asyncio.sleep(1)
