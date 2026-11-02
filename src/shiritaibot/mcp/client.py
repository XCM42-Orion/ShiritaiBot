"""Minimal Model Context Protocol client: JSON-RPC over stdio or Streamable HTTP.

Only the client side of the tools feature is implemented: initialize, tools/list
(with pagination), tools/call, ping, and tools/list_changed notifications. Server
requests the client does not support (sampling, elicitation, roots) get a JSON-RPC
"method not found" error so the server can fall back.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import sys
from typing import Any, Awaitable, Callable, Coroutine

import httpx

from shiritaibot.core.logs import get_logger

log = get_logger("MCP")

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "ShiritaiBot", "version": "0.2.0"}
MAX_LINE = 16 * 1024 * 1024


class McpError(Exception):
    """A failure talking to an MCP server; the message is safe to show the owner."""


class Transport:
    on_message: Callable[[dict], Awaitable[None]]

    async def open(self) -> None: ...

    async def send(self, message: dict) -> None: ...

    async def close(self) -> None: ...

    @property
    def alive(self) -> bool:
        return True


class StdioTransport(Transport):
    def __init__(self, command: list[str], env: dict[str, str], cwd: str | None, name: str):
        self.command, self.env, self.cwd, self.name = command, env, cwd, name
        self.process: asyncio.subprocess.Process | None = None
        self.tasks: list[asyncio.Task] = []
        self.closed = asyncio.Event()

    async def open(self) -> None:
        kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self.env},
                cwd=self.cwd or None,
                limit=MAX_LINE,
                **kwargs,
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"无法启动 MCP 服务进程（{type(exc).__name__}），请检查命令") from None
        self.tasks = [
            asyncio.create_task(self._read()),
            asyncio.create_task(self._drain_stderr()),
        ]

    async def _read(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    log.debug(f"MCP {self.name} 输出了非 JSON 行，已忽略")
                    continue
                for item in message if isinstance(message, list) else [message]:
                    if isinstance(item, dict):
                        await self.on_message(item)
        except (asyncio.LimitOverrunError, ValueError):
            log.warning(f"MCP {self.name} 输出的单条消息过大，连接已关闭")
        finally:
            self.closed.set()

    async def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        while line := await self.process.stderr.readline():
            log.debug(f"MCP {self.name}: {line.decode(errors='replace').rstrip()[:500]}")

    async def send(self, message: dict) -> None:
        if not self.process or not self.process.stdin or self.closed.is_set():
            raise McpError("MCP 服务进程已退出")
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            self.process.stdin.write(data.encode())
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise McpError("MCP 服务进程已退出") from None

    @property
    def alive(self) -> bool:
        return (
            self.process is not None
            and self.process.returncode is None
            and not self.closed.is_set()
        )

    async def close(self) -> None:
        process = self.process
        if process and process.returncode is None:
            with contextlib.suppress(Exception):
                assert process.stdin
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class HttpTransport(Transport):
    """Streamable HTTP: every message is a POST; replies come as JSON or an SSE stream."""

    def __init__(self, url: str, headers: dict[str, str], timeout: float):
        self.url, self.headers = url, headers
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15))
        self.session_id = ""
        self.protocol = ""
        self.tasks: set[asyncio.Task] = set()

    async def send(self, message: dict) -> None:
        headers = {
            **self.headers,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        try:
            request = self.client.build_request("POST", self.url, json=message, headers=headers)
            response = await self.client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise McpError(f"无法连接 MCP 服务（{type(exc).__name__}）") from None
        if session := response.headers.get("mcp-session-id"):
            self.session_id = session
        if response.status_code >= 400:
            await response.aclose()
            if response.status_code in {401, 403}:
                raise McpError(f"MCP 服务拒绝访问（HTTP {response.status_code}），请检查认证头")
            if response.status_code == 404 and self.session_id:
                raise McpError("MCP 会话已失效，请重新连接")
            raise McpError(f"MCP 服务返回 HTTP {response.status_code}")
        kind = response.headers.get("content-type", "")
        if response.status_code == 202 or "id" not in message or "method" not in message:
            await response.aclose()
            return
        if "text/event-stream" in kind:
            # Read the stream in the background; the reply arrives through on_message.
            task = asyncio.create_task(self._stream(response))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return
        try:
            body = json.loads(await response.aread())
        except (json.JSONDecodeError, httpx.HTTPError):
            raise McpError("MCP 服务返回了无效的 JSON") from None
        finally:
            await response.aclose()
        for item in body if isinstance(body, list) else [body]:
            if isinstance(item, dict):
                await self.on_message(item)

    async def _stream(self, response: httpx.Response) -> None:
        data: list[str] = []
        try:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    payload, data = "\n".join(data), []
                    try:
                        message = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for item in message if isinstance(message, list) else [message]:
                        if isinstance(item, dict):
                            await self.on_message(item)
        except httpx.HTTPError:
            pass
        finally:
            await response.aclose()

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.session_id:
            with contextlib.suppress(httpx.HTTPError):
                await self.client.delete(
                    self.url, headers={**self.headers, "Mcp-Session-Id": self.session_id}
                )
        await self.client.aclose()


class McpClient:
    def __init__(self, transport: Transport, name: str, timeout: float):
        self.transport, self.name, self.timeout = transport, name, timeout
        self.transport.on_message = self._dispatch
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future] = {}
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.instructions = ""
        self.background: set[asyncio.Task] = set()
        self.on_tools_changed: Callable[[], Coroutine[Any, Any, None]] | None = None

    @property
    def alive(self) -> bool:
        return self.transport.alive

    async def _dispatch(self, message: dict) -> None:
        if "method" in message:
            method = message["method"]
            if "id" in message:
                # A request from the server.
                if method == "ping":
                    reply: dict = {"jsonrpc": "2.0", "id": message["id"], "result": {}}
                else:
                    reply = {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": f"Method not supported: {method}"},
                    }
                with contextlib.suppress(McpError):
                    await self.transport.send(reply)
            elif method == "notifications/tools/list_changed" and self.on_tools_changed:
                task = asyncio.create_task(self.on_tools_changed())
                self.background.add(task)
                task.add_done_callback(self.background.discard)
            return
        future = self.pending.pop(message.get("id"), None)  # type: ignore[arg-type]
        if future and not future.done():
            future.set_result(message)

    async def request(self, method: str, params: dict | None = None, seconds: float | None = None):
        key = next(self.ids)
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        message: dict = {"jsonrpc": "2.0", "id": key, "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self.transport.send(message)
            async with asyncio.timeout(seconds or self.timeout):
                while True:
                    done, _ = await asyncio.wait({future}, timeout=0.5)
                    if done:
                        break
                    if not self.alive:
                        raise McpError("MCP 服务连接已断开")
            reply = future.result()
        except TimeoutError:
            with contextlib.suppress(McpError):
                await self.notify(
                    "notifications/cancelled", {"requestId": key, "reason": "timeout"}
                )
            raise McpError(f"MCP 请求 {method} 超时") from None
        finally:
            self.pending.pop(key, None)
        if "error" in reply:
            error = reply["error"] if isinstance(reply["error"], dict) else {}
            raise McpError(f"MCP 服务错误：{str(error.get('message', '未知错误'))[:500]}")
        return reply.get("result", {})

    async def notify(self, method: str, params: dict | None = None) -> None:
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self.transport.send(message)

    async def start(self) -> None:
        await self.transport.open()
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            seconds=max(self.timeout, 30),
        )
        self.server_info = result.get("serverInfo") or {}
        self.capabilities = result.get("capabilities") or {}
        self.instructions = str(result.get("instructions") or "")[:4000]
        if isinstance(self.transport, HttpTransport):
            self.transport.protocol = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        await self.notify("notifications/initialized")

    async def list_tools(self) -> list[dict]:
        if "tools" not in self.capabilities:
            return []
        tools: list[dict] = []
        cursor = None
        for _ in range(50):
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name")]
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, name: str, arguments: dict, seconds: float | None = None) -> dict:
        return await self.request(
            "tools/call", {"name": name, "arguments": arguments}, seconds=seconds
        )

    async def close(self) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(McpError("MCP 连接已关闭"))
        self.pending.clear()
        await self.transport.close()
