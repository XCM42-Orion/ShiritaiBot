"""Plugin base class, host context and settings (AstrBot's ``Star``, ``Context``, ``AstrBotConfig``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

from shiritaibot.core import Tool
from shiritaibot.core.logs import Logger
from shiritaibot.plugin_api.base import PluginContext

if TYPE_CHECKING:
    from shiritaibot.plugin.core import PluginManager


class Platform(Protocol):
    self_id: str  # The bot's QQ number once connected.
    status: str  # "connected" | "connecting" | "disconnected"

    async def call(self, action: str, params: dict) -> Any: ...


class PluginSettings(dict):
    """The plugin's configuration: schema defaults merged with the saved values."""

    def __init__(self, data: dict, saver: Callable[[dict], Awaitable[None]] | None = None):
        super().__init__(data)
        self._saver = saver

    async def save_config(self) -> None:
        """Persist the current values to ``config.toml`` (secret fields go to the Vault).

        A save reloads nothing; the running instance keeps using this object.
        """
        if self._saver is None:
            raise RuntimeError("settings are not bound to a plugin")
        await self._saver(dict(self))


@dataclass
class Context(PluginContext):
    """Host services for one plugin. Everything from the v1 ``PluginContext`` is included:
    ``emit``/``emit_many``/``get_event`` for proactive events, ``register_tool``,
    ``register_notifier``, ``set_process_env``, ``add_subagent_note``, ``add_shell_filter``,
    ``session_workspace``, ``resolve_secret``, ``data_dir`` and ``logger``.
    """

    host: PluginManager | None = field(default=None, repr=False)

    def _require_host(self) -> PluginManager:
        if self.host is None:
            raise RuntimeError("plugin host is not available")
        return self.host

    def add_tools(self, *tools: Tool) -> None:
        for tool in tools:
            self.register_tool(
                tool.name,
                tool.description,
                tool.schema,
                tool.handler,
                risk=tool.risk,
                timeout=int(tool.timeout),
                chat=tool.chat,
            )

    async def send_message(self, session: str, text: str) -> None:
        """Push text to a session (``MessageEvent.session``) outside any reply."""
        await self._require_host().send_message(session, text)

    async def notify_owner(self, text: str) -> None:
        """Message the owner directly, bypassing proactive triage and quiet hours."""
        await self._require_host().notify_owner(text)

    async def llm_generate(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict] | None = None,
        system: str = "",
        route: str = "light",
    ) -> str:
        """One model call without tools; ``prompt`` is appended after ``messages``."""
        return await self._require_host().llm_generate(prompt, messages, system, route)

    async def decide(self, state: str | dict, questions: dict, *, route: str = "light"):
        """Structured choice/score questions for a System One decision model.

        Returns None when the route has no decision model, so callers can fall back to
        ``llm_generate``.
        """
        return await self._require_host().decide(state, questions, route)

    def get_plugins(self) -> list[dict]:
        """Metadata of every loaded plugin."""
        return self._require_host().plugin_list()

    def get_platform(self, adapter_id: str) -> Platform | None:
        """The OneBot adapter instance; ``await platform.call(action, params)`` reaches its API."""
        return self._require_host().platform(adapter_id)


class Plugin:
    """Base class for plugins: subclass it exactly once in ``main.py``.

    ``__init__(self, context, config)`` receives the host context and settings; accept only
    ``context`` if the plugin has no settings. Long-running work starts in ``initialize``
    and must stop in ``terminate``.
    """

    def __init__(self, context: Context, config: PluginSettings | None = None):
        self.context = context
        self.config = config

    @property
    def name(self) -> str:
        return self.context.id

    @property
    def data_dir(self) -> Path:
        return self.context.data_dir

    @property
    def logger(self) -> Logger:
        return self.context.logger

    async def initialize(self) -> None:
        pass

    async def terminate(self) -> None:
        pass

    async def get_kv_data(self, key: str, default: Any = None) -> Any:
        return await self.context._require_host().kv_get(self.name, key, default)

    async def put_kv_data(self, key: str, value: Any) -> None:
        await self.context._require_host().kv_put(self.name, key, value)

    async def delete_kv_data(self, key: str) -> None:
        await self.context._require_host().kv_delete(self.name, key)
