from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Awaitable, Callable

from jsonschema import Draft202012Validator

from shiritaibot.config import PluginConfig
from shiritaibot.core import PublicError, uid
from shiritaibot.plugin_api import PluginContext

from .packages import install_archive, remove_directory


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

    def unregister(self, key: str) -> None:
        self.registry.remove_plugin(key)
        self.notifiers.pop(key, None)

    async def discover(self) -> list[dict]:
        def scan():
            result = {}
            for root in [self.source_root, self.home / "plugins"]:
                if root.exists():
                    for directory in sorted(root.iterdir()):
                        if directory.name in {"core", "__pycache__"}:
                            continue
                        manifest = directory / "plugin.toml"
                        if (
                            not directory.is_symlink()
                            and manifest.is_file()
                            and re.fullmatch(r"[a-z0-9_]{1,64}", directory.name)
                        ):
                            if directory.name in result:
                                continue  # The source-tree plugin takes precedence over data/plugins.
                            error = ""
                            meta = {}
                            try:
                                meta = tomllib.loads(manifest.read_text(encoding="utf-8"))
                                if meta.get("api") != 1:
                                    raise ValueError("不支持的插件 API 版本")
                                if not (directory / "main.py").is_file():
                                    raise ValueError("缺少 main.py 入口")
                                if not isinstance(meta.get("settings", {}), dict):
                                    raise ValueError("settings 必须是配置表")
                                schema = meta.get("settings_schema", {"type": "object"})
                                Draft202012Validator.check_schema(schema)
                                if schema.get("type") != "object":
                                    raise ValueError("settings_schema 必须描述对象")
                            except (OSError, ValueError) as exc:
                                error = f"插件清单无效（{type(exc).__name__}）"
                            except Exception:
                                error = "插件配置 schema 无效"
                            result[directory.name] = {
                                "name": meta.get("name", directory.name),
                                "description": meta.get("description", ""),
                                "settings_schema": meta.get("settings_schema", {})
                                if not error
                                else {},
                                "defaults": {
                                    **{
                                        k: v["default"]
                                        for k, v in meta.get("settings_schema", {})
                                        .get("properties", {})
                                        .items()
                                        if isinstance(v, dict) and "default" in v
                                    },
                                    **meta.get("settings", {}),
                                }
                                if not error
                                else {},
                                "id": directory.name,
                                "path": str(directory),
                                "error": error,
                            }
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
        settings = await self.vault.protect_settings(
            settings, {p["id"]: p["settings_schema"] for p in discovered}
        )
        if settings != self.config.value:
            await self.config.save(settings)

    async def schemas(self) -> dict:
        return {p["id"]: p["settings_schema"] for p in await self.discover()}

    async def listing(self) -> list[dict]:
        discovered = {p["id"]: p for p in await self.discover()}
        configs = {p.id: p for p in self.config.value.plugins}
        result = []
        for key in dict.fromkeys([*discovered, *configs]):
            meta = discovered.get(key, {"id": key, "name": key, "description": ""})
            cfg = configs.get(key, PluginConfig(id=key))
            error = meta.get("error") or self.errors.get(key, "")
            status = "loaded" if key in self.loaded else "disabled"
            if key not in discovered:
                status, error = "missing", "未找到插件目录或清单"
            elif error:
                status = "error"
            result.append({**meta, "status": status, "error": error, "enabled": cfg.enabled})
        return result

    async def reload_all(self) -> list[dict]:
        async with self.manage_lock:
            await self.sync_config()
            configured = {p.id for p in self.config.value.plugins}
            for key in list(self.loaded):
                if key not in configured:
                    async with self.locks[key]:
                        await self.loaded.pop(key).close()
                        self.unregister(key)
                        self._forget_modules(key)
            for cfg in self.config.value.plugins:
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
                    await old.close()
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
            await self.store.activity("plugin.failed", f"{key}: {type(exc).__name__}")
            raise

    async def _reload(self, key: str) -> None:
        cfg = next((p for p in self.config.value.plugins if p.id == key), None)
        if not cfg:
            raise PublicError("插件尚未配置")
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not cfg.enabled:
                old = self.loaded.pop(key, None)
                self.unregister(key)
                if old:
                    await old.close()
                self._forget_modules(key)
                return
            metadata = next((p for p in await self.discover() if p["id"] == key), None)
            if not metadata:
                raise PublicError("未找到插件清单")
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
            try:
                data_dir = self.home / "plugin_data" / key
                await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True, mode=0o700)
                context = PluginContext(
                    key, plugin_settings, data_dir, self.vault.resolve, self.emit
                )
                context.get_event = self.get_event
                plugin = module.Entry(context)
                await plugin.start()
                names = [t.name for t in context.tools]
                if len(set(names)) != len(names) or any(
                    t.name in self.registry.tools and self.registry.tools[t.name].plugin != key
                    for t in context.tools
                ):
                    raise PublicError("插件工具名称冲突")
                for tool in context.tools:
                    Draft202012Validator.check_schema(tool.schema)
            except BaseException:
                try:
                    if plugin is not None:
                        await plugin.close()
                finally:
                    self._drop_module(module_name)
                raise
            old = self.loaded.get(key)
            if old:
                try:
                    await old.close()
                except BaseException:
                    try:
                        await plugin.close()
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

            for tool in context.tools:
                tool.handler = guard(tool.handler)
                self.registry.add(tool)
            if context.notifiers:
                self.notifiers[key] = [guard(handler) for handler in context.notifiers]
            self.loaded[key] = plugin
            self._forget_modules(key, keep=module_name)
            await self.store.activity("plugin.loaded", key)

    async def start(self) -> None:
        await self.reload_all()

    async def close(self) -> None:
        for key, plugin in list(self.loaded.items()):
            async with self.locks[key]:
                await plugin.close()
                self.unregister(key)
                self._forget_modules(key)
        self.loaded.clear()
