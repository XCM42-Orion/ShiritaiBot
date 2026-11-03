"""Handler registration decorators (the counterpart of AstrBot's ``filter``).

Decorators only attach metadata to plugin methods; the host collects it when the plugin
loads. They can be stacked in any order. Registration decorators (``command``,
``message_type``, ``regex``, ``llm_tool`` and the ``on_*`` hooks) decide *when* a method
runs; ``permission`` and ``platform`` only narrow message handlers further.
"""

from __future__ import annotations

import enum
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Callable

HANDLERS = "__shiritai_handlers__"
FILTERS = "__shiritai_filters__"

# Lifecycle and pipeline hooks: decorator name → event name.
HOOK_EVENTS = (
    "loaded",
    "agent_begin",
    "llm_request",
    "llm_response",
    "tool_call",
    "tool_result",
    "agent_done",
    "decorating_result",
    "after_message_sent",
    "proactive_event",
    "proactive_judged",
    "proactive_done",
    "notification_sent",
)
TOOL_RISKS = {"safe", "normal", "sensitive", "dangerous"}
JSON_TYPES = {"string", "number", "integer", "boolean", "object", "array"}


class MessageType(enum.Flag):
    PRIVATE = enum.auto()
    GROUP = enum.auto()
    ALL = PRIVATE | GROUP


class Platform(enum.Flag):
    QQ = enum.auto()
    WEB = enum.auto()
    ALL = QQ | WEB


class Permission(enum.Enum):
    EVERYONE = "everyone"
    OWNER = "owner"


@dataclass
class Registration:
    kind: str  # "command" | "message" | "llm_tool" | one of HOOK_EVENTS
    priority: int = 0
    options: dict[str, Any] = field(default_factory=dict)


def _register(func: Callable, registration: Registration) -> Callable:
    target = getattr(func, "__func__", func)
    target.__dict__.setdefault(HANDLERS, []).append(registration)
    return func


def _filter(func: Callable, **values) -> Callable:
    target = getattr(func, "__func__", func)
    target.__dict__.setdefault(FILTERS, {}).update(values)
    return func


class Names(frozenset):
    """A command word and its aliases; ``primary`` is the name shown in help."""

    primary: str


def _names(name: str, aliases) -> Names:
    names = Names({name, *(aliases or ())})
    for item in names:
        if not isinstance(item, str) or not re.fullmatch(r"[^\s/]{1,64}", item):
            raise ValueError(f"invalid command name: {item!r}")
    names.primary = name
    return names


class CommandGroup:
    """Returned by ``hook.command_group``; sub-commands match ``/group sub args``."""

    def __init__(self, path: tuple[Names, ...], description: str = ""):
        self.path = path
        self.description = description

    def command(self, name: str, aliases=(), priority: int = 0) -> Callable[[Callable], Callable]:
        path = (*self.path, _names(name, aliases))
        return lambda func: _register(func, Registration("command", priority, {"path": path}))

    def group(self, name: str, aliases=()) -> Callable[[Callable], CommandGroup]:
        def decorate(func: Callable) -> CommandGroup:
            return CommandGroup((*self.path, _names(name, aliases)), inspect.getdoc(func) or "")

        return decorate


class _Hook:
    MessageType = MessageType
    Platform = Platform
    Permission = Permission

    # Message handling ---------------------------------------------------------------

    def command(self, name: str, aliases=(), priority: int = 0) -> Callable[[Callable], Callable]:
        """``/name arg1 arg2``; arguments bind to the handler's typed parameters."""
        path = (_names(name, aliases),)
        return lambda func: _register(func, Registration("command", priority, {"path": path}))

    def command_group(self, name: str, aliases=()) -> Callable[[Callable], CommandGroup]:
        return CommandGroup((), "").group(name, aliases)

    def message_type(self, types: MessageType = MessageType.ALL, priority: int = 0):
        """Listen to every message of these types (or narrow a command when stacked)."""

        def decorate(func):
            _filter(func, types=types)
            return _register(func, Registration("message", priority))

        return decorate

    def regex(self, pattern: str, priority: int = 0):
        """Listen to messages whose text matches ``pattern`` (``re.search``)."""
        compiled = re.compile(pattern)

        def decorate(func):
            _filter(func, regex=compiled)
            return _register(func, Registration("message", priority))

        return decorate

    def permission(self, level: Permission):
        return lambda func: _filter(func, permission=level)

    def platform(self, platforms: Platform):
        return lambda func: _filter(func, platforms=platforms)

    # Model tools ---------------------------------------------------------------------

    def llm_tool(
        self,
        name: str | None = None,
        *,
        risk: str,
        description: str | None = None,
        parameters: dict | None = None,
        chat: bool = False,
        timeout: int = 60,
    ):
        """Expose a method as a model tool: ``async def tool(self, ctx, **arguments)``.

        The schema comes from ``parameters`` when given, otherwise from the docstring's
        ``Args:`` block (``name(type): description``), otherwise from type hints.
        """
        if risk not in TOOL_RISKS:
            raise ValueError("tool risk must be safe, normal, sensitive or dangerous")

        def decorate(func):
            options = {
                "name": name or func.__name__,
                "risk": risk,
                "description": description,
                "parameters": parameters,
                "chat": chat,
                "timeout": timeout,
            }
            return _register(func, Registration("llm_tool", 0, options))

        return decorate

    # Lifecycle and pipeline hooks -----------------------------------------------------

    def _event(self, kind: str, priority: int = 0):
        return lambda func: _register(func, Registration(kind, priority))

    def on_loaded(self, priority: int = 0):
        """``(self)`` — after the host has started, or right after a later (re)load."""
        return self._event("loaded", priority)

    def on_agent_begin(self, priority: int = 0):
        """``(self, turn: Turn)``"""
        return self._event("agent_begin", priority)

    def on_llm_request(self, priority: int = 0):
        """``(self, turn: Turn, request: LLMRequest)`` — before every model request."""
        return self._event("llm_request", priority)

    def on_llm_response(self, priority: int = 0):
        """``(self, turn: Turn, response: LLMResponse)``"""
        return self._event("llm_response", priority)

    def on_tool_call(self, priority: int = 0):
        """``(self, turn: Turn, name: str, arguments: dict)`` — before the permission check."""
        return self._event("tool_call", priority)

    def on_tool_result(self, priority: int = 0):
        """``(self, turn: Turn, name: str, arguments: dict, result: str)``"""
        return self._event("tool_result", priority)

    def on_agent_done(self, priority: int = 0):
        """``(self, turn: Turn, result: AgentResult)``"""
        return self._event("agent_done", priority)

    def on_decorating_result(self, priority: int = 0):
        """``(self, message: OutgoingMessage)`` — rewrite or cancel text before it is queued."""
        return self._event("decorating_result", priority)

    def after_message_sent(self, priority: int = 0):
        """``(self, message: OutgoingMessage)`` — after the text entered the send queue."""
        return self._event("after_message_sent", priority)

    def on_proactive_event(self, priority: int = 0):
        """``(self, event: ProactiveEvent)`` — an event starts processing; may edit or drop."""
        return self._event("proactive_event", priority)

    def on_proactive_judged(self, priority: int = 0):
        """``(self, event: ProactiveEvent, triage: Triage)`` — adjust the triage decision."""
        return self._event("proactive_judged", priority)

    def on_proactive_done(self, priority: int = 0):
        """``(self, event: ProactiveEvent, outcome: ProactiveOutcome)`` — before notifying."""
        return self._event("proactive_done", priority)

    def on_notification_sent(self, priority: int = 0):
        """``(self, source: str, text: str)`` — after a proactive notification reached the owner."""
        return self._event("notification_sent", priority)


hook = _Hook()


# Tool schema from docstrings ----------------------------------------------------------

_HINT_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _json_type(raw: str) -> dict:
    raw = raw.strip().lower()
    match = re.fullmatch(r"array\[(\w+)\]", raw)
    if match:
        return {"type": "array", "items": _json_type(match[1])}
    if raw == "list":
        raw = "array"
    if raw == "dict":
        raw = "object"
    if raw not in JSON_TYPES:
        raise ValueError(f"unsupported tool parameter type: {raw}")
    return {"type": raw}


def _hint_type(hint) -> dict:
    origin = getattr(hint, "__origin__", hint)
    if origin in _HINT_TYPES:
        return {"type": _HINT_TYPES[origin]}
    if origin in (list, tuple):
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}
    return {}


def tool_spec(func: Callable, description: str | None, parameters: dict | None):
    """(description, JSON Schema) for an ``llm_tool`` method (bound or unbound)."""
    doc = inspect.getdoc(func) or ""
    head, _, args_block = doc.partition("Args:")
    summary = description or " ".join(head.split()) or func.__name__
    signature = inspect.signature(func)
    params = [
        p
        for p in signature.parameters.values()
        if p.name != "self" and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    ][1:]  # The first parameter receives the call context.
    if parameters is not None:
        return summary, parameters
    properties: dict[str, dict] = {}
    for line in args_block.splitlines():
        match = re.match(r"\s*(\w+)\s*\(([\w\[\]]+)\)\s*:\s*(.*)", line)
        if match:
            name, kind, text = match.groups()
            properties[name] = {**_json_type(kind), "description": text.strip()}
    if not properties:
        try:
            hints = inspect.get_annotations(func, eval_str=True)
        except Exception:
            hints = {}
        for param in params:
            properties[param.name] = _hint_type(hints.get(param.name))
    names = {p.name for p in params}
    required = [
        p.name for p in params if p.default is inspect.Parameter.empty and p.name in properties
    ]
    unknown = set(properties) - names
    if unknown and not any(p.kind is p.VAR_KEYWORD for p in signature.parameters.values()):
        raise ValueError(f"docstring documents unknown parameters: {', '.join(sorted(unknown))}")
    return summary, {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
