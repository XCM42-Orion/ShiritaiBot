import json

import httpx
import pytest
from fastapi.testclient import TestClient

from shiritaibot.config import Settings, WebSearchConfig
from shiritaibot.core import Context, PublicError
from shiritaibot.tools.builtin import register_builtin
from shiritaibot.tools.web_search import search_web
from shiritaibot.web import create_app


@pytest.mark.parametrize("provider", ["tavily", "serpapi", "deepseek"])
async def test_search_provider_contract_and_result_shape(app, monkeypatch, provider):
    config = WebSearchConfig(provider=provider, max_results=2)
    getattr(config, provider).api_key = "secret:search"
    config.tavily.search_depth = "advanced"
    config.deepseek.need_content = True
    await app.vault.set("search", "private-search-key")
    requests = []

    def handle(request):
        requests.append(request)
        if provider == "serpapi":
            assert request.method == "GET"
            assert str(request.url).startswith("https://serpapi.com/search.json?")
            assert request.url.params["q"] == "测试"
            assert request.url.params["api_key"] == "private-search-key"
            assert request.url.params["hl"] == "zh-cn"
            assert request.url.params["gl"] == "cn"
            return httpx.Response(
                200,
                json={
                    "organic_results": [
                        {"title": str(i), "link": f"https://example.com/{i}", "snippet": "摘要"}
                        for i in range(4)
                    ]
                },
            )
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer private-search-key"
        payload = json.loads(request.content)
        assert payload["query"] == "测试"
        if provider == "tavily":
            assert str(request.url) == "https://api.tavily.com/search"
            assert payload["max_results"] == 2
            assert payload["search_depth"] == "advanced"
            assert payload["topic"] == "general"
        else:
            assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
            assert payload["count"] == 2
            assert payload["need_content"] is True
            assert payload["localization"] == ""
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "来源",
                        "url": "https://example.com",
                        "summary": "长摘要",
                        "content": "",
                    }
                ]
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    result = await search_web(config, app.vault, {"query": " 测试 "})
    assert result["provider"] == provider
    assert len(result["results"]) <= 2
    assert result["results"][0]["url"].startswith("https://example.com")
    assert result["results"][0]["content"] == ("摘要" if provider == "serpapi" else "长摘要")
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["status", "body", "network", "json"])
async def test_search_errors_never_expose_credentials(app, monkeypatch, failure):
    await app.vault.set("search", "private-search-key")
    config = WebSearchConfig(provider="serpapi", serpapi={"api_key": "secret:search"})

    def handle(request):
        if failure == "network":
            raise httpx.ConnectError(str(request.url), request=request)
        if failure == "json":
            return httpx.Response(200, text="private-search-key")
        return httpx.Response(
            401 if failure == "status" else 200, json={"error": "private-search-key"}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    with pytest.raises(PublicError) as error:
        await search_web(config, app.vault, {"query": "test"})
    assert "private-search-key" not in str(error.value)
    assert "SerpAPI" in str(error.value)


async def test_search_registration_follows_enabled_selected_provider_and_credentials(app):
    app.config.value = Settings(web_search={"tavily": {"api_key": ""}})
    await register_builtin(app)
    assert "web_search" not in app.registry.tools
    await app.vault.set("search", "test-key")
    app.config.value.web_search.tavily.api_key = "secret:search"
    await register_builtin(app)
    assert "web_search" in app.registry.tools
    app.config.value.web_search.provider = "deepseek"
    app.config.value.web_search.deepseek.api_key = ""
    await register_builtin(app)
    assert "web_search" not in app.registry.tools
    app.config.value.web_search.deepseek.api_key = "secret:search"
    await register_builtin(app)
    assert "web_search" in app.registry.tools
    handler = app.registry.tools["web_search"].handler
    for query in [" ", "x" * 129]:
        with pytest.raises(PublicError, match="1–128"):
            await handler(Context("web:owner", "owner"), {"query": query})
    app.config.value.web_search.enabled = False
    await register_builtin(app)
    assert "web_search" not in app.registry.tools


def test_search_settings_migration_encryption_and_validation(tmp_path):
    assert (
        Settings.model_validate({"tavily_key": "secret:old"}).web_search.tavily.api_key
        == "secret:old"
    )
    settings = Settings.model_validate(
        {"tavily_key": "secret:old", "web_search": {"provider": "serpapi"}}
    )
    assert settings.web_search.provider == "serpapi"
    with TestClient(create_app(tmp_path, token="admin")) as client:
        client.headers["Authorization"] = "Bearer admin"
        config = client.get("/api/config").json()
        config.pop("web_search")
        config["tavily_key"] = "legacy-private-key"
        assert client.put("/api/config", json=config).status_code == 200
        config = client.get("/api/config").json()
        assert config["web_search"]["tavily"]["api_key"].startswith("secret:")
        for provider in ("tavily", "serpapi", "deepseek"):
            config["web_search"][provider]["api_key"] = f"{provider}-private-key"
        assert client.put("/api/config", json=config).status_code == 200
        saved = client.get("/api/config")
        assert "private-key" not in saved.text
        assert "private-key" not in (tmp_path / "config.toml").read_text()
        assert b"private-key" not in (tmp_path / "state.sqlite3").read_bytes()
        assert client.put("/api/config", json=saved.json()).status_code == 200
        config["web_search"]["provider"] = "unknown"
        error = client.put("/api/config", json=config)
        assert error.status_code == 422 and "private-key" not in error.text
