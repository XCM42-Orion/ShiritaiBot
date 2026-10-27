from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from pathlib import Path
from typing import Awaitable, Callable

from cryptography.fernet import Fernet

from shiritaibot.core import Context, PublicError, Tool, fingerprint, now, uid


class Vault:
    """Encrypted credentials at rest; API only exposes names, never values."""

    def __init__(self, home: Path, store):
        self.home, self.store = home, store
        self.cipher: Fernet

    async def open(self) -> None:
        def key() -> bytes:
            path = self.home / "master.key"
            if not path.exists():
                with path.open("xb") as stream:
                    os.chmod(path, 0o600)
                    stream.write(Fernet.generate_key())
            return path.read_bytes()

        self.cipher = Fernet(await asyncio.to_thread(key))

    async def set(self, name: str, value: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
            raise PublicError("invalid secret name")
        await self.store.put(
            "secret", name, {"cipher": self.cipher.encrypt(value.encode()).decode()}
        )

    async def resolve(self, ref: str) -> str:
        if not ref:
            return ""
        if ref.startswith("env:"):
            return os.environ.get(ref[4:], "")
        if ref.startswith("secret:"):
            row = await self.store.get("secret", ref[7:])
            return self.cipher.decrypt(row["cipher"].encode()).decode() if row else ""
        raise PublicError("secret reference required")

    async def protect_settings(self, settings):
        """Accept inline credentials while keeping configuration and API reads secret-free."""
        result = settings.model_copy(deep=True)

        async def protect(value):
            if not value or value.startswith(("env:", "secret:")):
                return value
            name = "inline-" + uid()
            await self.set(name, value)
            return "secret:" + name

        for provider in result.providers:
            provider.api_key = await protect(provider.api_key)
        for adapter in result.adapters:
            adapter.token = await protect(adapter.token)
        result.tavily_key = await protect(result.tavily_key)

        async def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"password", "api_key", "token", "access_token"} and isinstance(
                        item, str
                    ):
                        value[key] = await protect(item)
                    else:
                        await walk(item)
            elif isinstance(value, list):
                for item in value:
                    await walk(item)

        for plugin in result.plugins:
            await walk(plugin.settings)
        return result


class Permissions:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.pending: dict[str, asyncio.Future] = {}
        self.resolve_lock = asyncio.Lock()
        self.notify: Callable[[str], Awaitable[None]] | None = None

    def decision(self, ctx: Context, tool: Tool) -> str:
        policy = self.config.value.policy
        if not ctx.owner or ctx.mode != "agent" or ctx.group:
            if not tool.chat or tool.risk != "safe":
                return "deny"
        if ctx.background and tool.risk != "safe":
            return "deny"
        if ctx.allowed_tools is not None and tool.name not in ctx.allowed_tools:
            return "deny"
        if any(fnmatch.fnmatchcase(tool.name, x) for x in policy.denied_tools):
            return "deny"
        for scope, names in policy.scope_tools.items():
            if fnmatch.fnmatchcase(ctx.session, scope) and not any(
                fnmatch.fnmatchcase(tool.name, n) for n in names
            ):
                return "deny"
        if tool.name == "shell" and not policy.shell_enabled:
            return "deny"
        return "approve" if tool.risk in {"dangerous", "sensitive"} else "allow"

    async def check(self, ctx: Context, tool: Tool, args: dict) -> None:
        decision = self.decision(ctx, tool)
        if decision == "deny":
            raise PublicError("工具被权限策略拒绝")
        if decision == "allow":
            return
        key = uid()[:10]
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        body = {
            "tool": tool.name,
            "args": args,
            "hash": fingerprint([ctx.session, tool.name, args]),
            "status": "pending",
            "expires": now().timestamp() + self.config.value.policy.approval_timeout,
            "turn": ctx.turn,
        }
        await self.store.put("approval", key, body, ctx.session)
        try:
            if self.notify:
                await self.notify(
                    f"需要确认：{tool.name}\n参数：{str(args)[:2000]}\n同意 {key} / 拒绝 {key}（本次调用有效）"
                )
            async with asyncio.timeout(self.config.value.policy.approval_timeout):
                approved = await future
            if not approved:
                raise PublicError("主人拒绝了本次调用")
        except TimeoutError:
            body["status"] = "expired"
            await self.store.put("approval", key, body, ctx.session)
            raise PublicError("审批超时，未执行") from None
        except asyncio.CancelledError:
            body["status"] = "cancelled"
            await self.store.put("approval", key, body, ctx.session)
            raise
        finally:
            self.pending.pop(key, None)

    async def resolve(self, key: str, allow: bool) -> None:
        async with self.resolve_lock:
            await self._resolve(key, allow)

    async def _resolve(self, key: str, allow: bool) -> None:
        row = await self.store.get("approval", key)
        future = self.pending.get(key)
        if (
            not row
            or row["status"] != "pending"
            or not future
            or future.done()
            or row["expires"] < now().timestamp()
        ):
            raise PublicError("审批不存在、已处理或已过期")
        row["status"] = "approved" if allow else "denied"
        await self.store.put("approval", key, row, row["scope"])
        if future.done():
            row["status"] = "cancelled"
            await self.store.put("approval", key, row, row["scope"])
            raise PublicError("审批等待已取消")
        future.set_result(allow)
