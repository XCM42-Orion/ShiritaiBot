"""Plugin API v1. Only this module is a supported plugin import boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from shiritaibot.core import Context, ExternalEvent, PublicError, Tool
from shiritaibot.core.logs import Logger, brief, get_logger
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
    # Root of shell and subagent working directories (``<home>/workspaces``).
    workspaces: Path | None = None
    # Non-secret variables for shell and subagent child processes (see set_process_env).
    process_env: dict[str, str] = field(default_factory=dict)
    # Text appended to new subagent prompts (see add_subagent_note).
    subagent_notes: list[str] = field(default_factory=list)
    # Rewriters for shell tool commands (see add_shell_filter).
    shell_filters: list[Callable[[str], str]] = field(default_factory=list)
    # Colored terminal + rolling file + WebUI feed, tagged with this plugin's id.
    logger: Logger = field(init=False)

    def __post_init__(self) -> None:
        self.logger = get_logger(f"插件:{self.id}")

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
        chat: bool = False,
    ) -> None:
        if risk not in {"safe", "normal", "sensitive", "dangerous"}:
            raise ValueError("tool risk must be safe, normal, sensitive or dangerous")
        self.tools.append(
            Tool(
                name,
                description,
                parameters,
                handler,
                risk=risk,
                chat=chat,
                timeout=timeout,
                plugin=self.id,
            )
        )

    def set_process_env(self, env: dict[str, str]) -> None:
        """Add non-secret variables to shell tool and subagent processes, such as git identity.

        Child processes are model-controlled, so never pass credentials here. The host revokes
        the variables on reload or stop; two plugins may not set the same name.
        """
        for name, value in env.items():
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", name):
                raise ValueError(f"invalid environment variable name: {name!r}")
            if not isinstance(value, str) or "\0" in value:
                raise ValueError(f"invalid value for environment variable {name}")
        self.process_env.update(env)

    def add_subagent_note(self, text: str) -> None:
        """Append an instruction to the prompt of every new (non-generic) subagent run.

        Resumed runs already carry it in their native session. Revoked on reload or stop.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("subagent note must be non-empty text")
        self.subagent_notes.append(text.strip())

    def add_shell_filter(self, rewrite: Callable[[str], str]) -> None:
        """Rewrite each shell tool command after approval, just before it runs.

        ``rewrite(command) -> command`` must be fast, synchronous and side-effect free; it sees
        the command as approved, so it should only make narrow, predictable additions.
        Subagents do not use the shell tool and are not affected. Revoked on reload or stop.
        """
        if not callable(rewrite):
            raise ValueError("shell filter must be callable")
        self.shell_filters.append(rewrite)

    def session_workspace(self, session: str) -> Path:
        """The shell tool's working directory for a session."""
        if self.workspaces is None:
            raise RuntimeError("workspaces root is not configured")
        from shiritaibot.tools.qq import session_dir

        return session_dir(self.workspaces.parent, self.workspaces.name, session)

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
    "Logger",
    "get_logger",
    "brief",
]
