"""A tiny stdio MCP server for tests (newline-delimited JSON-RPC)."""

import json
import os
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo text back",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {"name": "fail", "description": "Always fails", "inputSchema": {"type": "object"}},
    {"name": "env.read", "description": "Read MCP_TEST_TOKEN", "inputSchema": {}},
]


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method, key = message.get("method"), message.get("id")
    if key is None:
        continue
    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": key,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "fake", "version": "1"},
                },
            }
        )
    elif method == "tools/list":
        cursor = (message.get("params") or {}).get("cursor")
        page = TOOLS[:2] if not cursor else TOOLS[2:]
        result = {"tools": page}
        if not cursor:
            result["nextCursor"] = "page2"
        send({"jsonrpc": "2.0", "id": key, "result": result})
    elif method == "tools/call":
        params = message["params"]
        if params["name"] == "echo":
            # A server-to-client ping before replying exercises request handling.
            send({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"})
            content = [{"type": "text", "text": "echo:" + params["arguments"]["text"]}]
            send({"jsonrpc": "2.0", "id": key, "result": {"content": content}})
        elif params["name"] == "fail":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": key,
                    "result": {"isError": True, "content": [{"type": "text", "text": "boom"}]},
                }
            )
        else:
            token = os.environ.get("MCP_TEST_TOKEN", "")
            send(
                {
                    "jsonrpc": "2.0",
                    "id": key,
                    "result": {
                        "content": [{"type": "text", "text": token}],
                        "structuredContent": {"token": token},
                    },
                }
            )
    else:
        send({"jsonrpc": "2.0", "id": key, "error": {"code": -32601, "message": "nope"}})
