"""Plugin API v2: the only supported import boundary for ``metadata.yaml`` plugins.

    from shiritaibot.api import Context, MessageEvent, Plugin, PluginSettings, hook

The legacy v1 API (``plugin.toml`` + ``Entry``) stays in ``shiritaibot.plugin_api``.
"""

from shiritaibot.config import check_proxy
from shiritaibot.core import Actor, ExternalEvent, PublicError, Tool
from shiritaibot.core import Context as ToolContext
from shiritaibot.core.logs import Logger, brief, get_logger
from shiritaibot.core.process import run_process
from shiritaibot.tools import TEXT, schema

from .event import (
    AgentResult,
    LLMRequest,
    LLMResponse,
    MessageEvent,
    MessageResult,
    OutgoingMessage,
    ProactiveEvent,
    ProactiveOutcome,
    Triage,
    Turn,
)
from .hook import CommandGroup, MessageType, Permission, Platform, hook
from .plugin import Context, Plugin, PluginSettings

__all__ = [
    "Plugin",
    "Context",
    "PluginSettings",
    "hook",
    "CommandGroup",
    "MessageType",
    "Permission",
    "Platform",
    "MessageEvent",
    "MessageResult",
    "OutgoingMessage",
    "Turn",
    "LLMRequest",
    "LLMResponse",
    "AgentResult",
    "ProactiveEvent",
    "Triage",
    "ProactiveOutcome",
    "ToolContext",
    "Tool",
    "ExternalEvent",
    "Actor",
    "PublicError",
    "TEXT",
    "schema",
    "run_process",
    "Logger",
    "get_logger",
    "brief",
    "check_proxy",
]
