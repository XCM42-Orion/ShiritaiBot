import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from shiritaibot.web import create_app, exchange_rates
from shiritaibot.web.exchange_rates import ExchangeRates


def payload():
    return {
        "result": "success",
        "base_code": "USD",
        "rates": {"CNY": 7.1234},
        "time_last_update_unix": 1000,
        "time_next_update_unix": 87400,
    }


def mock_provider(monkeypatch, handle):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )


async def test_cache_coalesces_requests_and_retains_stale_rate(monkeypatch):
    now = [2000]
    monkeypatch.setattr(exchange_rates, "time", SimpleNamespace(time=lambda: now[0]))
    requests = []

    async def handle(request):
        requests.append(request)
        assert str(request.url) == "https://open.er-api.com/v6/latest/USD"
        assert "authorization" not in request.headers
        await asyncio.sleep(0)
        if len(requests) == 2:
            raise httpx.ConnectError("offline", request=request)
        data = payload()
        if len(requests) > 2:
            data.update(time_last_update_unix=87400, time_next_update_unix=173800)
        return httpx.Response(200, json=data)

    mock_provider(monkeypatch, handle)
    rates = ExchangeRates()
    results = await asyncio.gather(*(rates.get() for _ in range(5)))
    assert all(item == {"usd_cny": 7.1234, "updated_at": 1000, "stale": False} for item in results)
    assert len(requests) == 1
    now[0] = 87401
    assert await rates.get() == {"usd_cny": 7.1234, "updated_at": 1000, "stale": True}
    await rates.get()
    assert len(requests) == 2
    now[0] += 301
    assert await rates.get() == {"usd_cny": 7.1234, "updated_at": 87400, "stale": False}
    assert len(requests) == 3


@pytest.mark.parametrize(
    "data",
    [
        {"result": "error"},
        [],
        {**payload(), "base_code": "CNY"},
        {**payload(), "time_next_update_unix": 0},
        {**payload(), "time_last_update_unix": "bad"},
        *({**payload(), "rates": {"CNY": value}} for value in [0, -1, True, "7.1", None]),
    ],
)
async def test_invalid_response_is_not_cached_and_retries_are_throttled(monkeypatch, data):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=data)

    mock_provider(monkeypatch, handle)
    rates = ExchangeRates()
    assert await rates.get() == {"usd_cny": None, "updated_at": None, "stale": False}
    await rates.get()
    assert len(requests) == 1


@pytest.mark.parametrize("status", [429, 500])
async def test_provider_http_failure(monkeypatch, status):
    mock_provider(monkeypatch, lambda _: httpx.Response(status))
    assert (await ExchangeRates().get())["usd_cny"] is None


def test_exchange_rate_endpoint_requires_authentication(tmp_path, monkeypatch):
    calls = []

    async def get(self):
        calls.append(True)
        return {"usd_cny": 7.1234, "updated_at": 1000, "stale": False}

    monkeypatch.setattr(ExchangeRates, "get", get)
    with TestClient(create_app(tmp_path, token="admin")) as client:
        assert client.get("/api/exchange-rate").status_code == 401
        assert not calls
        client.headers["Authorization"] = "Bearer admin"
        response = client.get("/api/exchange-rate")
        assert response.status_code == 200
        assert response.json() == {"usd_cny": 7.1234, "updated_at": 1000, "stale": False}
        assert len(calls) == 1
