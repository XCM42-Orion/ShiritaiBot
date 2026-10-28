import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shiritaibot.config import ConfigService, ModelConfig, ProviderConfig, Settings
from shiritaibot.core import PublicError
from shiritaibot.web import create_app


def configure(app, **provider_options):
    app.config.value = Settings(
        providers=[ProviderConfig(id="p", api_key="", **provider_options)],
        models=[ModelConfig(id="m", provider="p", model="m")],
        routes={"test": ["m"]},
    )


async def install_transport(app, handler):
    await app.llm.client.aclose()
    app.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_old_config_defaults_and_json_null_roundtrip(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[[providers]]\nid="p"\n[[models]]\nid="m"\nprovider="p"\nmodel="m"\n',
        encoding="utf-8",
    )
    service = ConfigService(tmp_path)
    await service.load()
    assert service.value.providers[0].max_retries == 2
    assert service.value.providers[0].proxy == ""
    model = service.value.models[0]
    assert model.modalities == ["text"] and model.max_context == 0 and model.extra_body == {}
    model.extra_body = {"temperature": 0.5, "nested": {"stop": ["结束", None], "flag": False}}
    model.max_context = 32000
    model.modalities = ["text", "image", "audio", "video"]
    await service.save(service.value)
    restored = ConfigService(tmp_path)
    await restored.load()
    assert restored.value == service.value


@pytest.mark.parametrize(
    "options",
    [
        {"max_retries": -1},
        {"max_retries": 11},
        {"proxy": "ftp://localhost:8080"},
        {"proxy": "http://"},
        {"proxy": "http://localhost:invalid"},
        {"proxy": "http://localhost:8080/path"},
    ],
)
def test_invalid_provider_config(options):
    with pytest.raises(ValidationError):
        ProviderConfig(id="p", **options)


@pytest.mark.parametrize(
    "options",
    [
        {"extra_body": "{"},
        {"extra_body": "[]"},
        {"extra_body": "null"},
        {"extra_body": {"stream": True}},
        {"extra_body": {"messages": []}},
        {"extra_body": '{"temperature": NaN}'},
        {"max_context": -1},
        {"max_context": 4096},
        {"modalities": []},
        {"modalities": ["unknown"]},
    ],
)
def test_invalid_model_config(options):
    with pytest.raises(ValidationError):
        ModelConfig(id="m", provider="p", model="m", **options)


@pytest.mark.parametrize("operation", ["discover", "complete"])
@pytest.mark.parametrize("retries", [0, 1, 3])
@pytest.mark.parametrize("failure", ["timeout", "status"])
async def test_retry_budget_and_exhaustion(app, monkeypatch, operation, retries, failure):
    configure(app, max_retries=retries)
    sleep = AsyncMock()
    monkeypatch.setattr("shiritaibot.llm.asyncio", SimpleNamespace(sleep=sleep))
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive-provider-message", request=request)
        return httpx.Response(503, headers={"retry-after": "0"})

    await install_transport(app, handle)
    with pytest.raises(PublicError) as error:
        if operation == "discover":
            await app.llm.discover(app.config.value.providers[0])
        else:
            await app.llm.complete("test", "sys", [], [])
    assert len(requests) == retries + 1
    assert sleep.await_count == retries
    assert "sensitive-provider-message" not in str(error.value)


async def test_timeout_then_success_and_nonretryable_fallback(app, monkeypatch):
    configure(app, max_retries=2)
    app.config.value.models.insert(0, ModelConfig(id="bad", provider="p", model="bad"))
    app.config.value.routes["test"].insert(0, "bad")
    monkeypatch.setattr("shiritaibot.llm.asyncio", SimpleNamespace(sleep=AsyncMock()))
    calls = []

    def handle(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "bad":
            return httpx.Response(401)
        if calls.count("m") == 1:
            raise httpx.ConnectTimeout("timeout", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await install_transport(app, handle)
    assert (await app.llm.complete("test", "sys", [], [])).text == "ok"
    assert calls == ["bad", "m", "m"]
    assert len(await app.store.list("usage")) == 1


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic", "gemini"])
async def test_custom_parameters_sent_for_all_protocols(app, protocol):
    configure(app, protocol=protocol)
    extra = {"temperature": 0.25, "stop": None}
    if protocol == "gemini":
        extra = {"generationConfig": extra}
    app.config.value.models[0].extra_body = extra
    responses = {
        "chat_completions": {"choices": [{"message": {"content": "ok"}}]},
        "responses": {"output": [{"type": "message", "content": [{"text": "ok"}]}]},
        "anthropic": {"content": [{"type": "text", "text": "ok"}]},
        "gemini": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
    }
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=responses[protocol])

    await install_transport(app, handle)
    assert (
        await app.llm.complete("test", "sys", [{"role": "user", "content": "hi"}], [])
    ).text == "ok"
    params = bodies[0]["generationConfig"] if protocol == "gemini" else bodies[0]
    assert params["temperature"] == 0.25 and params["stop"] is None
    if protocol == "gemini":
        assert params["maxOutputTokens"] == 4096
    assert app.config.value.models[0].extra_body == extra


async def test_context_limit_skips_small_model_and_reports_exhaustion(app):
    configure(app)
    app.config.value.models.insert(
        0,
        ModelConfig(
            id="small",
            provider="p",
            model="small",
            max_output=64,
            max_context=100,
        ),
    )
    app.config.value.routes["test"] = ["small", "m"]
    called = []

    def handle(request):
        called.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await install_transport(app, handle)
    messages = [{"role": "user", "content": "长文本" * 200}]
    assert (await app.llm.complete("test", "sys", messages, [])).text == "ok"
    assert called == ["m"]
    app.config.value.routes["test"] = ["small"]
    with pytest.raises(PublicError, match="上下文超过上限"):
        await app.llm.complete("test", "sys", messages, [])
    assert called == ["m"]


async def test_proxy_routes_discovery_and_completion_without_affecting_default_client(app):
    requests = []

    async def proxy(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            requests.append(headers.split(b"\r\n", 1)[0].decode())
            for line in headers.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    await reader.readexactly(int(line.split(b":", 1)[1]))
            body = json.dumps(
                {"data": [{"id": "m"}]}
                if headers.startswith(b"GET")
                else {
                    "choices": [{"message": {"content": "proxy-ok"}}],
                }
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        configure(app, base_url="http://provider.invalid/v1", proxy=f"http://127.0.0.1:{port}")
        await install_transport(
            app,
            lambda _: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "direct-ok"}}]},
            ),
        )
        assert (await app.llm.discover(app.config.value.providers[0]))[0]["id"] == "m"
        assert (await app.llm.complete("test", "sys", [], [])).text == "proxy-ok"
        app.config.value.providers[0].proxy = ""
        assert (await app.llm.complete("test", "sys", [], [])).text == "direct-ok"
    assert requests == [
        "GET http://provider.invalid/v1/models HTTP/1.1",
        "POST http://provider.invalid/v1/chat/completions HTTP/1.1",
    ]


def test_api_save_reload_and_invalid_json_is_not_saved(tmp_path):
    with TestClient(create_app(tmp_path, token="admin")) as client:
        client.headers["Authorization"] = "Bearer admin"
        cfg = client.get("/api/config").json()
        cfg["providers"] = [{"id": "p", "max_retries": 4, "proxy": "socks5://localhost:1080"}]
        cfg["models"] = [
            {
                "id": "m",
                "provider": "p",
                "model": "m",
                "max_context": 64000,
                "modalities": ["text", "image"],
                "extra_body": '{"temperature":0.5,"stop":null}',
            }
        ]
        assert client.put("/api/config", json=cfg).status_code == 200
        saved = client.get("/api/config").json()
        assert saved["models"][0]["extra_body"] == {"temperature": 0.5, "stop": None}
        cfg["models"][0]["extra_body"] = '{"temperature":'
        assert client.put("/api/config", json=cfg).status_code == 422
        assert client.get("/api/config").json() == saved
    with TestClient(create_app(tmp_path, token="admin")) as client:
        client.headers["Authorization"] = "Bearer admin"
        assert client.get("/api/config").json() == saved
