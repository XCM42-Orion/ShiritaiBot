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

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable[[Context, dict], Awaitable[Any]],
        risk: str = "safe",
        timeout: int = 60,
    ) -> None:
        self.tools.append(
            Tool(name, description, parameters, handler, risk=risk, timeout=timeout, plugin=self.id)
        )


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
