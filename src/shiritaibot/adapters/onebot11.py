from __future__ import annotations

import asyncio
import base64
import html
import json
import random
import re
import time
from collections import deque
from time import monotonic
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from websockets.asyncio.client import connect

from shiritaibot.api.event import MessageEvent
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
from shiritaibot.core.text import clean_outbound
from shiritaibot.meme.inject import MemeInjector


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


def qq_download_url(url: str) -> bool:
    """Only QQ's own HTTPS CDNs; never protocol-side local paths or arbitrary hosts."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and any(
            host == d or host.endswith("." + d)
            for d in ("qq.com", "qq.com.cn", "qpic.cn", "gtimg.cn")
        )
    )


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
        self._friends: tuple[float, frozenset[str]] = (0.0, frozenset())
        # Auto-start bookkeeping: when the failure streak began and the next allowed attempt.
        self.failed_since: float | None = None
        self.next_auto_start = 0.0
        self.auto_process: asyncio.subprocess.Process | None = None

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

    async def friend_ids(self) -> frozenset[str]:
        """协议端好友列表，缓存 300 秒；拉取失败时沿用旧缓存。"""
        ts, ids = self._friends
        if monotonic() - ts >= 300:
            try:
                data = await self.call("get_friend_list", {})
                ids = frozenset(str(row.get("user_id")) for row in data or () if row.get("user_id"))
                self._friends = (monotonic(), ids)
            except Exception as exc:
                self.log.warning("获取好友列表失败，沿用缓存判断", exc=exc)
        return ids

    def invalidate_friends(self) -> None:
        self._friends = (0.0, frozenset())

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
        self.failed_since = None
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
                await self.try_auto_start()
            finally:
                self.ws, self.status = None, "disconnected"
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(PublicError("OneBot 连接断开"))
            await asyncio.sleep(delay + random.random())
            delay = min(60, delay * 2)

    async def try_auto_start(self) -> None:
        """Launch the backend command once the endpoint has been unreachable long enough."""
        command = self.config.auto_start
        if not command:
            return
        clock = time.monotonic()
        if self.failed_since is None:
            self.failed_since = clock
        if clock - self.failed_since < self.config.auto_start_after:
            return
        # At most one launch per five minutes: startup tools may take a while themselves.
        if clock < self.next_auto_start:
            return
        self.next_auto_start = clock + 300
        self.log.log(f"协议端已连续不可达，尝试自动启动：{command[0]}")
        try:
            self.auto_process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await self.store.activity("adapter.autostart", f"{self.config.id}: {command[0]}")
        except Exception as exc:
            # Usually a missing path or a launcher that needs elevation and cannot ask for it.
            self.log.warning(f"自动启动失败（{type(exc).__name__}）：{exc}")
            await self.store.activity(
                "adapter.autostart_failed", f"{self.config.id}: {type(exc).__name__}"
            )


class AdapterHub:
    def __init__(self, config, vault, store):
        self.config, self.vault, self.store = config, vault, store
        self.instances: dict[str, OneBot] = {}
        self.tasks: list[asyncio.Task] = []
        self.runtime: RuntimePort | None = None
        self.permissions: ApprovalPort | None = None
        # Friend / group requests become events so PE decides whether to tell the owner.
        self.emit: Callable[[ExternalEvent], Awaitable[str]] | None = None
        # shiritaibot.meme.MemeManager: collects group stickers, turns markers into stickers.
        self.memes: Any = None
        # shiritaibot.plugin.core.hooks.HookBus: plugin commands, listeners and send hooks.
        self.hooks: Any = None
        # Passive participation: last interject per session and a one-hour history.
        self.interject_at: dict[str, float] = {}
        self.interject_log: dict[str, deque[float]] = {}

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
        if (
            not group
            and user != cfg.owner_id
            and user not in cfg.friends
            and user not in await bot.friend_ids()
        ):
            bot.log.debug("忽略非好友私聊（不在协议端好友列表和 friends 白名单中）", speaker)
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
        if settings.wake_keyword_mode == "prefix":
            keyword = any(text.startswith(word) for word in settings.wake_keywords)
        else:
            keyword = any(word in text for word in settings.wake_keywords)
        message_id = f"{cfg.id}:{event.get('message_id')}"
        where = f"群 {target}" if group else "私聊"
        # Untriggered group chatter is only context, so it stays at debug level.
        (bot.log.log if not group or mentioned or keyword else bot.log.debug)(
            brief(describe(parts), 200) or "[空消息]", where, speaker
        )
        if group and self.memes is not None:
            self.memes.hooker.observe(session, parts)
        if group and settings.group_context.enabled:
            await self.store.record_group(
                session,
                message_id,
                user,
                str(sender.get("card") or sender.get("nickname") or user),
                describe(parts),
                mentioned or keyword,
            )

        def message(wake: bool) -> MessageEvent:
            return MessageEvent(
                session=session,
                text=text,
                sender_id=user,
                sender_name=str(sender.get("card") or sender.get("nickname") or ""),
                platform="qq",
                adapter=cfg.id,
                group_id=target if group else "",
                message_id=str(event.get("message_id", "")),
                is_owner=user == cfg.owner_id,
                is_wake=wake,
                segments=parts,
                raw=event,
            )

        if group and not (mentioned or keyword):
            if self.hooks is not None:
                # Plugin listeners also see group chatter that does not wake the bot.
                await self.hooks.dispatch(message(False))
            await self.maybe_interject(bot, session, user, speaker, describe(parts))
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
        # The model sees the keyword as typed; it is only peeled off so that
        # "小知，/stop" and the like still reach the command handlers.
        for word in sorted(settings.wake_keywords, key=len, reverse=True):
            if text.startswith(word):
                rest = text[len(word) :].lstrip(" ，,：:！!、")
                if (
                    rest.startswith("/")
                    or rest in {"停", "取消"}
                    or re.fullmatch(r"(同意|拒绝)\s+([a-f0-9]{10})", rest)
                ):
                    text = rest
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
        if self.hooks is not None and await self.hooks.dispatch(message(True)):
            return
        for part in parts:
            data = part.get("data", {})
            if part.get("type") == "file":
                ref = await file_ref(self.store, cfg.id, target if group else "", data, session)
                text += f"\n[QQ 文件引用 {ref}: {data.get('name') or data.get('file') or 'file'}]"
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

    async def maybe_interject(
        self, bot: OneBot, session: str, user: str, speaker: str, text: str
    ) -> None:
        """Occasionally let the Bot read untriggered group chatter and chime in."""
        # Imported lazily: proactive -> memory -> agent would cycle at module load.
        from shiritaibot.proactive import quiet_hours

        settings = self.config.value
        group = settings.group_context
        if not group.enabled or not group.interject or not text or self.runtime is None:
            return
        busy = getattr(self.runtime, "busy", None)
        if busy is not None and busy(session):
            return
        proactive = settings.proactive
        try:
            local = now().astimezone(ZoneInfo(proactive.timezone))
        except Exception:
            local = now().astimezone()
        if quiet_hours(proactive.quiet_start, proactive.quiet_end, local):
            return
        clock = asyncio.get_running_loop().time()
        if clock - self.interject_at.get(session, float("-inf")) < group.interject_cooldown:
            return
        recent = self.interject_log.setdefault(session, deque())
        while recent and clock - recent[0] > 3600:
            recent.popleft()
        if len(recent) >= group.interject_max_per_hour:
            return
        if random.random() >= group.interject_probability:
            return
        self.interject_at[session] = clock
        recent.append(clock)
        prompt = (
            "[群聊观察] 群里刚有人发言，这条没有 @ 你。\n"
            f"最新一条来自 {speaker}：{brief(text, 300)}\n"
            "能顺着话题自然接一句就写要发到群里的内容，否则只输出 <pass>。"
        )
        bot.log.debug(f"群聊观察：可能插话（{speaker}）")
        asyncio.create_task(self._interject(session, user, prompt))

    async def _interject(self, session: str, user: str, prompt: str) -> None:
        try:
            assert self.runtime is not None
            await self.runtime.submit(
                Context(session, user, "chat", group=True, interject=True), prompt
            )
        except Exception as exc:
            get_logger("群聊").debug(f"插话失败（{type(exc).__name__}）")

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

    async def enqueue(self, session: str, text: str, *, decorate: bool = True) -> None:
        if decorate and self.hooks is not None:
            decorated = await self.hooks.decorate(session, text)
            if decorated is None:
                return
            text = decorated
        await self._enqueue(session, text)
        if self.hooks is not None:
            self.hooks.sent(session, text)

    async def _enqueue(self, session: str, text: str) -> None:
        injector = self.memes.injector if self.memes is not None else None
        if session.startswith(("web:", "background:")):
            if injector is not None:
                text = injector.plain(text)
            await self.store.put("reply", uid(), {"text": text, "at": now().isoformat()}, session)
            return
        text = clean_outbound(text)
        # <meme> markers become their own sticker messages, splitting the text around them.
        pieces = injector.split(text) if injector is not None else [text]
        expires = now().timestamp() + 86400
        for piece in pieces:
            if not isinstance(piece, str):
                await self.store.put(
                    "outbox",
                    uid(),
                    {
                        "text": MemeInjector.label(piece),
                        "meme": piece["id"],
                        "status": "pending",
                        "expires": expires,
                    },
                    session,
                )
                continue
            for index in range(0, len(piece), 3500):
                await self.store.put(
                    "outbox",
                    uid(),
                    {"text": piece[index : index + 3500], "status": "pending", "expires": expires},
                    session,
                )

    async def outgoing(self, row: dict) -> list[dict] | None:
        """OneBot message segments for an outbox row; None when its sticker is gone."""
        if not row.get("meme"):
            return [{"type": "text", "data": {"text": row["text"]}}]
        data = await self.memes.core.image(row["meme"]) if self.memes is not None else None
        if data is None:
            return None
        return [
            {
                "type": "image",
                "data": {
                    "file": "base64://" + base64.b64encode(data).decode(),
                    "sub_type": 1,
                    "summary": "[动画表情]",
                },
            }
        ]

    async def notify_owner(self, text: str, *, handoff: bool = True) -> None:
        target = next((a for a in self.config.value.adapters if a.enabled and a.owner_id), None)
        session = f"private:{target.id}:{target.owner_id}" if target else "web:owner"
        if not handoff:
            # Approval prompts must be visible while the requesting turn waits for approval;
            # plugins cannot rewrite or cancel them.
            await self.enqueue(session, text, decorate=False)
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
                elif (message := await self.outgoing(row)) is None:
                    row["status"] = "failed"
                    get_logger(adapter_id).warning(f"表情 {row['meme']} 已被删除，跳过发送")
                else:
                    try:
                        receipt = await bot.call(
                            "send_group_msg" if kind == "group" else "send_private_msg",
                            {
                                "group_id" if kind == "group" else "user_id": int(target),
                                "message": message,
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
