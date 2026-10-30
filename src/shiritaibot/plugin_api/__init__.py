"""Stable public API for plugins and school integrations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shiritaibot.learn import OneLearnPlugin

from .base import (
    TEXT,
    Context,
    ExternalEvent,
    Logger,
    Plugin,
    PluginContext,
    PublicError,
    Tool,
    brief,
    get_logger,
    run_process,
    schema,
)

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
    "OneLearnPlugin",
]


def __getattr__(name: str):
    if name == "OneLearnPlugin":
        from shiritaibot.learn import OneLearnPlugin

        return OneLearnPlugin
    raise AttributeError(name)
