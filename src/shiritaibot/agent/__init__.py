from __future__ import annotations

import asyncio
from collections import Counter, deque
from dataclasses import replace

from shiritaibot.agent.commands import command
from shiritaibot.agent.context import ContextManager, context_chars
from shiritaibot.core import Context, PublicError, canonical, fingerprint, now, uid


def repair_history(messages: list[dict], max_chars: int | None) -> list[dict]:
    """Keep complete request/result pairs, including after interrupted turns."""
    result, pending = [], {}
    for message in messages:
        if message.get("_command"):
            continue
        if message["role"] != "tool" and pending:
            result.extend(
                {
                    "role": "tool",
                    "tool_call_id": key,
                    "content": '{"error":"上次执行中断，结果未知，请先核查副作用"}',
                }
                for key in pending
            )
            pending.clear()
        if message["role"] == "tool":
            if message["tool_call_id"] not in pending:
                continue
            pending.pop(message["tool_call_id"])
        result.append(message)
        for call in message.get("tool_calls", []):
            pending[call["id"]] = True
    result.extend(
        {"role": "tool", "tool_call_id": key, "content": '{"error":"上次执行中断，结果未知"}'}
        for key in pending
    )
    # Trim at user boundaries; never send an orphan tool result to a provider.
    while max_chars is not None and len(canonical(result)) > max_chars:
        index = next((i for i, m in enumerate(result[1:], 1) if m["role"] == "user"), None)
        if index is None:
            break
        result = result[index:]
    while result and result[0]["role"] != "user" and not result[0].get("_proactive"):
        result.pop(0)
    if result and result[0].get("_proactive"):
        result.insert(0, {"role": "user", "content": "[上下文：以下是已向用户发送的主动通知]"})
    return result


class SessionActor:
    def __init__(self, runtime, session: str):
        self.runtime, self.session = runtime, session
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=5)
        self.steering: deque[str] = deque(maxlen=10)
        self.current: asyncio.Task | None = None
        self.context: Context | None = None
        self.task = asyncio.create_task(self.run(), name=f"session:{session}")

    async def submit(self, ctx: Context, text: str) -> str:
        if text.strip() in {"/stop", "/cancel", "停", "取消"}:
            if self.current and self.context and (ctx.owner or ctx.actor == self.context.actor):
                self.current.cancel()
                self.steering.clear()
            # Also discard queued work when no turn has started yet.
            retained = []
            while not self.queue.empty():
                item = self.queue.get_nowait()
                if not ctx.owner and item[0].actor != ctx.actor:
                    retained.append(item)
            for item in retained:
                self.queue.put_nowait(item)
            return "cancelled"
        if (
            self.current
            and not self.current.done()
            and self.context
            and ctx.actor == self.context.actor
            and ctx.mode == "agent"
        ):
            if len(self.steering) == self.steering.maxlen:
                raise PublicError("插话队列已满")
            self.steering.append(text)
            return "steering"
        try:
            self.queue.put_nowait((ctx, text))
        except asyncio.QueueFull:
            raise PublicError("会话队列已满，请稍后重试") from None
        return "queued"

    async def run(self) -> None:
        try:
            while True:
                try:
                    ctx, text = await asyncio.wait_for(self.queue.get(), timeout=1800)
                except TimeoutError:
                    return
                self.context = ctx
                self.current = asyncio.create_task(self.runtime.turn(self, ctx, text))
                try:
                    await self.current
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    if task and task.cancelling():
                        raise
                except Exception as exc:
                    await self.runtime.store.activity(
                        "actor.failed", type(exc).__name__, self.session
                    )
                finally:
                    self.current = None
                if self.steering:
                    followup = "\n".join(self.steering)
                    self.steering.clear()
                    if not self.queue.full():
                        self.queue.put_nowait((ctx, followup))
        finally:
            if self.current:
                self.current.cancel()
                await asyncio.gather(self.current, return_exceptions=True)


class AgentRuntime:
    def __init__(self, config, store, llm, registry, memory, deliver, short_term=None):
        self.config, self.store, self.llm = config, store, llm
        self.registry, self.memory, self.deliver = registry, memory, deliver
        self.short_term = short_term
        self.actors: dict[str, SessionActor] = {}
        self.submit_locks: dict[str, asyncio.Lock] = {}
        self.context_locks: dict[str, asyncio.Lock] = {}

    async def handoff(self, session: str, text: str, send) -> None:
        # Append between turns, never between an assistant tool call and its result.
        async with self.context_locks.setdefault(session, asyncio.Lock()):
            await send(session, text)
            await self.store.append(
                session, "handoff", {"role": "assistant", "content": text, "_proactive": True}
            )

    async def submit(self, ctx: Context, text: str) -> str:
        async with self.submit_locks.setdefault(ctx.session, asyncio.Lock()):
            return await self._submit(ctx, text)

    async def _submit(self, ctx: Context, text: str) -> str:
        await self.store.put("session", ctx.session, {"mode": ctx.mode, "actor": ctx.actor})
        if text.strip().startswith("/"):
            return await command(self, ctx, text)
        if ctx.session not in self.actors or self.actors[ctx.session].task.done():
            actor = SessionActor(self, ctx.session)
            self.actors[ctx.session] = actor

            def cleanup(task):
                if self.actors.get(ctx.session) is actor:
                    self.actors.pop(ctx.session, None)

            actor.task.add_done_callback(cleanup)
        await self.store.put("session", ctx.session, {"mode": ctx.mode, "actor": ctx.actor})
        return await self.actors[ctx.session].submit(ctx, text)

    async def close(self) -> None:
        for actor in self.actors.values():
            actor.task.cancel()
        await asyncio.gather(*(a.task for a in self.actors.values()), return_exceptions=True)

    async def turn(self, actor: SessionActor, ctx: Context, text: str) -> None:
        async with self.context_locks.setdefault(ctx.session, asyncio.Lock()):
            await self._turn(actor, ctx, text)

    async def _turn(self, actor: SessionActor, ctx: Context, text: str) -> None:
        ctx = replace(ctx, turn=uid())
        cfg = self.config.value.model_copy(deep=True)
        selected_persona = await self.store.get("session_persona", ctx.session)
        system = cfg.system_prompt(
            selected_persona.get("persona", "") if selected_persona else "",
            owner=ctx.owner,
        )
        selected = await self.store.get("session_model", ctx.session)
        if selected and selected.get("model"):
            cfg.routes["agent" if ctx.mode == "agent" else "chat"] = [selected["model"]]
        body = {
            "status": "running",
            "actor": ctx.actor,
            "started": now().isoformat(),
            "steps": 0,
            "memory_scope": "owner"
            if ctx.owner and ctx.mode == "agent" and not ctx.group
            else ctx.session,
            "loop": "proactive" if ctx.background else "main",
            "assistant_name": cfg.assistant_name,
            "owner_name": cfg.owner_name,
        }
        await self.store.put("turn", ctx.turn, body, ctx.session)
        try:
            async with asyncio.timeout(cfg.policy.max_turn_seconds):
                # Chat never calls the private memory interface, even for the owner in a group.
                memory = (
                    await self.memory.search(ctx, text)
                    if ctx.owner and ctx.mode == "agent" and not ctx.group
                    else []
                )
                if self.short_term and ctx.owner and ctx.mode == "agent" and not ctx.group:
                    memory += await self.short_term.recent(limit=5)
                user = {
                    "role": "user",
                    "_speaker": cfg.owner_name if ctx.owner else ctx.actor,
                    "_original_content": text,
                    "content": text
                    + f"\n<context time='{now().isoformat()}' timezone='{cfg.proactive.timezone}'>\n"
                    + canonical(memory)
                    + "\n</context>",
                }
                # Persist the original input even if context compression fails.
                await self.store.append(ctx.session, ctx.turn, user)
                history = repair_history(
                    await ContextManager(self.store, self.llm).prepare(
                        ctx.session,
                        cfg,
                        user,
                        owner=ctx.owner,
                        current_turn=ctx.turn,
                    ),
                    None,
                )
                history.append(user)
                tools = self.registry.specs(ctx)
                repetitions: Counter = Counter()
                for step in range(cfg.policy.max_steps if ctx.mode == "agent" else 3):
                    while actor.steering:
                        steering = actor.steering.popleft()
                        item = {
                            "role": "user",
                            "content": "[用户在你工作时追加] " + steering,
                            "_original_content": steering,
                            "_speaker": cfg.owner_name if ctx.owner else ctx.actor,
                        }
                        await self.store.append(ctx.session, ctx.turn, item)
                        history.append(item)
                    if context_chars(history) > cfg.policy.max_context_chars * 2:
                        raise PublicError("本轮上下文达到限制，请开启新会话或减少输入")
                    reply = await self.llm.complete(
                        "agent" if ctx.mode == "agent" else "chat",
                        system,
                        history,
                        tools,
                        settings=cfg,
                    )
                    message = {
                        "role": "assistant",
                        "content": reply.text,
                        "_opaque": reply.opaque,
                        "_speaker": cfg.assistant_name,
                    }
                    if reply.calls:
                        message["tool_calls"] = reply.calls
                    await self.store.append(ctx.session, ctx.turn, message)
                    history.append(message)
                    if not reply.calls:
                        await self.deliver(ctx.session, reply.text or "模型没有返回可显示的内容。")
                        body["status"] = "completed"
                        return
                    # Main-loop text is visible before tools run; proactive turns
                    # deliver only their final structured notification decision.
                    if not ctx.background and reply.text.strip():
                        await self.deliver(ctx.session, reply.text)
                    for call in reply.calls:
                        function = call["function"]
                        sig = fingerprint(function)
                        repetitions[sig] += 1
                        if repetitions[sig] >= 5:
                            raise PublicError("重复调用同一工具已达上限")
                        output = await self.registry.execute(
                            ctx, function["name"], function["arguments"]
                        )
                        result = {"role": "tool", "tool_call_id": call["id"], "content": output}
                        await self.store.append(ctx.session, ctx.turn, result)
                        history.append(result)
                    body["steps"] = step + 1
                    await self.store.put("turn", ctx.turn, body, ctx.session)
                raise PublicError("已达到本轮步数限制")
        except asyncio.CancelledError:
            body["status"] = "cancelled"
            raise
        except Exception as exc:
            body["status"] = "failed"
            error = (
                str(exc) if isinstance(exc, PublicError) else f"本轮执行失败 ({type(exc).__name__})"
            )
            await self.store.append(ctx.session, ctx.turn, {"role": "assistant", "content": error})
            await self.deliver(ctx.session, error)
        finally:
            body["finished"] = now().isoformat()
            await self.store.put("turn", ctx.turn, body, ctx.session)
            if self.short_term:
                await self.short_term.enqueue(ctx, body["status"])
                body["summary_enqueued"] = True
                await self.store.put("turn", ctx.turn, body, ctx.session)
