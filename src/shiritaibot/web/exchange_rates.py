from __future__ import annotations

import asyncio
import math
import time

import httpx


class ExchangeRates:
    """Cache the open-access USD rate and retain it during provider outages."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._rate: float | None = None
        self._updated_at: int | None = None
        self._expires = 0.0
        self._retry_at = 0.0

    async def get(self) -> dict:
        async with self._lock:
            now = time.time()
            if now >= max(self._expires, self._retry_at):
                try:
                    async with httpx.AsyncClient(timeout=8) as client:
                        response = await client.get("https://open.er-api.com/v6/latest/USD")
                        response.raise_for_status()
                        data = response.json()
                    if data.get("result") != "success" or data.get("base_code") != "USD":
                        raise ValueError("Invalid exchange-rate response")
                    rate = data["rates"]["CNY"]
                    updated = data["time_last_update_unix"]
                    next_update = data["time_next_update_unix"]
                    for value in (rate, updated, next_update):
                        if (
                            isinstance(value, bool)
                            or not isinstance(value, (int, float))
                            or not math.isfinite(value)
                            or value <= 0
                        ):
                            raise ValueError("Invalid exchange-rate value")
                    if next_update <= updated:
                        raise ValueError("Invalid exchange-rate update times")
                    self._rate = float(rate)
                    self._updated_at = int(updated)
                    self._expires = min(float(next_update), now + 86400)
                    self._retry_at = now + 300
                except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                    self._retry_at = now + 300
            return {
                "usd_cny": self._rate,
                "updated_at": self._updated_at,
                "stale": self._rate is not None and time.time() >= self._expires,
            }
