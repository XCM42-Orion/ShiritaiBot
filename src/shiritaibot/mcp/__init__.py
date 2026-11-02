"""MCP Manager: connects configured MCP servers and exposes their tools to the Agent.

Every server registers its tools under the owner ``mcp:<server id>`` with names
``mcp_<server>_<tool>``, so the usual per-session tool permissions and approvals
apply. Tool security levels come from configuration, never from the server.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from copy import deepcopy

from jsonschema import Draft202012Validator

from shiritaibot.config import McpServerConfig
from shiritaibot.core import PublicError, Tool
from shiritaibot.core.logs import get_logger

from .client import HttpTransport, McpClient, McpError, StdioTransport

log = get_logger("MCP")

TOOL_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def tool_name(server: str, tool: str) -> str:
    """A provider-safe tool name (^[a-zA-Z0-9_-]{1,64}$), stable for the same input."""
    name = TOOL_NAME.sub("_", f"mcp_{server}_{tool}")
    if len(name) > 64:
        digest = hashlib.sha256(f"{server}/{tool}".encode()).hexdigest()[:8]
        name = name[:55] + "_" + digest
    return name


def input_schema(value) -> dict:
    """The tool's JSON Schema as an object schema the registry and providers accept."""
    schema = deepcopy(value) if isinstance(value, dict) else {}
    schema.pop("$schema", None)
    if schema.get("type") != "object":
        schema = {"type": "object", "properties": {}}
    schema.setdefault("properties", {})
    Draft202012Validator.check_schema(schema)
    return schema


def render_result(result: dict) -> dict:
    """Flatten a tools/call result into text for the model."""
    parts = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind in {"image", "audio"}:
            parts.append(f"[{kind}: {item.get('mimeType', 'unknown')}，未展示]")
        elif kind == "resource_link":
            parts.append(f"[资源链接 {item.get('name', '')}: {item.get('uri', '')}]")
        elif kind == "resource":
            resource = item.get("resource") or {}
            if "text" in resource:
                parts.append(f"[资源 {resource.get('uri', '')}]\n{resource['text']}")
            else:
                parts.append(f"[二进制资源 {resource.get('uri', '')}，未展示]")
    text = "\n".join(parts)
    if result.get("isError"):
        raise PublicError("MCP 工具返回错误：" + (text[:2000] or "未提供原因"))
    output: dict = {"content": text}
    if isinstance(result.get("structuredContent"), dict):
        output["structured"] = result["structuredContent"]
    return output


class McpServer:
    def __init__(self, cfg: McpServerConfig):
        self.cfg = cfg
        self.client: McpClient | None = None
        self.status = "disabled" if not cfg.enabled else "connecting"
        self.error = ""
        self.tools: list[dict] = []
        self.lock = asyncio.Lock()


class McpManager:
    def __init__(self, config, registry, vault, store):
        self.config, self.registry, self.vault, self.store = config, registry, vault, store
        self.servers: dict[str, McpServer] = {}
        self.manage_lock = asyncio.Lock()

    @staticmethod
    def owner(key: str) -> str:
        return f"mcp:{key}"

    async def _resolve(self, value: str) -> str:
        if value.startswith(("env:", "secret:")):
            return await self.vault.resolve(value)
        return value

    async def _client(self, cfg: McpServerConfig) -> McpClient:
        if cfg.transport == "stdio":
            env = {key: await self._resolve(value) for key, value in cfg.env.items()}
            transport = StdioTransport(cfg.command, env, cfg.cwd or None, cfg.id)
        else:
            headers = {key: await self._resolve(value) for key, value in cfg.headers.items()}
            transport = HttpTransport(cfg.url, headers, cfg.timeout)
        return McpClient(transport, cfg.id, cfg.timeout)

    async def _connect(self, server: McpServer) -> None:
        cfg = server.cfg
        server.status, server.error = "connecting", ""
        client = await self._client(cfg)
        try:
            await client.start()
            tools = await client.list_tools()
        except BaseException as exc:
            await client.close()
            server.status = "error"
            server.error = (
                str(exc) if isinstance(exc, McpError) else f"连接失败（{type(exc).__name__}）"
            )
            log.error(f"MCP 服务 {cfg.id} 连接失败：{server.error}")
            if not isinstance(exc, Exception):
                raise
            return
        server.client = client
        client.on_tools_changed = lambda: self._refresh(server)
        self._register(server, tools)
        server.status = "connected"
        name = client.server_info.get("name") or cfg.id
        log.log(f"MCP 服务 {cfg.id}（{name}）已连接：{len(server.tools)} 个工具")
        await self.store.activity("mcp.connected", cfg.id)

    def _register(self, server: McpServer, tools: list[dict]) -> None:
        cfg = server.cfg
        key = cfg.id
        self.registry.remove_plugin(self.owner(key))
        server.tools = []
        for item in tools:
            original = str(item["name"])
            name = tool_name(key, original)
            entry = {
                "name": name,
                "original": original,
                "description": str(item.get("description") or "")[:2000],
                "risk": cfg.tool_risks.get(original, cfg.risk),
                "read_only_hint": bool((item.get("annotations") or {}).get("readOnlyHint")),
                "enabled": original not in cfg.disabled_tools,
                "error": "",
            }
            server.tools.append(entry)
            if not entry["enabled"]:
                continue
            try:
                schema = input_schema(item.get("inputSchema"))
                if name in self.registry.tools:
                    raise ValueError("工具名称与已有工具冲突")
                title = item.get("title") or (item.get("annotations") or {}).get("title")
                description = (
                    f"[MCP {key}] "
                    + (f"{title}：" if title and title != original else "")
                    + (entry["description"] or original)
                )
                self.registry.add(
                    Tool(
                        name,
                        description,
                        schema,
                        self._handler(server, original),
                        risk=entry["risk"],
                        timeout=cfg.timeout + 10,
                        plugin=self.owner(key),
                    )
                )
            except Exception as exc:
                entry["error"] = str(exc) if isinstance(exc, ValueError) else "输入 Schema 无效"
                log.warning(f"MCP 服务 {key} 的工具 {original} 未注册：{entry['error']}")

    async def _refresh(self, server: McpServer) -> None:
        async with server.lock:
            if not server.client or not server.client.alive:
                return
            try:
                self._register(server, await server.client.list_tools())
                log.log(f"MCP 服务 {server.cfg.id} 的工具列表已更新：{len(server.tools)} 个")
            except McpError as exc:
                log.warning(f"MCP 服务 {server.cfg.id} 刷新工具失败：{exc}")

    def _handler(self, server: McpServer, original: str):
        async def call(ctx, args):
            if self.servers.get(server.cfg.id) is not server:
                raise PublicError("MCP 服务已重新配置，请重新调用")
            if not server.client or not server.client.alive:
                # One reconnect attempt: stdio servers may have crashed or been restarted.
                async with server.lock:
                    if not server.client or not server.client.alive:
                        if server.client:
                            await server.client.close()
                            server.client = None
                        await self._connect(server)
                if not server.client:
                    raise PublicError(f"MCP 服务 {server.cfg.id} 不可用：{server.error}")
            try:
                result = await server.client.call_tool(original, args, seconds=server.cfg.timeout)
            except McpError as exc:
                raise PublicError(str(exc)) from None
            return render_result(result)

        return call

    async def _stop(self, key: str) -> None:
        server = self.servers.pop(key, None)
        self.registry.remove_plugin(self.owner(key))
        if server and server.client:
            await server.client.close()
            log.log(f"MCP 服务 {key} 已断开")

    async def reload(self, force: set[str] | None = None) -> None:
        """Apply configuration: restart only changed servers (and those in force)."""
        async with self.manage_lock:
            wanted = {cfg.id: cfg for cfg in self.config.value.mcp_servers}
            for key in list(self.servers):
                current = self.servers[key]
                if key not in wanted or current.cfg != wanted[key] or key in (force or set()):
                    await self._stop(key)
            starting = []
            for key, cfg in wanted.items():
                if key in self.servers:
                    continue
                server = McpServer(cfg)
                self.servers[key] = server
                if cfg.enabled:
                    starting.append(self._connect(server))
            await asyncio.gather(*starting)

    async def reconnect(self, key: str) -> list[dict]:
        if not any(cfg.id == key for cfg in self.config.value.mcp_servers):
            raise PublicError("MCP 服务不存在")
        await self.reload(force={key})
        return self.listing()

    def listing(self) -> list[dict]:
        result = []
        for cfg in self.config.value.mcp_servers:
            server = self.servers.get(cfg.id)
            client = server.client if server else None
            result.append(
                {
                    "id": cfg.id,
                    "enabled": cfg.enabled,
                    "transport": cfg.transport,
                    "status": server.status if server else "disabled",
                    "error": server.error if server else "",
                    "server": client.server_info if client else {},
                    "instructions": client.instructions if client else "",
                    "tools": server.tools if server else [],
                }
            )
        return result

    async def start(self) -> None:
        await self.reload()

    async def close(self) -> None:
        async with self.manage_lock:
            for key in list(self.servers):
                await self._stop(key)
