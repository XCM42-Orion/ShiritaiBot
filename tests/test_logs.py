import io
import logging

from fastapi.testclient import TestClient

from shiritaibot.core.logs import (
    LogManager,
    TextFormatter,
    brief,
    describe_session,
    get_logger,
)
from shiritaibot.plugin_api import PluginContext
from shiritaibot.web import create_app


def test_logger_writes_bracketed_text_and_structured_feed(tmp_path):
    manager = LogManager(tmp_path, console=False).install()
    try:
        log = get_logger("qq")
        log.log("你好", "群 1", "张三(42)")
        log.debug("hidden at info level")
        log.bind("事件 abcd").warning("slow")
        try:
            raise ValueError("boom")
        except ValueError as exc:
            log.error("failed", exc=exc)
    finally:
        manager.uninstall()
    lines = manager.tail(50)
    assert any(
        line.startswith("[LOG][") and line.endswith("[qq][群 1][张三(42)]: 你好") for line in lines
    )
    assert not any("hidden" in line for line in lines)
    assert any("Traceback" in line for line in lines)
    feed = manager.entries(50)
    assert feed["sources"] == ["qq"]
    assert [e["level"] for e in feed["entries"]] == ["LOG", "WARNING", "ERROR"]
    assert feed["entries"][0]["tags"] == ["群 1", "张三(42)"]
    assert feed["entries"][2]["error"] == "ValueError"
    assert [e["message"] for e in manager.entries(50, level="warning")["entries"]] == [
        "slow",
        "failed",
    ]
    assert [e["message"] for e in manager.entries(50, query="abcd")["entries"]] == ["slow"]


def test_debug_level_and_console_colors(tmp_path):
    manager = LogManager(tmp_path, level="debug", console=False).install()
    try:
        get_logger("x").debug("visible")
    finally:
        manager.uninstall()
    assert any("[DEBUG]" in line and "visible" in line for line in manager.tail(10))
    record = logging.LogRecord("shiritaibot.x", logging.WARNING, "", 0, "careful", (), None)
    record.source, record.tags = "x", ("t",)
    colored = TextFormatter(color=True).format(record)
    assert "\033[33m[WARNING]" in colored and "careful" in colored
    stream = io.StringIO()
    assert not LogManager._colorful(stream)


def test_readable_helpers():
    assert brief({"query": "  hello\n world ", "empty": "", "n": 3}) == "query=hello world, n=3"
    assert brief("x" * 100, 10) == "x" * 9 + "…"
    assert brief(["a", "b"]) == "a、b"
    assert brief([{"a": 1}]) == "1 项"
    assert describe_session("group:qq:123") == "qq·群 123"
    assert describe_session("private:qq:9") == "qq·私聊 9"
    assert describe_session("web:owner") == "WebUI"
    assert describe_session("background:proactive:abcdef1234") == "事件 abcdef12"


def test_plugin_context_exposes_logger(tmp_path):
    async def resolve(ref):
        return ref

    async def emit(event):
        return ""

    context = PluginContext("demo", {}, tmp_path, resolve, emit)
    assert context.logger.source == "插件:demo"


def test_structured_log_endpoint(tmp_path):
    with TestClient(create_app(tmp_path, token="admin")) as client:
        assert client.get("/api/logs").status_code == 401
        client.headers["Authorization"] = "Bearer admin"
        client.post("/api/messages", json={"text": "日志测试消息", "session": "web:owner"})
        body = client.get("/api/logs").json()
        assert "ShiritaiBot" in body["sources"]
        assert any(
            e["source"] == "WebUI" and e["tags"] == ["主人"] and e["message"] == "日志测试消息"
            for e in body["entries"]
        )
        only = client.get("/api/logs", params={"source": "WebUI"}).json()["entries"]
        assert only and all(e["source"] == "WebUI" for e in only)
