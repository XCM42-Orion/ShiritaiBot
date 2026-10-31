from __future__ import annotations

import asyncio
import html
import json
import random
import re
from typing import Any, Awaitable, Callable

from websockets.asyncio.client import connect

from shiritaibot.core import (
    Actor,
    ApprovalPort,
    Context,
    ExternalEvent,
    PublicError,
    RuntimePort,
    canonical,
    fingerprint,
    now,
    uid,
)
from shiritaibot.core.logs import brief, get_logger


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


def describe(parts: list[dict]) -> str:
    """Readable plain text for group context, keeping placeholders for non-text segments."""
    labels = {"image": "[图片]", "face": "[表情]", "record": "[语音]", "video": "[视频]"}
    out = []
    for part in parts:
        data = part.get("data", {})
        kind = part.get("type")
        if kind == "text":
            out.append(str(data.get("text", "")))
        elif kind == "at":
            out.append(f"@{data.get('name') or data.get('qq', '')} ")
        elif kind == "file":
            out.append(f"[文件 {data.get('name', '')}]")
        elif kind == "forward":
            out.append(f"[合并转发 {data.get('id', '')}]")
        elif kind in labels:
            out.append(labels[kind])
    return "".join(out).strip()[:2000]


async def file_ref(store, adapter: str, group_id: str, data: dict, session: str) -> str:
    """A session-bound handle for qq_file_download; platform IDs never leave the store."""
    ref = uid()
    await store.put(
        "qq_file", ref, {"adapter": adapter, "group_id": group_id, "data": data}, session
    )
    return ref


class OneBot:
    def __init__(self, config, vault, store, sink):
        self.config, self.vault, self.store, self.sink = config, vault, store, sink
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.status = "disconnected"
        self.self_id = config.self_id
        self.events: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.log = get_logger(config.id)

    async def call(self, action: str, params: dict) -> Any:
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
                self.log.error(f"处理 OneBot 事件失败（{type(exc).__name__}）", exc=exc)
                await self.store.activity(
                    "adapter.event_failed", f"{self.config.id}: {type(exc).__name__}"
                )

    async def identify(self) -> None:
        result = await self.call("get_login_info", {})
        actual = str(result.get("user_id", ""))
        if self.config.self_id and actual != self.config.self_id:
            self.log.error(
                f"登录账号 {actual} 与配置的 self_id {self.config.self_id} 不一致，已断开"
            )
            if self.ws:
                await self.ws.close(code=1008, reason="self_id mismatch")
            return
        self.self_id = actual
        self.status = "connected"
        self.log.log(f"已连接 OneBot，登录账号 {result.get('nickname') or actual}（{actual}）")

    async def run(self) -> None:
        delay = 1
        warned = False
        while True:
            try:
                self.log.debug(f"正在连接 {self.config.url}")
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
                    delay, warned = 1, False
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
                                        self.log.warning("事件队列已满，丢弃一条 OneBot 事件")
                                        await self.store.activity(
                                            "adapter.overflow", self.config.id
                                        )
                        finally:
                            consumer.cancel()
                            identity.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Only the first failure of a reconnect streak is a warning; retries stay quiet.
                message = f"连接断开或无法连接（{type(exc).__name__}），约 {delay} 秒后重连"
                (self.log.debug if warned else self.log.warning)(message)
                warned = True
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
        # Friend / group requests become events so PE decides whether to tell the owner.
        self.emit: Callable[[ExternalEvent], Awaitable[str]] | None = None

    async def start(self) -> None:
        for cfg in self.config.value.adapters:
            if cfg.enabled:
                get_logger(cfg.id).log(f"启动协议端（OneBot 11 → {cfg.url}）")
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
        if event.get("post_type") == "request":
            await self.request(bot, event)
            return
        if event.get("post_type") != "message" or not bot.self_id:
            return
        user = str(event.get("user_id", ""))
        if user == bot.self_id or user in cfg.ignore_users:
            return
        group = event.get("message_type") == "group"
        target = str(event.get("group_id")) if group else user
        sender = event.get("sender") or {}
        speaker = f"{sender.get('card') or sender.get('nickname') or user}({user})"
        if group and target not in cfg.groups:
            bot.log.debug(f"忽略未启用群 {target} 的消息", speaker)
            return
        if not group and user != cfg.owner_id and user not in cfg.friends:
            bot.log.debug("忽略非好友名单用户的私聊", speaker)
            return
        session = f"{'group' if group else 'private'}:{cfg.id}:{target}"
        parts = segments(event.get("message", []))
        text = "".join(
            str(s.get("data", {}).get("text", "")) for s in parts if s.get("type") == "text"
        ).strip()
        mentioned = any(
            s.get("type") == "at" and str(s.get("data", {}).get("qq")) == bot.self_id for s in parts
        )
        settings = self.config.value
        keyword = any(word in text for word in settings.wake_keywords)
        message_id = f"{cfg.id}:{event.get('message_id')}"
        where = f"群 {target}" if group else "私聊"
        # Untriggered group chatter is only context, so it stays at debug level.
        (bot.log.log if not group or mentioned or keyword else bot.log.debug)(
            brief(describe(parts), 200) or "[空消息]", where, speaker
        )
        if group and settings.group_context.enabled:
            await self.store.record_group(
                session,
                message_id,
                user,
                str(sender.get("card") or sender.get("nickname") or user),
                describe(parts),
                mentioned or keyword,
            )
        if group and not (mentioned or keyword):
            return
        # Explicit controls and approvals must remain reachable while keyword gating is on.
        control = (
            text.startswith("/")
            or text in {"停", "取消"}
            or re.fullmatch(r"(同意|拒绝)\s+([a-f0-9]{10})", text)
        )
        if (
            not group
            and user == cfg.owner_id
            and settings.private_require_keyword
            and not keyword
            and not control
        ):
            bot.log.debug("私聊未包含唤醒词，不处理", speaker)
            return
        for word in sorted(settings.wake_keywords, key=len, reverse=True):
            if text.startswith(word):
                text = text[len(word) :].lstrip(" ，,：:！!、")
                break
        if not await self.store.execute(
            "INSERT OR IGNORE INTO seen_messages VALUES(?,?)", (message_id, now().timestamp())
        ):
            bot.log.debug("重复消息，已跳过", speaker)
            return
        if not group and user == cfg.owner_id:
            match = re.fullmatch(r"(同意|拒绝)\s+([a-f0-9]{10})", text)
            if match:
                try:
                    await self.permissions.resolve(match[2], match[1] == "同意")
                    bot.log.log(f"主人{match[1]}了审批 {match[2]}", where)
                    await self.enqueue(session, "审批已处理。")
                except PublicError as exc:
                    bot.log.warning(f"审批 {match[2]} 处理失败：{exc}", where)
                    await self.enqueue(session, str(exc))
                return
        for part in parts:
            data = part.get("data", {})
            if part.get("type") == "file":
                ref = await file_ref(self.store, cfg.id, target if group else "", data, session)
                text += f"\n[QQ 文件引用 {ref}: {data.get('name', 'file')}]"
            elif part.get("type") in {"image", "video"}:
                # Media-only messages must still start a turn; the CDN link becomes a
                # session-bound ref that qq_file_download can fetch.
                label = "图片" if part["type"] == "image" else "视频"
                if data.get("url"):
                    ref = await file_ref(self.store, cfg.id, target if group else "", data, session)
                    text += f"\n[QQ {label}引用 {ref}: {data.get('file') or label}]"
                else:
                    text += f"\n[QQ {label}，协议端未提供下载地址]"
            elif part.get("type") == "forward" and data.get("id"):
                text += f"\n[QQ 合并转发 {data['id']}]"
            elif part.get("type") == "reply" and data.get("id"):
                text = f"[引用 QQ 消息 {data['id']}] " + text
        text = text.strip()
        if text:
            owner = user == cfg.owner_id
            await self.runtime.submit(
                Context(session, user, "agent" if owner and not group else "chat", owner, group),
                text,
            )

    async def request(self, bot: OneBot, event: dict) -> None:
        kind, flag = event.get("request_type"), str(event.get("flag", ""))
        if kind not in {"friend", "group"} or not flag:
            return
        key = fingerprint([bot.config.id, flag])[:16]
        user, group = str(event.get("user_id", "")), str(event.get("group_id") or "")
        sub_type = str(event.get("sub_type", "")) if kind == "group" else ""
        title = (
            f"QQ 好友申请：{user}"
            if kind == "friend"
            else f"QQ 群邀请：{user} 邀请加入群 {group}"
            if sub_type == "invite"
            else f"QQ 入群申请：{user} 申请加入群 {group}"
        )
        record = {
            "adapter": bot.config.id,
            "kind": kind,
            "sub_type": sub_type,
            "user_id": user,
            "group_id": group,
            "comment": str(event.get("comment", ""))[:500],
            "flag": flag,
            "status": "pending",
            "at": now().isoformat(),
        }
        if not await self.store.insert("qq_request", key, record):
            return
        bot.log.log(f"{title}（{brief(record['comment'], 80) or '无附言'}）")
        if self.emit is not None:
            await self.emit(
                ExternalEvent(
                    source="qq",
                    kind=f"qq.request.{kind}",
                    dedup_key=key,
                    title=title,
                    payload={
                        "request": key,
                        "comment": record["comment"],
                        "group_id": group,
                        "how_to_handle": "用 qq_request_handle 处理该 request",
                    },
                    actors=[Actor(identity=f"qq:{user}", role="requester")] if user else [],
                )
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

    async def notify_owner(self, text: str, *, handoff: bool = True) -> None:
        target = next((a for a in self.config.value.adapters if a.enabled and a.owner_id), None)
        session = f"private:{target.id}:{target.owner_id}" if target else "web:owner"
        if not handoff:
            # Approval prompts must be visible while the requesting turn waits for approval.
            await self.enqueue(session, text)
        elif self.runtime is not None:
            await self.runtime.handoff(session, text, self.enqueue)
        else:
            await self.enqueue(session, text)
            await self.store.append(
                session, "handoff", {"role": "assistant", "content": text, "_proactive": True}
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
                    get_logger(adapter_id).warning(
                        f"消息超过 24 小时仍未发出，已放弃：{brief(row['text'], 60)}"
                    )
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
                        bot.log.debug(
                            f"已发送：{brief(row['text'], 80)}",
                            f"→ {'群' if kind == 'group' else '私聊'} {target}",
                        )
                        if kind == "group" and self.config.value.group_context.enabled:
                            await self.store.record_group(
                                row["scope"],
                                f"{adapter_id}:self:{row['id']}",
                                bot.self_id,
                                self.config.value.assistant_name,
                                row["text"],
                                False,
                            )
                    except Exception as exc:
                        # An unacknowledged send may have succeeded. Do not automatically duplicate it.
                        row["status"] = "uncertain"
                        bot.log.warning(
                            f"发送未确认（{type(exc).__name__}），为避免重复不再重试："
                            f"{brief(row['text'], 60)}",
                            f"→ {'群' if kind == 'group' else '私聊'} {target}",
                        )
                    await asyncio.sleep(0.7)
                await self.store.put("outbox", row["id"], row, row["scope"])
            await asyncio.sleep(1)
