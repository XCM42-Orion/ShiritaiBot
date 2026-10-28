"""Plugin API v1. Only this module is a supported plugin import boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from shiritaibot.core import Context, ExternalEvent, PublicError, Tool
from shiritaibot.core.process import run_process
from shiritaibot.tools import TEXT, schema


@dataclass
class PluginContext:
    id: str
    settings: dict
    data_dir: Path
    resolve_secret: Callable[[str], Awaitable[str]]
    emit: Callable[[ExternalEvent], Awaitable[str]]
    tools: list[Tool] = field(default_factory=list)
    notifiers: list[Callable[[str, str], Awaitable[None]]] = field(default_factory=list)
    get_event: Callable[[str], Awaitable[dict | None]] | None = None

    async def emit_many(self, events: list[ExternalEvent]) -> list[str]:
        """Submit a batch through the same ingress and deduplication as emit."""
        return [await self.emit(event) for event in events]

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable[[Context, dict], Awaitable[Any]],
        risk: str,
        timeout: int = 60,
    ) -> None:
        if risk not in {"safe", "normal", "sensitive", "dangerous"}:
            raise ValueError("tool risk must be safe, normal, sensitive or dangerous")
        self.tools.append(
            Tool(name, description, parameters, handler, risk=risk, timeout=timeout, plugin=self.id)
        )

    def register_notifier(self, handler: Callable[[str, str], Awaitable[None]]) -> None:
        """Receive (source, text) for every proactive notification already sent to the owner."""
        self.notifiers.append(handler)


class Plugin:
    def __init__(self, context: PluginContext):
        self.context = context

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


__all__ = [
    "Plugin",
    "PluginContext",
    "Context",
    "ExternalEvent",
    "PublicError",
    "Tool",
    "TEXT",
    "schema",
    "run_process",
]
