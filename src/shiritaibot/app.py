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
from shiritaibot.emotion import EmotionManager
from shiritaibot.knowledge import KnowledgeBase
from shiritaibot.llm import ProviderManager
from shiritaibot.mcp import McpManager
from shiritaibot.meme import MemeManager
from shiritaibot.memory import MemoryManager, ShortTermMemory
from shiritaibot.memory.extract import MemoryExtractor
from shiritaibot.memory.maintenance import MemoryMaintenance
from shiritaibot.plugin.core import PluginManager
from shiritaibot.proactive import PREPARE_SESSION, ProactiveEngine
from shiritaibot.routine import RoutineManager
from shiritaibot.skill import SkillManager
from shiritaibot.store import Store
from shiritaibot.subagent import SubagentManager
from shiritaibot.tools import ToolRegistry
from shiritaibot.tools.builtin import register_builtin

log = get_logger("ShiritaiBot")

ROUTE_LABELS = {
    "chat": "聊天（chat）路由",
    "agent": "Agent（agent）路由",
    "agent_chat": "Agent 聊天（agent_chat）路由",
    "router": "判定（router）路由",
    "light": "轻量（light）路由",
    "memory": "记忆（memory）路由",
    "approval": "审批（approval）路由",
    "embedding": "向量（embedding）路由",
    "vision": "视觉（vision）路由",
    "meme": "表情（meme）路由",
}


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
        await self.refresh_secret_list()
        self.llm = ProviderManager(self.config, self.vault, self.store)
        self.permissions.llm = self.llm
        self.memory = MemoryManager(self.store, self.config, self.llm)
        await self.memory.open()
        self.short_term = ShortTermMemory(self.store, self.llm, config=self.config)
        await self.short_term.recover()
        self.memes = MemeManager(self.store, self.config, self.llm)
        await self.memes.open()
        self.knowledge = KnowledgeBase(self.store, self.config, self.llm)
        await self.knowledge.open()
        self.adapters = AdapterHub(self.config, self.vault, self.store)
        self.adapters.memes = self.memes
        self.proactive = ProactiveEngine(
            self.config, self.store, self.llm, self.adapters.notify_owner, self.short_term
        )
        self.emotion = EmotionManager(self.store, self.llm, self.config, self.proactive.emit)
        # Handled events feed long-term memory; the plugin that emitted them is not involved.
        self.extractor = MemoryExtractor(self.memory, self.store, self.config, self.llm)
        await self.extractor.open()
        self.proactive.memory_sink = self.extractor.enqueue_event
        self.adapters.emit = self.proactive.emit
        self.maintenance = MemoryMaintenance(self.memory, self.store, self.proactive.emit)
        self.routine = RoutineManager(self.store, self.proactive.emit)
        self.subagents = SubagentManager(self.config, self.store, self.home, self.proactive.emit)
        self.plugins = PluginManager(
            self.home, self.config, self.registry, self.vault, self.store, self.proactive.emit
        )
        self.plugins.llm, self.plugins.adapters = self.llm, self.adapters
        self.plugins.hooks.send = self.adapters.enqueue
        self.adapters.hooks = self.proactive.hooks = self.plugins.hooks
        self.subagents.env = self.plugins.child_env
        self.skills = SkillManager(self.home, self.config)
        self.mcp = McpManager(self.config, self.registry, self.vault, self.store)
        self.subagents.notes = self.plugins.subagent_notes
        await register_builtin(self)
        self.runtime = AgentRuntime(
            self.config,
            self.store,
            self.llm,
            self.registry,
            self.memory,
            self.deliver,
            self.short_term,
            emotion=self.emotion,
            skills=self.skills,
        )
        self.runtime.hooks = self.plugins.hooks
        self.adapters.runtime, self.adapters.permissions = self.runtime, self.permissions
        self.permissions.notify = partial(self.adapters.notify_owner, handoff=False)
        self.proactive.runtime = self.runtime
        self.proactive.channels = self.plugins.notifiers
        self.plugins.get_event = self.proactive.get_event
        await self.plugins.start()
        await self.mcp.start()
        await self.adapters.start()
        self.tasks = [
            asyncio.create_task(self.routine.run()),
            asyncio.create_task(self.proactive.run()),
            asyncio.create_task(self.short_term.run()),
            asyncio.create_task(self.memory.run()),
            asyncio.create_task(self.memes.run()),
            asyncio.create_task(self.knowledge.run()),
            asyncio.create_task(self.extractor.run()),
            asyncio.create_task(self.maintenance.run()),
        ]
        await self.plugins.mark_ready()
        cfg = self.config.value
        log.log(
            f"启动完成：{len(self.registry.tools)} 个工具，{len(self.plugins.loaded)} 个插件，"
            f"{len(self.adapters.instances)} 个协议端，{len(self.mcp.servers)} 个 MCP 服务；主动性引擎"
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

    async def diagnostics(self) -> list[str]:
        """Warnings about configuration that would silently fail at runtime."""
        settings = self.config.value
        warnings: list[str] = []

        async def resolves(ref: str) -> bool:
            try:
                return bool(await self.vault.resolve(ref))
            except Exception:
                return False

        provider_ok: dict[str, bool] = {}
        for provider in settings.providers:
            provider_ok[provider.id] = provider.enabled and await resolves(provider.api_key)
            if provider.enabled and not provider_ok[provider.id]:
                warnings.append(f"提供商「{provider.id}」没有可用的 API Key")
        models = {model.id: model for model in settings.models}
        for route, refs in settings.routes.items():
            if route == "test":
                continue
            label = ROUTE_LABELS.get(route, route)
            if route == "embedding" and not any(m.kind == "embedding" for m in models.values()):
                continue
            if not refs:
                warnings.append(f"{label}没有配置模型，相关功能会直接报错")
                continue
            usable = [
                model
                for model in refs
                if (cfg_model := models.get(model)) is not None
                and cfg_model.enabled
                and provider_ok.get(cfg_model.provider, False)
            ]
            if not usable:
                warnings.append(f"{label}的模型都不可用：检查模型是否启用、提供商 Key 是否填好")
        for provider_id, count in self.llm.failing().items():
            warnings.append(
                f"提供商「{provider_id}」最近 15 分钟调用失败 {count} 次，请检查 Key 和网络/代理"
            )
        search = settings.web_search
        if search.enabled:
            provider = getattr(search, search.provider, None)
            if provider is None or not await resolves(provider.api_key):
                warnings.append(
                    f"搜索服务「{search.provider}」没有可用密钥，web_search 工具不会注册"
                )
        return warnings

    async def refresh_secret_list(self) -> None:
        """Cache decrypted credentials so tool output can be scrubbed of them."""
        values = []
        for row in await self.store.list("secret"):
            try:
                value = self.vault.cipher.decrypt(row["cipher"].encode()).decode()
            except Exception:
                continue
            if len(value) >= 6:
                values.append(value)
        self.registry.secrets = tuple(values)

    async def reconfigure(self, settings: Settings) -> None:
        async with self.configure_lock:
            log.log("配置已更新，正在重载协议端、内置工具与插件")
            previous = self.config.value
            settings = await self.vault.protect_settings(settings, await self.plugins.schemas())
            await self.config.save(settings)
            await self.refresh_secret_list()
            if settings.adapters != previous.adapters:
                await self.adapters.close()
                await self.adapters.start()
            else:
                log.log("协议端配置未变化，保持现有连接")
            await register_builtin(self)
            await self.plugins.reload_all()
            await self.mcp.reload()

    async def close(self) -> None:
        log.log("正在关闭…")
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.runtime.close()
        await self.subagents.close()
        await self.plugins.close()
        await self.mcp.close()
        await self.adapters.close()
        await self.llm.close()
        await self.store.close()
        log.log("已关闭")
