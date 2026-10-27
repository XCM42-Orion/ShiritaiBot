from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

from shiritaibot.core import PublicError, uid
from shiritaibot.plugin_api import PluginContext


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
        # Proactive notification channels per plugin; the engine reads this mapping directly.
        self.notifiers: dict[str, list] = {}

    def unregister(self, key: str) -> None:
        self.registry.remove_plugin(key)
        self.notifiers.pop(key, None)

    async def discover(self) -> list[dict]:
        def scan():
            result = {}
            for root in [Path(__file__).parent.parent, self.home / "plugins"]:
                if root.exists():
                    for directory in root.iterdir():
                        if directory.name in {"core", "__pycache__"}:
                            continue
                        manifest = directory / "plugin.toml"
                        if manifest.is_file() and re.fullmatch(r"[a-z0-9_]+", directory.name):
                            meta = tomllib.loads(manifest.read_text(encoding="utf-8"))
                            if meta.get("api") != 1:
                                continue
                            if directory.name in result:
                                continue  # The source-tree plugin takes precedence over data/plugins.
                            result[directory.name] = {
                                **meta,
                                "id": directory.name,
                                "path": str(directory),
                            }
            return list(result.values())

        return await asyncio.to_thread(scan)

    async def reload(self, key: str) -> None:
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
                return
            metadata = next((p for p in await self.discover() if p["id"] == key), None)
            if not metadata:
                raise PublicError("未找到插件清单")
            directory = Path(metadata["path"])
            module_name = f"shiritaibot_plugin_{key}_{uid()}"

            def load():
                spec = importlib.util.spec_from_file_location(
                    module_name, directory / "main.py", submodule_search_locations=[str(directory)]
                )
                assert spec is not None and spec.loader is not None
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                # Avoid timestamp-based .pyc reuse during rapid edit/reload cycles.
                source = (directory / "main.py").read_text(encoding="utf-8")
                exec(compile(source, str(directory / "main.py"), "exec"), module.__dict__)
                return module

            module = await asyncio.to_thread(load)
            data_dir = self.home / "plugin_data" / key
            await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True, mode=0o700)
            context = PluginContext(
                key, cfg.settings.copy(), data_dir, self.vault.resolve, self.emit
            )
            plugin = module.Entry(context)
            try:
                await plugin.start()
                names = [t.name for t in context.tools]
                if len(set(names)) != len(names) or any(
                    t.name in self.registry.tools and self.registry.tools[t.name].plugin != key
                    for t in context.tools
                ):
                    raise PublicError("插件工具名称冲突")
                from jsonschema import Draft202012Validator

                for tool in context.tools:
                    Draft202012Validator.check_schema(tool.schema)
            except BaseException:
                await plugin.close()
                raise
            old = self.loaded.pop(key, None)
            if old:
                await old.close()
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
            await self.store.activity("plugin.loaded", key)

    async def start(self) -> None:
        for cfg in self.config.value.plugins:
            if cfg.enabled:
                try:
                    await self.reload(cfg.id)
                except Exception as exc:
                    await self.store.activity("plugin.failed", f"{cfg.id}: {type(exc).__name__}")

    async def close(self) -> None:
        for key, plugin in list(self.loaded.items()):
            async with self.locks[key]:
                await plugin.close()
                self.unregister(key)
        self.loaded.clear()
