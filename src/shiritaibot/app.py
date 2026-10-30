from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path

from shiritaibot.adapters.onebot11 import AdapterHub
from shiritaibot.agent import AgentRuntime
from shiritaibot.auth import Permissions, Vault
from shiritaibot.config import ConfigService, Settings
from shiritaibot.core import EventBus
from shiritaibot.core.logs import get_logger
from shiritaibot.llm import ProviderManager
from shiritaibot.memory import MemoryManager, ShortTermMemory
from shiritaibot.plugin.core import PluginManager
from shiritaibot.proactive import PREPARE_SESSION, ProactiveEngine
from shiritaibot.routine import RoutineManager
from shiritaibot.store import Store
from shiritaibot.subagent import SubagentManager
from shiritaibot.tools import ToolRegistry
from shiritaibot.tools.builtin import register_builtin

log = get_logger("ShiritaiBot")


class Application:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.bus = EventBus()
        self.config = ConfigService(self.home)
        self.store = Store(self.home, self.bus)
        self.tasks: list[asyncio.Task] = []
        self.configure_lock = asyncio.Lock()

    async def start(self) -> None:
        log.log(f"正在启动，数据目录 {self.home}")
        await self.config.load()
        await self.store.open()
        self.vault = Vault(self.home, self.store)
        await self.vault.open()
        protected = await self.vault.protect_settings(self.config.value)
        if protected != self.config.value:
            await self.config.save(protected)
        self.permissions = Permissions(self.config, self.store)
        self.registry = ToolRegistry(self.permissions, self.store)
        self.llm = ProviderManager(self.config, self.vault, self.store)
        self.permissions.llm = self.llm
        self.memory = MemoryManager(self.store)
        self.short_term = ShortTermMemory(self.store, self.llm, config=self.config)
        await self.short_term.recover()
        self.adapters = AdapterHub(self.config, self.vault, self.store)
        self.proactive = ProactiveEngine(
            self.config, self.store, self.llm, self.adapters.notify_owner, self.short_term
        )
        self.routine = RoutineManager(self.store, self.proactive.emit)
        self.subagents = SubagentManager(self.config, self.store, self.home, self.proactive.emit)
        self.plugins = PluginManager(
            self.home, self.config, self.registry, self.vault, self.store, self.proactive.emit
        )
        await register_builtin(self)
        self.runtime = AgentRuntime(
            self.config,
            self.store,
            self.llm,
            self.registry,
            self.memory,
            self.deliver,
            self.short_term,
        )
        self.adapters.runtime, self.adapters.permissions = self.runtime, self.permissions
        self.permissions.notify = partial(self.adapters.notify_owner, handoff=False)
        self.proactive.runtime = self.runtime
        self.proactive.channels = self.plugins.notifiers
        self.plugins.get_event = self.proactive.get_event
        await self.plugins.start()
        await self.adapters.start()
        self.tasks = [
            asyncio.create_task(self.routine.run()),
            asyncio.create_task(self.proactive.run()),
            asyncio.create_task(self.short_term.run()),
        ]
        cfg = self.config.value
        log.log(
            f"启动完成：{len(self.registry.tools)} 个工具，{len(self.plugins.loaded)} 个插件，"
            f"{len(self.adapters.instances)} 个协议端；主动性引擎"
            + ("已启用" if cfg.proactive.enabled else "未启用")
        )

    async def deliver(self, session: str, text: str) -> None:
        if session.startswith(PREPARE_SESSION):
            # Proactive turns return to PE, which owns the final notification decision.
            await self.proactive.prepared(session, text)
        elif session.startswith("background:"):
            await self.adapters.notify_owner(text)
        else:
            await self.adapters.enqueue(session, text)

    async def reconfigure(self, settings: Settings) -> None:
        async with self.configure_lock:
            log.log("配置已更新，正在重载协议端、内置工具与插件")
            settings = await self.vault.protect_settings(settings, await self.plugins.schemas())
            await self.config.save(settings)
            await self.adapters.close()
            await self.adapters.start()
            await register_builtin(self)
            await self.plugins.reload_all()

    async def close(self) -> None:
        log.log("正在关闭…")
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.runtime.close()
        await self.subagents.close()
        await self.plugins.close()
        await self.adapters.close()
        await self.llm.close()
        await self.store.close()
        log.log("已关闭")
