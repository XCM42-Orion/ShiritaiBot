import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from shiritaibot.config import McpServerConfig
from shiritaibot.core import Context
from shiritaibot.mcp import input_schema, tool_name
from shiritaibot.mcp.client import HttpTransport, McpClient

OWNER = Context("web:owner", "owner", mode="agent", owner=True)
SERVER = str(Path(__file__).parent / "fixtures" / "mcp_server.py")


def server_config(**changes):
    values = {
        "id": "fake",
        "enabled": True,
        "command": [sys.executable, "-I", SERVER],
        "env": {"MCP_TEST_TOKEN": "s3cret"},
        "risk": "normal",
        "tool_risks": {"echo": "safe"},
    }
    return McpServerConfig(**{**values, **changes})


async def configure(app, *servers):
    settings = app.config.value.model_copy(deep=True)
    settings.mcp_servers = list(servers)
    settings.policy.owner_default.preset = "full_access"
    await app.reconfigure(settings)


def test_tool_names_and_schemas():
    assert tool_name("fake", "env.read") == "mcp_fake_env_read"
    long = tool_name("server", "x" * 100)
    assert len(long) == 64 and long == tool_name("server", "x" * 100)
    assert input_schema({}) == {"type": "object", "properties": {}}
    assert "$schema" not in input_schema({"$schema": "x", "type": "object"})


def test_config_validation():
    with pytest.raises(ValueError):
        McpServerConfig(id="a", enabled=True, transport="http", url="ftp://x")
    with pytest.raises(ValueError):
        McpServerConfig(id="a", enabled=True, transport="stdio")
    with pytest.raises(ValueError):
        McpServerConfig(id="a", cwd="relative/path")
    assert McpServerConfig(id="a", env='{"A": "1"}').env == {"A": "1"}


async def test_stdio_server_tools_register_call_and_reload(app):
    await configure(app, server_config())
    status = app.mcp.listing()[0]
    assert status["status"] == "connected", status["error"]
    assert status["server"]["name"] == "fake"
    # Pagination brought in the third tool.
    tools = {tool["original"]: tool for tool in status["tools"]}
    assert set(tools) == {"echo", "fail", "env.read"}
    assert tools["echo"]["risk"] == "safe" and tools["echo"]["read_only_hint"]
    assert app.registry.tools["mcp_fake_fail"].risk == "normal"
    assert app.registry.tools["mcp_fake_echo"].plugin == "mcp:fake"

    echo = json.loads(await app.registry.execute(OWNER, "mcp_fake_echo", {"text": "hi"}))
    assert echo == {"content": "echo:hi"}
    failed = json.loads(await app.registry.execute(OWNER, "mcp_fake_fail", {}))
    assert "boom" in failed["error"]
    invalid = json.loads(await app.registry.execute(OWNER, "mcp_fake_echo", {}))
    assert "Schema" in invalid["error"]

    # The credential-like env value moved into the Vault and still reaches the server.
    stored = app.config.value.mcp_servers[0].env["MCP_TEST_TOKEN"]
    assert stored.startswith("secret:")
    token = json.loads(await app.registry.execute(OWNER, "mcp_fake_env_read", {}))
    assert token == {"content": "s3cret", "structured": {"token": "s3cret"}}

    # A crashed server is restarted on the next call.
    server = app.mcp.servers["fake"]
    server.client.transport.process.kill()
    await server.client.transport.process.wait()
    await asyncio.sleep(0.1)
    echo = json.loads(await app.registry.execute(OWNER, "mcp_fake_echo", {"text": "again"}))
    assert echo == {"content": "echo:again"}

    # Unchanged configuration keeps the connection; disabled tools disappear.
    client = app.mcp.servers["fake"].client
    await app.reconfigure(app.config.value.model_copy(deep=True))
    assert app.mcp.servers["fake"].client is client
    changed = app.config.value.model_copy(deep=True)
    changed.mcp_servers[0].disabled_tools = ["fail"]
    await app.reconfigure(changed)
    assert "mcp_fake_fail" not in app.registry.tools and "mcp_fake_echo" in app.registry.tools

    await configure(app)
    assert not any(name.startswith("mcp_") for name in app.registry.tools)


async def test_broken_server_reports_error(app):
    await configure(app, server_config(command=[sys.executable, "-c", "pass"], timeout=5))
    status = app.mcp.listing()[0]
    assert status["status"] == "error" and status["error"]
    assert not any(name.startswith("mcp_") for name in app.registry.tools)


async def test_streamable_http_json_and_sse():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            seen.append(("DELETE", request.headers.get("mcp-session-id")))
            return httpx.Response(200)
        message = json.loads(request.content)
        seen.append((message.get("method"), request.headers.get("mcp-session-id")))
        if "id" not in message:
            return httpx.Response(202)
        if message["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                headers={"Mcp-Session-Id": "abc"},
            )
        if message["method"] == "tools/list":
            tools = [{"name": "t", "inputSchema": {"type": "object"}}]
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": message["id"], "result": {"tools": tools}}
            )
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }
        )
        return httpx.Response(
            200,
            content=f"event: message\ndata: {body}\n\n".encode(),
            headers={"Content-Type": "text/event-stream"},
        )

    transport = HttpTransport("https://mcp.example/mcp", {"Authorization": "Bearer x"}, 10)
    transport.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = McpClient(transport, "remote", 10)
    await client.start()
    assert [t["name"] for t in await client.list_tools()] == ["t"]
    result = await client.call_tool("t", {})
    assert result["content"][0]["text"] == "ok"
    assert transport.protocol == "2025-03-26"
    assert seen[0] == ("initialize", None) and seen[-1] == ("tools/call", "abc")
    await client.close()
    assert seen[-1] == ("DELETE", "abc")
