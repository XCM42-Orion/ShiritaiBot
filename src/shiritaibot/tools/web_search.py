"""Search providers with a shared result shape and secret-safe errors.

API contracts: docs.tavily.com/documentation/api-reference/endpoint/search,
serpapi.com/search-api, api-docs.deepseek.com/api/search.
"""

from __future__ import annotations

import httpx

from shiritaibot.config import WebSearchConfig
from shiritaibot.core import PublicError

LABELS = {"tavily": "Tavily", "serpapi": "SerpAPI", "deepseek": "DeepSeek"}


async def search_token(config: WebSearchConfig, vault) -> str:
    if not config.enabled:
        return ""
    return await vault.resolve(getattr(config, config.provider).api_key)


async def search_web(config: WebSearchConfig, vault, args: dict) -> dict:
    provider = config.provider
    label = LABELS[provider]
    token = await search_token(config, vault)
    if not token:
        raise PublicError(f"网页搜索未启用或未配置 {label} API Key")
    query = str(args.get("query", "")).strip()
    if not query or len(query) > 128:
        raise PublicError("搜索词须为 1–128 个字符")
    count = args.get("max_results", config.max_results)
    if type(count) is not int or not 1 <= count <= 10:
        raise PublicError("搜索结果数量须为 1–10")
    try:
        async with httpx.AsyncClient(timeout=config.timeout) as client:
            if provider == "tavily":
                response = await client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "query": query,
                        "max_results": count,
                        "search_depth": config.tavily.search_depth,
                        "topic": config.tavily.topic,
                    },
                )
            elif provider == "serpapi":
                response = await client.get(
                    "https://serpapi.com/search.json",
                    params={
                        "engine": "google",
                        "q": query,
                        "api_key": token,
                        "hl": config.serpapi.hl,
                        "gl": config.serpapi.gl,
                    },
                )
            else:
                response = await client.post(
                    "https://api.deepseek.com/v1/tools/search",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "query": query,
                        "count": count,
                        "need_content": config.deepseek.need_content,
                        "localization": config.deepseek.localization,
                    },
                )
            response.raise_for_status()
            data = response.json()
        if data.get("error"):
            raise PublicError(f"{label} 搜索失败，请检查配置或配额")
        rows = data.get("organic_results" if provider == "serpapi" else "results", [])
        return {
            "provider": provider,
            "query": query,
            "results": [
                {
                    "title": row.get("title", ""),
                    "url": row.get("url") or row.get("link", ""),
                    "content": row.get("content") or row.get("summary") or row.get("snippet", ""),
                }
                for row in rows[:count]
            ],
        }
    except httpx.HTTPStatusError as exc:
        # SerpAPI's URL contains its API key; never expose the exception or response body.
        raise PublicError(f"{label} 搜索失败（HTTP {exc.response.status_code}）") from None
    except httpx.RequestError:
        raise PublicError(f"无法连接 {label} 搜索服务，请稍后重试") from None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PublicError(f"{label} 搜索返回了无效数据") from None
