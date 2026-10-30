import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_permissions import eventually
from websockets.asyncio.server import serve

from shiritaibot.adapters.onebot11 import OneBot, segments
from shiritaibot.config import AdapterConfig, SubagentConfig
from shiritaibot.core import Context, Reply
from shiritaibot.core.process import run_process
from shiritaibot.subagent import command_for
from shiritaibot.web import create_app


async def test_two_onebot_instances_route_and_reply_without_crossing_sessions(app):
    delivered = []

    class FakeLLM:
        async def complete(self, route, system, messages, tools, **kwargs):
            return Reply(text="reply:" + messages[-1]["content"].split("\n")[0])

    app.runtime.llm = FakeLLM()

    async def peer(ws):
        async for raw in ws:
            request = json.loads(raw)
            action = request["action"]
            await ws.send(
                json.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "echo": request["echo"],
                        "data": {"user_id": 100}
                        if action == "get_login_info"
                        else {"message_id": 999},
                    }
                )
            )
            if action == "get_login_info":
                await asyncio.sleep(0.02)
                await ws.send(
                    json.dumps(
                        {
                            "post_type": "message",
                            "message_type": "private",
                            "user_id": 1,
                            "message_id": 77,
                            "message": [{"type": "text", "data": {"text": "hello"}}],
                        }
                    )
                )
            elif action == "send_private_msg":
                delivered.append(request["params"])

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        cfg = app.config.value.model_copy(deep=True)
        cfg.adapters = [
            AdapterConfig(id=key, url=f"ws://127.0.0.1:{port}", owner_id="1", self_id="100")
            for key in ("a", "b")
        ]
        await app.reconfigure(cfg)

        async def finished():
            return len(delivered) == 2

        await eventually(finished)
        assert (await app.store.history("private:a:1"))[-1]["content"] == "reply:hello"
        assert (await app.store.history("private:b:1"))[-1]["content"] == "reply:hello"
        assert all(row["user_id"] == 1 for row in delivered)
        await app.adapters.close()


async def test_onebot_echo_concurrent_calls_and_disconnect(app):
    received = []

    async def handler(ws):
        async for raw in ws:
            request = json.loads(raw)
            data = (
                {"user_id": 100}
                if request["action"] == "get_login_info"
                else {"action": request["action"]}
            )
            await ws.send(
                json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": request["echo"]})
            )

    async def sink(bot, event):
        received.append(event)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        bot = OneBot(
            AdapterConfig(id="a", url=f"ws://127.0.0.1:{port}", self_id="100"),
            app.vault,
            app.store,
            sink,
        )
        task = asyncio.create_task(bot.run())
        try:

            async def connected():
                return bot.status == "connected"

            await eventually(connected)
            results = await asyncio.gather(bot.call("a", {}), bot.call("b", {}))
            assert results == [{"action": "a"}, {"action": "b"}]
            assert not bot.pending
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_cq_parser_handles_escaped_at_and_text():
    assert segments("hello [CQ:at,qq=123]&amp;") == [
        {"type": "text", "data": {"text": "hello "}},
        {"type": "at", "data": {"qq": "123"}},
        {"type": "text", "data": {"text": "&"}},
    ]


async def test_process_bounds_output_and_cancels(tmp_path):
    result = await run_process([sys.executable, "-c", "print('x'*10000)"], tmp_path, limit=100)
    assert result["truncated"] and len(result["stdout"]) == 100
    marker = tmp_path / "must-not-exist"
    with pytest.raises(TimeoutError):
        await run_process(
            [
                sys.executable,
                "-c",
                "import time,pathlib; time.sleep(2); pathlib.Path('must-not-exist').write_text('bad')",
            ],
            tmp_path,
            timeout=0.1,
        )
    assert not marker.exists()


def test_subagent_arguments_never_disable_sandbox():
    for kind in ["codex", "claude_code"]:
        config = SubagentConfig(id="a", kind=kind)
        command = command_for(config, Path("workspace"))
        assert not any("bypass" in p for p in command)
        assert "read-only" in command if kind == "codex" else "Read,Glob,Grep" in command


async def test_subagent_generic_executes_and_reports_completion(app):
    app.config.value.subagents = [
        SubagentConfig(
            id="local",
            kind="generic",
            command=[sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        )
    ]
    row = await app.subagents.start(Context("web:owner", "owner", "agent", True), "local", "hello")

    async def complete():
        result = await app.store.get("subagent_run", row["id"])
        return result if result["status"] == "completed" else None

    result = await eventually(complete)
    assert result["result"].strip() == "HELLO"


def test_http_auth_crud_and_validation(tmp_path):
    with TestClient(create_app(tmp_path, token="test-token")) as client:
        assert client.get("/api/config").status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/records/secret").status_code == 404
        assert client.put("/api/secrets/key", json={"value": "secret-value"}).status_code == 200
        assert client.get("/api/secrets").json() == ["key"]
        assert "secret-value" not in client.get("/api/config").text
        # Memory is read-only over HTTP; the Agent writes it through tools.
        assert client.post("/api/memory", json={"content": "持久记忆"}).status_code == 405
        assert client.get("/api/memory").json() == []
        assert client.get("/api/memory/status").json()["model"] is None
        assert (
            client.post(
                "/api/routines", json={"kind": "reminder", "title": "bad", "due": "2026-01-01"}
            ).status_code
            == 422
        )
        assert (
            client.post("/api/messages", json={"session": "group:a:1", "text": "no"}).status_code
            == 422
        )
        assert client.get("/api/health", headers={"Host": "attacker.test"}).status_code == 400
        with client.websocket_connect("/api/ws") as ws:
            ws.send_text("test-token")
            client.post("/api/routines", json={"kind": "todo", "title": "WS test"})
            assert ws.receive_json()["kind"] == "routine"


async def test_qq_file_reference_cannot_cross_session(app):
    app.config.value.policy.owner_default.tools["qq_file_download"] = "allow"
    await app.store.put("qq_file", "ref", {"adapter": "a", "data": {}}, "private:a:1")
    result = await app.registry.execute(
        Context("private:a:2", "owner", "agent", True), "qq_file_download", {"ref": "ref"}
    )
    assert "不属于" in result


def test_feishu_fixed_operation_validation():
    # Concrete plugins are local and intentionally absent from a clean checkout.
    operations = pytest.importorskip("shiritaibot.plugin.feishu.operations")
    LarkError, build_args = operations.LarkError, operations.build_args

    assert "--query=--evil" in build_args("search_docs", {"query": "--evil"})
    with pytest.raises(LarkError):
        build_args("delete", {})
    with pytest.raises(LarkError):
        build_args("search_docs", {"query": "x", "output": "secret.txt"})
    with pytest.raises(LarkError):
        build_args("document", {"doc": "https://attacker.test/docx/a"})


def test_tool_catalog_and_session_policy_api(tmp_path):
    with TestClient(create_app(tmp_path, token="test-token")) as client:
        assert client.get("/api/tools").status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        catalog = client.get("/api/tools").json()
        assert catalog["source"] == "default" and catalog["policy"]["mode"] == "agent"
        tools = {tool["name"]: tool for tool in catalog["tools"]}
        assert tools["shell"]["safe"] is False and tools["shell"]["permission"] == "manual"
        assert tools["current_time"]["safe"] is True
        assert (
            client.get("/api/tools", params={"session": "background:proactive:x"}).status_code
            == 422
        )
        cfg = client.get("/api/config").json()
        cfg["policy"]["sessions"]["group:qq:123"] = {
            "mode": "agent",
            "preset": "full_access",
            "tools": {"shell": "deny"},
        }
        assert client.put("/api/config", json=cfg).status_code == 200
        selected = client.get("/api/tools", params={"session": "group:qq:123"}).json()
        assert selected["source"] == "session" and selected["policy"]["mode"] == "agent"
        assert next(t for t in selected["tools"] if t["name"] == "shell")["decision"] == "deny"
        cfg["policy"]["sessions"]["group:qq:123"]["mode"] = "chat"
        assert client.put("/api/config", json=cfg).status_code == 200
        selected = client.get("/api/tools", params={"session": "group:qq:123"}).json()
        assert {t["name"] for t in selected["tools"] if t["decision"] != "deny"} == {
            "artifact_read",
            "current_time",
        }
        cfg["policy"]["sessions"]["group:qq:123"]["tools"]["shell"] = "unknown"
        assert client.put("/api/config", json=cfg).status_code == 422
        cfg["policy"]["sessions"] = {"background:x": {"mode": "agent"}}
        assert client.put("/api/config", json=cfg).status_code == 422
