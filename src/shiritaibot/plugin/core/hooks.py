"""Dispatch of v2 plugin handlers: commands, message listeners and pipeline hooks.

Handlers run in the host process without the per-plugin reload lock, so a slow tool call
never blocks message hooks; a handler whose plugin was reloaded or stopped is skipped.
Failures and timeouts are logged and never break the pipeline that called the hook.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import re
import shlex
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from shiritaibot.agent.commands import COMMANDS
from shiritaibot.api.event import (
    MessageEvent,
    MessageResult,
    OutgoingMessage,
    ProactiveEvent,
    ProactiveOutcome,
    Triage,
)
from shiritaibot.api.hook import FILTERS, HANDLERS, MessageType, Names, Permission, Platform
from shiritaibot.core import PublicError, uid
from shiritaibot.core.logs import brief, get_logger

log = get_logger("插件")

HOOK_TIMEOUT = 10
LISTENER_TIMEOUT = 30
PROACTIVE_TIMEOUT = 30
COMMAND_TIMEOUT = 600
BUILTIN_COMMANDS = frozenset({*COMMANDS, "cancel", "reset"})
CONTROL = re.compile(r"(停|取消|(同意|拒绝)\s+[a-f0-9]{10})")


class UsageError(ValueError):
    pass


@dataclass(eq=False)
class Handler:
    plugin: str
    owner: object  # The plugin instance; stale once the plugin is reloaded.
    kind: str
    name: str  # Method name, for logs and the plugin listing.
    func: Callable
    priority: int
    order: int
    options: dict
    path: tuple[Names, ...] = ()
    types: MessageType = MessageType.ALL
    platforms: Platform = Platform.ALL
    permission: Permission = Permission.EVERYONE
    regex: re.Pattern | None = None
    description: str = ""

    @property
    def command(self) -> str:
        return "/" + " ".join(names.primary for names in self.path)


def builtin_control(text: str) -> bool:
    """Built-in commands and approval words always reach the host untouched."""
    text = text.strip()
    if CONTROL.fullmatch(text):
        return True
    if text.startswith("/"):
        name = text[1:].split(maxsplit=1)
        return bool(name) and name[0].lower() in BUILTIN_COMMANDS
    return False


def _hints(func: Callable) -> dict:
    try:
        return inspect.get_annotations(func, eval_str=True)
    except Exception:
        return {}


def _convert(token: str, hint) -> Any:
    if hint is bool:
        lowered = token.lower()
        if lowered in {"1", "true", "yes", "on", "是", "开"}:
            return True
        if lowered in {"0", "false", "no", "off", "否", "关"}:
            return False
        raise ValueError(token)
    if hint in (int, float):
        return hint(token)
    return token


def command_params(func: Callable) -> list[inspect.Parameter]:
    return list(inspect.signature(func).parameters.values())[1:]  # After ``event``.


def usage(handler: Handler) -> str:
    parts = [handler.command]
    hints = _hints(handler.func)
    for param in command_params(handler.func):
        if param.kind is param.VAR_POSITIONAL:
            parts.append(f"[{param.name}...]")
        elif param.kind is param.POSITIONAL_OR_KEYWORD:
            hint = hints.get(param.name)
            label = param.name + (f":{hint.__name__}" if hint in (int, float, bool) else "")
            parts.append(f"<{label}>" if param.default is param.empty else f"[{label}]")
    return " ".join(parts)


def bind_arguments(func: Callable, text: str) -> list:
    """Positional values for ``func(event, *values)`` from the text after the command."""
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    hints = _hints(func)
    params = command_params(func)
    positional = [p for p in params if p.kind is p.POSITIONAL_OR_KEYWORD]
    greedy = not any(p.kind is p.VAR_POSITIONAL for p in params)
    values: list = []
    for index, param in enumerate(params):
        hint = hints.get(param.name, str)
        if param.kind is param.VAR_POSITIONAL:
            values.extend(_convert(token, hint) for token in tokens[index:])
            break
        if param.kind is not param.POSITIONAL_OR_KEYWORD:
            break
        if index >= len(tokens):
            if param.default is param.empty:
                raise UsageError(param.name)
            break
        if greedy and param is positional[-1] and hint is str and len(tokens) > index + 1:
            values.append(" ".join(tokens[index:]))  # The last text parameter takes the rest.
            break
        try:
            values.append(_convert(tokens[index], hint))
        except ValueError:
            raise UsageError(param.name) from None
    return values


def collect(plugin: str, instance: object, counter: Callable[[], int]) -> list[Handler]:
    """Handlers declared with ``hook`` decorators on the plugin class (subclasses override)."""
    members: dict[str, Any] = {}
    for klass in reversed(type(instance).__mro__):
        for name, value in vars(klass).items():
            function = getattr(value, "__func__", value)
            if callable(function) and HANDLERS in getattr(function, "__dict__", {}):
                members[name] = function
            else:
                members.pop(name, None)
    result = []
    for name, function in members.items():
        registrations = function.__dict__[HANDLERS]
        filters = function.__dict__.get(FILTERS, {})
        # message_type / regex stacked on a command narrow it instead of adding a listener.
        has_command = any(r.kind == "command" for r in registrations)
        bound = getattr(instance, name)
        for registration in registrations:
            if registration.kind == "message" and has_command:
                continue
            result.append(
                Handler(
                    plugin=plugin,
                    owner=instance,
                    kind=registration.kind,
                    name=name,
                    func=bound,
                    priority=registration.priority,
                    order=counter(),
                    options=registration.options,
                    path=registration.options.get("path", ()),
                    types=filters.get("types", MessageType.ALL),
                    platforms=filters.get("platforms", Platform.ALL),
                    permission=filters.get("permission", Permission.EVERYONE),
                    regex=filters.get("regex"),
                    description=(inspect.getdoc(function) or "").split("\n", 1)[0],
                )
            )
    return result


class HookBus:
    def __init__(self, store, alive: Callable[[Handler], bool]):
        self.store = store
        self.alive = alive
        self.handlers: dict[str, list[Handler]] = {}
        self.tasks: set[asyncio.Task] = set()
        self.counter = itertools.count().__next__
        # (session, text): set by the plugin manager to the adapter send queue.
        self.send: Callable[[str, str], Awaitable[None]] | None = None

    # Registry -----------------------------------------------------------------------

    def set(self, plugin: str, handlers: list[Handler]) -> None:
        self.handlers[plugin] = [h for h in handlers if h.kind != "llm_tool"]

    def remove(self, plugin: str) -> None:
        self.handlers.pop(plugin, None)

    def of(self, kind: str) -> list[Handler]:
        found = [h for hs in self.handlers.values() for h in hs if h.kind == kind]
        return sorted((h for h in found if self.alive(h)), key=lambda h: (-h.priority, h.order))

    def has(self, kind: str) -> bool:
        return any(h.kind == kind for hs in self.handlers.values() for h in hs)

    def command_conflicts(self, plugin: str, handlers: list[Handler]) -> list[str]:
        others = {
            name
            for key, hs in self.handlers.items()
            if key != plugin
            for h in hs
            if h.kind == "command"
            for name in h.path[0]
        }
        clashes = set()
        for handler in handlers:
            if handler.kind == "command":
                clashes |= handler.path[0] & (BUILTIN_COMMANDS | others)
        return sorted(clashes)

    def help_lines(self, owner: bool) -> list[str]:
        return [
            f"{h.command} — {h.description or '插件指令'}（{h.plugin}）"
            for h in self.of("command")
            if owner or h.permission is not Permission.OWNER
        ]

    def describe(self, plugin: str) -> tuple[list[dict], list[dict]]:
        """(commands, hooks) of a plugin for the WebUI listing."""
        commands, hooks = [], []
        for handler in self.handlers.get(plugin, []):
            if handler.kind == "command":
                commands.append(
                    {
                        "command": usage(handler),
                        "description": handler.description,
                        "owner_only": handler.permission is Permission.OWNER,
                    }
                )
            else:
                hooks.append(
                    {"event": handler.kind, "handler": handler.name, "priority": handler.priority}
                )
        return commands, hooks

    # Invocation ---------------------------------------------------------------------

    def spawn(self, work: Awaitable) -> None:
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _failed(self, handler: Handler, exc: BaseException) -> None:
        what = "超时" if isinstance(exc, TimeoutError) else f"出错（{type(exc).__name__}）"
        log.warning(
            f"插件 {handler.plugin} 的 {handler.kind} 处理器 {handler.name} {what}",
            exc=None if isinstance(exc, (TimeoutError, PublicError)) else exc,
        )
        await self.store.activity(
            "plugin.hook_failed", f"{handler.plugin}.{handler.name}: {type(exc).__name__}"
        )

    async def invoke(self, handler: Handler, seconds: float, *args) -> bool:
        if not self.alive(handler):
            return False
        try:
            async with asyncio.timeout(seconds):
                result = handler.func(*args)
                if inspect.isawaitable(result):
                    await result
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._failed(handler, exc)
            return False

    async def call(self, kind: str, *args, seconds: float = HOOK_TIMEOUT) -> None:
        for handler in self.of(kind):
            await self.invoke(handler, seconds, *args)

    # Messages -----------------------------------------------------------------------

    def matches(self, handler: Handler, event: MessageEvent) -> bool:
        kind = MessageType.GROUP if event.is_group else MessageType.PRIVATE
        platform = Platform.WEB if event.platform == "web" else Platform.QQ
        return bool(kind & handler.types) and bool(platform & handler.platforms)

    def match_command(self, text: str) -> tuple[Handler, str] | None:
        tokens = text[1:].split()
        best = None
        for handler in self.of("command"):
            path = handler.path
            if len(tokens) >= len(path) and all(tokens[i] in path[i] for i in range(len(path))):
                if best is None or len(path) > len(best.path):
                    best = handler
        if best is None:
            return None
        parts = text[1:].split(maxsplit=len(best.path))
        return best, parts[len(best.path)] if len(parts) > len(best.path) else ""

    def group_help(self, text: str, event: MessageEvent) -> str:
        tokens = text[1:].split()
        lines = [
            f"{usage(h)} — {h.description}" if h.description else usage(h)
            for h in self.of("command")
            if tokens
            and len(h.path) > 1
            and tokens[0] in h.path[0]
            and self.matches(h, event)
            and (event.is_owner or h.permission is not Permission.OWNER)
        ]
        return "可用的子指令：\n" + "\n".join(lines) if lines else ""

    async def emit_result(self, event: MessageEvent, item: Any) -> None:
        if item is None:
            return
        await event.send(item if isinstance(item, MessageResult) else str(item))

    async def run_handler(self, handler: Handler, event: MessageEvent, args: list) -> None:
        result = handler.func(event, *args)
        if inspect.isasyncgen(result):
            async for item in result:
                await self.emit_result(event, item)
        else:
            if inspect.isawaitable(result):
                result = await result
            await self.emit_result(event, result)

    async def run_command(self, handler: Handler, event: MessageEvent, args: list) -> None:
        if not self.alive(handler):
            return
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await self.run_handler(handler, event, args)
        except asyncio.CancelledError:
            raise
        except PublicError as exc:
            await event.send(str(exc))
        except Exception as exc:
            await self._failed(handler, exc)
            await event.send(f"插件指令执行失败（{type(exc).__name__}）")

    def bind_sender(self, event: MessageEvent, record: str | None = None) -> None:
        async def sender(text: str) -> None:
            if self.send is None:
                raise RuntimeError("plugin send queue is not available")
            if record:
                await self.store.append(
                    event.session, record, {"role": "assistant", "content": text, "_command": True}
                )
            await self.send(event.session, text)

        event._sender = sender

    async def dispatch(self, event: MessageEvent) -> bool:
        """Run plugin commands and listeners; True when the message must not reach the model."""
        if builtin_control(event.text):
            return False
        if event.is_wake and event.text.startswith("/"):
            found = self.match_command(event.text)
            if found and self.matches(found[0], event):
                handler, rest = found
                turn = uid()
                await self.store.append(
                    event.session, turn, {"role": "user", "content": event.text, "_command": True}
                )
                self.bind_sender(event, turn)
                event.stop_event()
                log.log(
                    f"{'主人' if event.is_owner else event.sender_id} 执行插件指令 "
                    f"{brief(event.text, 80)}（{handler.plugin}）"
                )
                if handler.permission is Permission.OWNER and not event.is_owner:
                    await event.send("此指令仅限主人使用。")
                    return True
                try:
                    args = bind_arguments(handler.func, rest)
                except UsageError:
                    await event.send("用法：" + usage(handler))
                    return True
                # Commands may run long; the adapter keeps receiving meanwhile.
                self.spawn(self.run_command(handler, event, args))
                return True
            if found is None and (help_text := self.group_help(event.text, event)):
                self.bind_sender(event)
                await event.send(help_text)
                event.stop_event()
                return True
        self.bind_sender(event)
        for handler in self.of("message"):
            if not self.matches(handler, event):
                continue
            if handler.permission is Permission.OWNER and not event.is_owner:
                continue
            if handler.regex is not None and not handler.regex.search(event.text):
                continue
            if not self.alive(handler):
                continue
            try:
                async with asyncio.timeout(LISTENER_TIMEOUT):
                    await self.run_handler(handler, event, [])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._failed(handler, exc)
            if event.is_stopped:
                log.debug(f"消息被插件 {handler.plugin} 拦截：{brief(event.text, 60)}")
                return True
        return False

    # Outgoing messages ----------------------------------------------------------------

    async def decorate(self, session: str, text: str) -> str | None:
        """Text after on_decorating_result hooks; None when a hook cancelled the message."""
        handlers = self.of("decorating_result")
        if not handlers:
            return text
        message = OutgoingMessage(session, text)
        for handler in handlers:
            before = message.text
            await self.invoke(handler, HOOK_TIMEOUT, message)
            if not isinstance(message.text, str):
                message.text = before
            if message.cancelled:
                log.debug(f"插件 {handler.plugin} 取消了发往 {session} 的消息")
                return None
        return message.text if message.text.strip() else None

    def sent(self, session: str, text: str) -> None:
        if self.has("after_message_sent"):
            self.spawn(self.call("after_message_sent", OutgoingMessage(session, text)))

    # Proactive engine -----------------------------------------------------------------

    @staticmethod
    def view(key: str, event: dict, protected: bool) -> ProactiveEvent:
        return ProactiveEvent(
            id=key,
            source=event["source"],
            kind=event["kind"],
            title=event["title"],
            payload=deepcopy(event.get("payload") or {}),
            urgency=event.get("urgency", "normal"),
            occurred_at=str(event.get("occurred_at", "")),
            actors=deepcopy(event.get("actors") or []),
            protected=protected,
        )

    async def proactive_event(self, view: ProactiveEvent) -> ProactiveEvent:
        protected = view.protected
        for handler in self.of("proactive_event"):
            await self.invoke(handler, PROACTIVE_TIMEOUT, view)
            if view.dropped:
                if protected:
                    log.warning(f"插件 {handler.plugin} 试图丢弃主人设定或委派的事件，已忽略")
                    view.dropped, view.drop_reason = False, ""
                else:
                    view.drop_reason = view.drop_reason or f"插件 {handler.plugin} 丢弃"
                    break
        return view

    async def proactive_judged(self, view: ProactiveEvent, decision):
        """The Decision after on_proactive_judged hooks; invalid edits are reverted per plugin."""
        handlers = self.of("proactive_judged")
        if not handlers:
            return decision
        for handler in handlers:
            triage = Triage(
                importance=decision.importance,
                decision=decision.decision,
                message=decision.message,
                reason=decision.reason,
                prepare=decision.prepare,
            )
            if not await self.invoke(handler, PROACTIVE_TIMEOUT, view, triage):
                continue
            try:
                decision = type(decision).model_validate(
                    {
                        **decision.model_dump(),
                        "importance": triage.importance,
                        "decision": triage.decision,
                        "message": triage.message,
                        "reason": triage.reason,
                        "prepare": triage.prepare,
                    }
                )
            except ValueError:
                log.warning(f"插件 {handler.plugin} 修改的分诊结果无效，已忽略")
        return decision

    async def proactive_done(self, view: ProactiveEvent, outcome: ProactiveOutcome):
        for handler in self.of("proactive_done"):
            before = (outcome.notify, outcome.message, outcome.reason)
            if not await self.invoke(handler, PROACTIVE_TIMEOUT, view, outcome):
                outcome.notify, outcome.message, outcome.reason = before
                continue
            if (
                not isinstance(outcome.notify, bool)
                or not isinstance(outcome.message, str)
                or not 1 <= len(outcome.message.strip()) <= 4000
                or not isinstance(outcome.reason, str)
                or not 1 <= len(outcome.reason.strip()) <= 2000
            ):
                log.warning(f"插件 {handler.plugin} 修改的处理结论无效，已忽略")
                outcome.notify, outcome.message, outcome.reason = before
            if outcome.forced and not outcome.notify:
                log.warning(f"插件 {handler.plugin} 试图取消必须发送的通知，已忽略")
                outcome.notify = True
        return outcome

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
