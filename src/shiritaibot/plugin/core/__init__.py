from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator

from shiritaibot.api import Context, Plugin, PluginSettings
from shiritaibot.api.hook import tool_spec
from shiritaibot.config import PluginConfig
from shiritaibot.core import PublicError, uid
from shiritaibot.core.logs import get_logger
from shiritaibot.plugin_api import PluginContext

from .hooks import HOOK_TIMEOUT, Handler, HookBus, collect
from .manifest import manifest_file, read_manifest
from .packages import install_archive, remove_directory

log = get_logger("插件")


class PluginManager:
    def __init__(self, home, config, registry, vault, store, emit):
        self.home, self.config, self.registry, self.vault, self.store, self.emit = (
            home,
            config,
            registry,
            vault,
            store,
            emit,
        )
        self.loaded: dict = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.errors: dict[str, str] = {}
        self.modules: dict[str, str] = {}
        self.manage_lock = asyncio.Lock()
        self.source_root = Path(__file__).parent.parent
        # Proactive notification channels per plugin; the engine reads this mapping directly.
        self.notifiers: dict[str, list] = {}
        self.get_event: Callable[[str], Awaitable[dict | None]] | None = None
        # Non-secret environment variables each plugin adds to shell / subagent processes.
        self.process_env: dict[str, dict[str, str]] = {}
        # Per-plugin subagent prompt notes and shell command rewriters.
        self.subagent_note_map: dict[str, list[str]] = {}
        self.shell_filters: dict[str, list[Callable[[str], str]]] = {}
        # v2 decorator handlers (commands, listeners, pipeline and proactive hooks).
        self.hooks = HookBus(store, self._alive)
        self.metadata: dict[str, dict] = {}
        # Host services for v2 contexts, wired by the application after construction.
        self.llm: Any = None
        self.adapters: Any = None
        # on_loaded hooks run once the host has started, then after every later (re)load.
        self.ready = False

    def _alive(self, handler: Handler) -> bool:
        return self.loaded.get(handler.plugin) is handler.owner

    def unregister(self, key: str) -> None:
        self.registry.remove_plugin(key)
        self.notifiers.pop(key, None)
        self.process_env.pop(key, None)
        self.subagent_note_map.pop(key, None)
        self.shell_filters.pop(key, None)
        self.hooks.remove(key)

    @staticmethod
    async def _stop(plugin) -> None:
        await (plugin.terminate() if isinstance(plugin, Plugin) else plugin.close())

    # Host services used by v2 plugin contexts ---------------------------------------

    async def send_message(self, session: str, text: str) -> None:
        if self.adapters is None:
            raise PublicError("消息通道尚未就绪")
        await self.adapters.enqueue(session, text)

    async def notify_owner(self, text: str) -> None:
        if self.adapters is None:
            raise PublicError("消息通道尚未就绪")
        await self.adapters.notify_owner(text)

    async def llm_generate(
        self, prompt: str | None, messages: list[dict] | None, system: str, route: str
    ) -> str:
        if self.llm is None:
            raise PublicError("模型服务尚未就绪")
        history = list(messages or [])
        if prompt:
            history.append({"role": "user", "content": prompt})
        if not history:
            raise ValueError("prompt or messages is required")
        return (await self.llm.complete(route, system, history, [])).text

    async def decide(self, state, questions: dict, route: str):
        if self.llm is None:
            raise PublicError("模型服务尚未就绪")
        return await self.llm.decide(route, state, questions)

    def plugin_list(self) -> list[dict]:
        keys = ("id", "api", "name", "description", "version", "author", "repo")
        return [
            {key: self.metadata.get(plugin, {}).get(key, "") for key in keys} | {"id": plugin}
            for plugin in self.loaded
        ]

    def platform(self, adapter_id: str):
        return self.adapters.instances.get(adapter_id) if self.adapters is not None else None

    async def kv_get(self, plugin: str, key: str, default: Any = None) -> Any:
        row = await self.store.get("plugin_kv", f"{plugin}:{key}")
        return row["value"] if row else default

    async def kv_put(self, plugin: str, key: str, value: Any) -> None:
        await self.store.put("plugin_kv", f"{plugin}:{key}", {"value": value}, plugin)

    async def kv_delete(self, plugin: str, key: str) -> None:
        await self.store.delete("plugin_kv", f"{plugin}:{key}")

    async def save_settings(self, plugin: str, values: dict) -> None:
        settings = self.config.value.model_copy(deep=True)
        entry = next((p for p in settings.plugins if p.id == plugin), None)
        if entry is None:
            raise PublicError("插件尚未配置")
        entry.settings = deepcopy(values)
        settings = await self.vault.protect_settings(settings, await self.schemas())
        await self.config.save(settings)

    def child_env(self) -> dict[str, str] | None:
        """Environment for model-driven child processes; None inherits the host unchanged."""
        if not any(self.process_env.values()):
            return None
        env = dict(os.environ)
        for values in self.process_env.values():
            env.update(values)
        return env

    def subagent_notes(self) -> str:
        """Plugin instructions appended to new subagent prompts; empty when there are none."""
        notes = [note for values in self.subagent_note_map.values() for note in values]
        return "".join(f"\n\n---\n{note}" for note in notes)

    def filter_shell(self, command: str) -> str:
        for key, filters in list(self.shell_filters.items()):
            for rewrite in filters:
                try:
                    result = rewrite(command)
                except Exception as error:  # A broken filter must not block the shell.
                    log.log(f"插件 {key} 的 shell 过滤器出错，已忽略：{error!r}")
                    continue
                if isinstance(result, str) and result:
                    command = result
        return command

    async def discover(self) -> list[dict]:
        def scan():
            result = {}
            for root in [self.source_root, self.home / "plugins"]:
                if root.exists():
                    for directory in sorted(root.iterdir()):
                        if directory.name in {"core", "__pycache__"}:
                            continue
                        if (
                            not directory.is_symlink()
                            and manifest_file(directory) is not None
                            and re.fullmatch(r"[a-z0-9_]{1,64}", directory.name)
                        ):
                            if directory.name in result:
                                continue  # The source-tree plugin takes precedence over data/plugins.
                            result[directory.name] = read_manifest(directory)
            return list(result.values())

        return await asyncio.to_thread(scan)

    async def sync_config(self) -> None:
        """Register discovered plugins without changing existing settings or opt-in state."""
        settings = self.config.value.model_copy(deep=True)
        known = {p.id for p in settings.plugins}
        discovered = await self.discover()
        for meta in discovered:
            if meta["id"] not in known and not meta["error"]:
                settings.plugins.append(
                    PluginConfig(id=meta["id"], settings=deepcopy(meta["defaults"]))
                )
            elif meta["api"] == 2 and not meta["error"]:
                # Like AstrBot: new fields get defaults, fields the schema dropped go away.
                cfg = next(p for p in settings.plugins if p.id == meta["id"])
                fields = meta["settings_schema"].get("properties", {})
                cfg.settings = {
                    name: deepcopy(cfg.settings.get(name, meta["defaults"].get(name)))
                    for name in fields
                }
        settings = await self.vault.protect_settings(
            settings, {p["id"]: p["settings_schema"] for p in discovered}
        )
        if settings != self.config.value:
            await self.config.save(settings)

    async def schemas(self) -> dict:
        return {p["id"]: p["settings_schema"] for p in await self.discover()}

    async def listing(self) -> list[dict]:
        configs = {p.id: p for p in self.config.value.plugins}
        result = []
        for meta in await self.discover():
            key = meta["id"]
            cfg = configs.get(key, PluginConfig(id=key))
            error = meta.get("error") or self.errors.get(key, "")
            status = "loaded" if key in self.loaded else "disabled"
            if error:
                status = "error"
            tools = [
                {"name": tool.name, "risk": tool.risk, "safe": tool.safe}
                for tool in self.registry.tools.values()
                if tool.plugin == key
            ]
            commands, hooks = self.hooks.describe(key)
            result.append(
                {
                    **meta,
                    "status": status,
                    "error": error,
                    "enabled": cfg.enabled,
                    "tools": tools,
                    "commands": commands,
                    "hooks": hooks,
                }
            )
        return result

    async def reload_all(self) -> list[dict]:
        async with self.manage_lock:
            await self.sync_config()
            configured = {p.id for p in self.config.value.plugins}
            discovered = {p["id"] for p in await self.discover()}
            for key in list(self.loaded):
                if key not in configured or key not in discovered:
                    async with self.locks[key]:
                        await self._stop(self.loaded.pop(key))
                        self.unregister(key)
                        self._forget_modules(key)
            self.errors = {key: error for key, error in self.errors.items() if key in discovered}
            for cfg in self.config.value.plugins:
                if cfg.id not in discovered:
                    continue
                try:
                    await self.reload(cfg.id)
                except Exception:
                    pass  # reload records the failure; one broken plugin must not stop the rest.
            return await self.listing()

    async def install(self, key: str, archive: bytes) -> list[dict]:
        async with self.manage_lock:
            if any(p["id"] == key for p in await self.discover()):
                raise PublicError("同名插件已存在，请先移除或手动更新后重载")
            await asyncio.to_thread(install_archive, self.home / "plugins", key, archive)
            log.log(f"已安装插件 {key}")
            await self.sync_config()
            try:
                await self.reload(key)
            except Exception:
                pass  # Keep installed code and show its load error so settings can be corrected.
            return await self.listing()

    async def remove(self, key: str) -> list[dict]:
        async with self.manage_lock:
            metadata = next((p for p in await self.discover() if p["id"] == key), None)
            cfg = next((p for p in self.config.value.plugins if p.id == key), None)
            if metadata is None and cfg is None:
                raise PublicError("插件不存在")
            async with self.locks.setdefault(key, asyncio.Lock()):
                old = self.loaded.get(key)
                if old:
                    await self._stop(old)
                self.loaded.pop(key, None)
                self.unregister(key)
                self._forget_modules(key)
                if metadata:
                    # Remove both copies to prevent a shadowed plugin reappearing on reload.
                    for root in [self.source_root, self.home / "plugins"]:
                        await asyncio.to_thread(remove_directory, root, key)
                settings = self.config.value.model_copy(deep=True)
                settings.plugins = [p for p in settings.plugins if p.id != key]
                await self.config.save(settings)
                self.errors.pop(key, None)
                log.log(f"已移除插件 {key}")
                await self.store.activity("plugin.removed", key)
            await self.sync_config()
            return await self.listing()

    def _forget_modules(self, key: str, keep: str = "") -> None:
        old = self.modules.pop(key, None)
        if old and old != keep:
            self._drop_module(old)
        if keep:
            self.modules[key] = keep

    @staticmethod
    def _drop_module(module_name: str) -> None:
        for name in list(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                del sys.modules[name]

    async def reload(self, key: str) -> None:
        try:
            await self._reload(key)
            self.errors.pop(key, None)
        except Exception as exc:
            # Never expose arbitrary exception text, which may contain plugin credentials.
            message = (
                str(exc)
                if isinstance(exc, PublicError)
                else f"插件加载失败（{type(exc).__name__}）"
            )
            self.errors[key] = message
            log.error(f"插件 {key} 加载失败：{message}")
            # Tracebacks may quote plugin settings, so they only appear at debug level.
            log.debug(f"插件 {key} 加载失败详情", exc=exc)
            await self.store.activity("plugin.failed", f"{key}: {type(exc).__name__}")
            raise

    async def _reload(self, key: str) -> None:
        cfg = next((p for p in self.config.value.plugins if p.id == key), None)
        if not cfg:
            raise PublicError("插件尚未配置")
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            metadata = next((p for p in await self.discover() if p["id"] == key), None)
            if not metadata:
                raise PublicError("未找到插件清单")
            if not cfg.enabled:
                old = self.loaded.pop(key, None)
                self.unregister(key)
                if old:
                    await self._stop(old)
                    log.log(f"插件 {key} 已停用")
                self._forget_modules(key)
                return
            if metadata["error"]:
                raise PublicError(metadata["error"])
            plugin_settings = deepcopy({**metadata["defaults"], **cfg.settings})
            invalid = list(
                Draft202012Validator(metadata["settings_schema"]).iter_errors(plugin_settings)
            )
            if invalid:
                fields = sorted(
                    {".".join(map(str, error.absolute_path)) or "settings" for error in invalid}
                )
                raise PublicError("插件配置不符合定义：" + "、".join(fields))
            directory = Path(metadata["path"])
            module_name = f"shiritaibot_plugin_{key}_{uid()}"

            def load():
                # Relative imports must also pick up edits made within a .pyc timestamp tick.
                for cache in directory.rglob("__pycache__/*.pyc"):
                    cache.unlink(missing_ok=True)
                spec = importlib.util.spec_from_file_location(
                    module_name, directory / "main.py", submodule_search_locations=[str(directory)]
                )
                assert spec is not None and spec.loader is not None
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                # Avoid timestamp-based .pyc reuse during rapid edit/reload cycles.
                source = (directory / "main.py").read_text(encoding="utf-8")
                try:
                    exec(compile(source, str(directory / "main.py"), "exec"), module.__dict__)
                except BaseException:
                    self._drop_module(module_name)
                    raise
                return module

            module = await asyncio.to_thread(load)
            plugin = None
            handlers: list[Handler] = []
            try:
                data_dir = self.home / "plugin_data" / key
                await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True, mode=0o700)
                if metadata["api"] == 2:
                    settings = PluginSettings(
                        plugin_settings, lambda values: self.save_settings(key, values)
                    )
                    context = Context(
                        key, settings, data_dir, self.vault.resolve, self.emit, host=self
                    )
                else:
                    context = PluginContext(
                        key, plugin_settings, data_dir, self.vault.resolve, self.emit
                    )
                context.get_event = self.get_event
                context.workspaces = self.home / "workspaces"
                if isinstance(context, Context):
                    plugin = self._instantiate(module, context)
                    handlers = collect(key, plugin, self.hooks.counter)
                    for handler in handlers:
                        if handler.kind == "llm_tool":
                            self._register_tool(context, handler)
                    await plugin.initialize()
                    clashes = self.hooks.command_conflicts(key, handlers)
                    if clashes:
                        raise PublicError("插件指令与已有指令冲突：" + "、".join(clashes))
                else:
                    plugin = module.Entry(context)
                    await plugin.start()
                names = [t.name for t in context.tools]
                if len(set(names)) != len(names) or any(
                    t.name in self.registry.tools and self.registry.tools[t.name].plugin != key
                    for t in context.tools
                ):
                    raise PublicError("插件工具名称冲突")
                if any(
                    name in values
                    for other, values in self.process_env.items()
                    if other != key
                    for name in context.process_env
                ):
                    raise PublicError("插件环境变量与其他插件冲突")
                for tool in context.tools:
                    self.registry.validate(tool)
            except BaseException:
                try:
                    if plugin is not None:
                        await self._stop(plugin)
                finally:
                    self._drop_module(module_name)
                raise
            old = self.loaded.get(key)
            if old:
                try:
                    await self._stop(old)
                except BaseException:
                    try:
                        await self._stop(plugin)
                    finally:
                        self._drop_module(module_name)
                    raise
            self.unregister(key)

            def guard(handler):
                async def guarded(*args):
                    async with lock:
                        if self.loaded.get(key) is not plugin:
                            raise PublicError("插件已重载，请重新调用")
                        return await handler(*args)

                return guarded

            def channel(handler: Handler):
                async def notified(source: str, text: str) -> None:
                    await self.hooks.invoke(handler, HOOK_TIMEOUT, source, text)

                return notified

            for tool in context.tools:
                tool.handler = guard(tool.handler)
                self.registry.add(tool)
            channels = [guard(handler) for handler in context.notifiers] + [
                channel(h) for h in handlers if h.kind == "notification_sent"
            ]
            if channels:
                self.notifiers[key] = channels
            if context.process_env:
                self.process_env[key] = dict(context.process_env)
            if context.subagent_notes:
                self.subagent_note_map[key] = list(context.subagent_notes)
            if context.shell_filters:
                self.shell_filters[key] = list(context.shell_filters)
            self.hooks.set(key, handlers)
            self.metadata[key] = metadata
            self.loaded[key] = plugin
            self._forget_modules(key, keep=module_name)
            commands = sum(h.kind == "command" for h in handlers)
            hooks = sum(h.kind not in {"command", "llm_tool"} for h in handlers)
            log.log(
                f"插件 {metadata['name']}（{key}）已{'重新' if old else ''}加载："
                f"{len(context.tools)} 个工具"
                + (f"，{commands} 个指令" if commands else "")
                + (f"，{hooks} 个 hook" if hooks else "")
                + (f"，{len(channels)} 个通知渠道" if channels else "")
            )
            await self.store.activity("plugin.loaded", key)
            if self.ready:
                self.hooks.spawn(self._loaded(key))

    @staticmethod
    def _instantiate(module, context: Context) -> Plugin:
        """Construct the single concrete ``Plugin`` subclass defined in ``main.py``."""
        defined = [
            value
            for value in vars(module).values()
            if inspect.isclass(value)
            and issubclass(value, Plugin)
            and value is not Plugin
            and value.__module__ == module.__name__
        ]
        leaves = [
            cls for cls in defined if not any(o is not cls and issubclass(o, cls) for o in defined)
        ]
        if len(leaves) != 1:
            raise PublicError("main.py 中需要恰好定义一个 Plugin 子类")
        cls = leaves[0]
        try:
            inspect.signature(cls).bind(context, context.settings)
            takes_config = True
        except TypeError:
            takes_config = False
        plugin = cls(context, context.settings) if takes_config else cls(context)
        if getattr(plugin, "config", None) is None:
            plugin.config = context.settings
        return plugin

    @staticmethod
    def _register_tool(context: Context, handler: Handler) -> None:
        options = handler.options
        try:
            description, parameters = tool_spec(
                handler.func, options["description"], options["parameters"]
            )
        except ValueError as exc:
            raise PublicError(f"工具 {options['name']} 的参数定义无效：{exc}") from None
        method = handler.func

        async def call(ctx, args):
            return await method(ctx, **args)

        context.register_tool(
            options["name"],
            description,
            parameters,
            call,
            risk=options["risk"],
            timeout=options["timeout"],
            chat=options["chat"],
        )

    async def _loaded(self, key: str) -> None:
        for handler in self.hooks.of("loaded"):
            if handler.plugin == key:
                await self.hooks.invoke(handler, HOOK_TIMEOUT)

    async def mark_ready(self) -> None:
        """Called once the host has started: run every loaded plugin's on_loaded hooks."""
        self.ready = True
        for key in list(self.loaded):
            self.hooks.spawn(self._loaded(key))

    async def start(self) -> None:
        await self.reload_all()

    async def close(self) -> None:
        await self.hooks.close()
        for key, plugin in list(self.loaded.items()):
            async with self.locks[key]:
                await self._stop(plugin)
                self.unregister(key)
                self._forget_modules(key)
        self.loaded.clear()
