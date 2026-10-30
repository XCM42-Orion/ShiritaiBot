from __future__ import annotations

import asyncio
import json
import math
import time
from copy import deepcopy
from typing import Any

import httpx

from shiritaibot.core import PublicError, Reply, canonical, uid
from shiritaibot.core.logs import get_logger

log = get_logger("模型")


def plain(message: dict) -> dict:
    return {k: v for k, v in message.items() if not k.startswith("_")}


def merge_body(base: dict, extra: dict) -> dict:
    """Merge nested protocol parameters without changing defaults or configuration."""
    result = deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_body(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def estimated_tokens(body: dict) -> int:
    # A shared heuristic, not a provider tokenizer: ASCII ~4 chars/token,
    # other characters ~1 token. Include system text, tool schemas and opaque state.
    text = json.dumps(body, ensure_ascii=False)
    ascii_chars = sum(character.isascii() for character in text)
    return math.ceil(ascii_chars / 4) + len(text) - ascii_chars


def request_payload(
    protocol: str, model: str, system: str, messages: list[dict], tools: list[dict], max_output: int
) -> tuple[str, dict]:
    key = f"{protocol}:{model}"
    if protocol == "chat_completions":
        history = []
        for message in messages:
            data = plain(message)
            opaque = message.get("_opaque", {})
            if opaque.get("key") == key and "reasoning_content" in opaque.get("data", {}):
                data["reasoning_content"] = opaque["data"]["reasoning_content"]
            history.append(data)
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *history],
            "max_completion_tokens": max_output,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        return "/chat/completions", body
    if protocol == "responses":
        items = []
        for message in messages:
            opaque = message.get("_opaque", {})
            if opaque.get("key") == key:
                items.extend(opaque["data"])
                continue
            if message["role"] == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message["tool_call_id"],
                        "output": message["content"],
                    }
                )
            else:
                if message.get("content"):
                    items.append({"role": message["role"], "content": message["content"]})
                for call in message.get("tool_calls", []):
                    items.append(
                        {"type": "function_call", "call_id": call["id"], **call["function"]}
                    )
        return "/responses", {
            "model": model,
            "instructions": system,
            "input": items,
            "tools": [{"type": "function", **t["function"], "strict": False} for t in tools],
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "max_output_tokens": max_output,
        }
    if protocol == "anthropic":
        history = []
        for message in messages:
            opaque = message.get("_opaque", {})
            role = "user" if message["role"] == "tool" else message["role"]
            if opaque.get("key") == key:
                blocks = opaque["data"]
            elif message["role"] == "tool":
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                ]
            else:
                blocks = (
                    [{"type": "text", "text": message["content"]}] if message.get("content") else []
                )
                blocks += [
                    {
                        "type": "tool_use",
                        "id": c["id"],
                        "name": c["function"]["name"],
                        "input": json.loads(c["function"]["arguments"]),
                    }
                    for c in message.get("tool_calls", [])
                ]
            if not blocks:
                continue
            if history and history[-1]["role"] == role:
                history[-1]["content"].extend(blocks)
            else:
                history.append({"role": role, "content": list(blocks)})
        return "/messages", {
            "model": model,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": history,
            "max_tokens": max_output,
            "tools": [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ],
        }
    if protocol == "gemini":
        history, names = [], {}
        for message in messages:
            for call in message.get("tool_calls", []):
                names[call["id"]] = call["function"]["name"]
            opaque = message.get("_opaque", {})
            if opaque.get("key") == key:
                parts = opaque["data"]
            elif message["role"] == "tool":
                parts = [
                    {
                        "functionResponse": {
                            "name": names.get(message["tool_call_id"], "unknown"),
                            "response": {"result": message["content"]},
                        }
                    }
                ]
            else:
                parts = [{"text": message["content"]}] if message.get("content") else []
                parts += [
                    {
                        "functionCall": {
                            "name": c["function"]["name"],
                            "args": json.loads(c["function"]["arguments"]),
                        }
                    }
                    for c in message.get("tool_calls", [])
                ]
            role = "model" if message["role"] == "assistant" else "user"
            if not parts:
                continue
            if history and history[-1]["role"] == role:
                history[-1]["parts"].extend(parts)
            else:
                history.append({"role": role, "parts": list(parts)})

        def clean(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    k: clean(v)
                    for k, v in value.items()
                    if k not in {"additionalProperties", "$schema"}
                }
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": history,
            "generationConfig": {"maxOutputTokens": max_output},
        }
        if tools:
            body["tools"] = [{"functionDeclarations": [clean(t["function"]) for t in tools]}]
        return f"/models/{model}:generateContent", body
    raise PublicError("不支持的 LLM 协议")


def parse_reply(protocol: str, model: str, data: dict) -> Reply:
    key = f"{protocol}:{model}"
    calls, text = [], ""
    if protocol == "chat_completions":
        msg = data["choices"][0]["message"]
        return Reply(
            msg.get("content") or "",
            msg.get("tool_calls") or [],
            data.get("usage", {}),
            {"key": key, "data": msg},
        )
    if protocol == "responses":
        raw = data.get("output", [])
        for item in raw:
            if item["type"] == "message":
                text += "".join(
                    x.get("text", x.get("refusal", "")) for x in item.get("content", [])
                )
            elif item["type"] == "function_call":
                calls.append(
                    {
                        "id": item["call_id"],
                        "type": "function",
                        "function": {"name": item["name"], "arguments": item["arguments"]},
                    }
                )
        return Reply(text, calls, data.get("usage", {}), {"key": key, "data": raw})
    if protocol == "anthropic":
        raw = data.get("content", [])
        for block in raw:
            if block["type"] == "text":
                text += block["text"]
            elif block["type"] == "tool_use":
                calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {"name": block["name"], "arguments": canonical(block["input"])},
                    }
                )
        return Reply(text, calls, data.get("usage", {}), {"key": key, "data": raw})
    raw = data["candidates"][0]["content"]["parts"]
    for part in raw:
        if "text" in part and not part.get("thought"):
            text += part["text"]
        if "functionCall" in part:
            call = part["functionCall"]
            calls.append(
                {
                    "id": call.get("id", uid()),
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": canonical(call.get("args", {})),
                    },
                }
            )
    return Reply(text, calls, data.get("usageMetadata", {}), {"key": key, "data": raw})


def failure(provider_id: str, exc: Exception) -> str:
    """Activity detail for a failed call; the status code tells a bad path from a bad key."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{provider_id}: HTTP {exc.response.status_code}"
    return f"{provider_id}: {type(exc).__name__}"


class ProviderManager:
    def __init__(self, config, vault, store):
        self.config, self.vault, self.store = config, vault, store
        self.client = httpx.AsyncClient(follow_redirects=False, timeout=120)
        self.semaphore = asyncio.Semaphore(config.value.concurrency)

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, provider, method: str, url: str, **kwargs) -> httpx.Response:
        async def send(client):
            for attempt in range(provider.max_retries + 1):
                delay = min(20, 2**attempt)
                try:
                    response = await client.request(method, url, timeout=provider.timeout, **kwargs)
                except httpx.TimeoutException:
                    if attempt == provider.max_retries:
                        raise
                else:
                    if (
                        response.status_code not in {429, 500, 502, 503, 504}
                        or attempt == provider.max_retries
                    ):
                        response.raise_for_status()
                        return response
                    try:
                        delay = min(20, max(0, float(response.headers.get("retry-after", delay))))
                    except ValueError:
                        pass
                    await response.aclose()
                await asyncio.sleep(delay)
            raise AssertionError("unreachable retry state")

        if provider.proxy:
            # Scoped clients avoid stale proxy settings after saving or testing a draft.
            async with httpx.AsyncClient(
                proxy=provider.proxy, follow_redirects=False, trust_env=False
            ) as client:
                return await send(client)
        return await send(self.client)

    async def discover(self, provider) -> list[dict]:
        ref = provider.api_key
        token = await self.vault.resolve(ref) if ref.startswith(("env:", "secret:")) else ref
        headers = {}
        if provider.protocol == "anthropic":
            headers = {"x-api-key": token, "anthropic-version": "2023-06-01"}
        elif provider.protocol == "gemini":
            headers = {"x-goog-api-key": token}
        elif token:
            headers = {"Authorization": f"Bearer {token}"}
        if provider.protocol == "systemone":
            # TypeSafe publishes model routes instead of a listing endpoint, and OpenRouter's
            # /models omits decision models, so offer each host's own names.
            host = httpx.URL(provider.base_url).host
            keys = (
                ("~typesafe/jev-latest",)
                if host == "openrouter.ai" or host.endswith(".openrouter.ai")
                else ("jev-latest", "jev-preview")
            )
            return [{"id": key, "name": key, "max_output": 4096} for key in keys]
        base = provider.base_url.rstrip("/")
        params, found, cursors = {}, {}, set()
        try:
            for _ in range(100):
                response = await self._request(
                    provider, "GET", base + "/models", headers=headers, params=params
                )
                body = response.json()
                if not isinstance(body, dict):
                    raise PublicError("提供商未返回有效的模型列表")
                rows = body.get("models" if provider.protocol == "gemini" else "data")
                if not isinstance(rows, list):
                    raise PublicError("提供商未返回有效的模型列表，请检查接口协议和服务地址")
                for row in rows:
                    if not isinstance(row, dict):
                        raise PublicError("提供商返回了无效的模型条目")
                    if provider.protocol == "gemini":
                        methods = row.get("supportedGenerationMethods", [])
                        if methods and "generateContent" not in methods:
                            continue
                        key = row["name"].removeprefix("models/")
                    else:
                        key = row["id"]
                    if not isinstance(key, str) or not key:
                        raise PublicError("提供商返回了无效的模型名称")
                    found[key] = {
                        "id": key,
                        "name": row.get("displayName", row.get("display_name", key)),
                        "max_output": row.get("outputTokenLimit", 4096),
                    }
                cursor = (
                    body.get("nextPageToken")
                    if provider.protocol == "gemini"
                    else (body.get("last_id") if body.get("has_more") else None)
                )
                if not cursor:
                    return sorted(found.values(), key=lambda row: row["id"])
                if cursor in cursors:
                    raise PublicError("提供商的模型分页游标重复，请稍后重试")
                cursors.add(cursor)
                params = {"pageToken" if provider.protocol == "gemini" else "after_id": cursor}
            raise PublicError("模型列表分页超过限制，请缩小提供商返回范围")
        except httpx.HTTPStatusError as exc:
            raise PublicError(
                f"获取模型失败（HTTP {exc.response.status_code}），请检查 API Key、地址与权限"
            ) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise PublicError("无法读取提供商模型列表，请检查连接和接口协议") from None

    async def complete(
        self, route: str, system: str, messages: list[dict], tools: list[dict], settings=None
    ) -> Reply:
        cfg = settings or self.config.value
        candidates = cfg.routes.get(route, [])
        if not candidates:
            raise PublicError(f"尚未配置 {route} 模型路由，请在 WebUI 配置 Provider、模型和路由")
        async with self.semaphore:
            context_skipped = decision_only = False
            for model_id in candidates:
                model = next((m for m in cfg.models if m.id == model_id and m.enabled), None)
                provider = next(
                    (p for p in cfg.providers if model and p.id == model.provider and p.enabled),
                    None,
                )
                if not model or not provider:
                    continue
                if provider.protocol == "systemone":
                    decision_only = True
                    continue
                path, body = request_payload(
                    provider.protocol, model.model, system, messages, tools, model.max_output
                )
                body = merge_body(body, model.extra_body)
                if provider.protocol == "chat_completions" and "max_tokens" in model.extra_body:
                    body.pop("max_completion_tokens", None)
                generation = body.get("generationConfig")
                output_limit = {
                    "chat_completions": body.get("max_tokens", body.get("max_completion_tokens")),
                    "responses": body.get("max_output_tokens"),
                    "anthropic": body.get("max_tokens"),
                    "gemini": generation.get("maxOutputTokens")
                    if isinstance(generation, dict)
                    else None,
                }[provider.protocol]
                reserved_output = (
                    output_limit
                    if isinstance(output_limit, int) and output_limit > 0
                    else model.max_output
                )
                if (
                    model.max_context
                    and estimated_tokens(body) + reserved_output > model.max_context
                ):
                    context_skipped = True
                    log.warning(f"{model.id} 的估算上下文超过上限 {model.max_context}，跳过", route)
                    await self.store.activity("provider.context_limit", model.id)
                    continue
                ref = provider.api_key
                token = (
                    await self.vault.resolve(ref) if ref.startswith(("env:", "secret:")) else ref
                )
                headers = {"Content-Type": "application/json"}
                if provider.protocol == "anthropic":
                    headers.update({"x-api-key": token, "anthropic-version": "2023-06-01"})
                elif provider.protocol == "gemini":
                    headers["x-goog-api-key"] = token
                elif token:
                    headers["Authorization"] = f"Bearer {token}"
                started = time.monotonic()
                try:
                    response = await self._request(
                        provider,
                        "POST",
                        provider.base_url.rstrip("/") + path,
                        json=body,
                        headers=headers,
                    )
                    reply = parse_reply(provider.protocol, model.model, response.json())
                    await self.store.put(
                        "usage",
                        uid(),
                        {"model": model.id, "protocol": provider.protocol, "usage": reply.usage},
                    )
                    log.debug(
                        f"{model.id} 应答，用时 {time.monotonic() - started:.1f}s"
                        + (f"，请求调用 {len(reply.calls)} 个工具" if reply.calls else ""),
                        route,
                    )
                    return reply
                except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                    log.warning(
                        f"{model.id} 调用失败（{failure(provider.id, exc)}），尝试下一个候选模型",
                        route,
                    )
                    await self.store.activity("provider.failed", failure(provider.id, exc))
        if context_skipped:
            raise PublicError(
                "候选模型调用失败，部分模型的估算上下文超过上限，请减少输入或调整模型上下文配置"
            )
        if decision_only:
            raise PublicError(
                f"{route} 路由的候选模型调用失败；Jev 等决策模型不生成文本，请再添加一个文本模型"
            )
        raise PublicError("所有候选模型调用失败，请检查 Provider 地址、凭据与模型名称")

    async def decide(
        self, route: str, state: str | dict, questions: dict, settings=None
    ) -> dict | None:
        """Ask the route's System One decision models; None lets callers fall back to text."""
        cfg = settings or self.config.value
        for model_id in cfg.routes.get(route, []):
            model = next((m for m in cfg.models if m.id == model_id and m.enabled), None)
            provider = next(
                (
                    p
                    for p in cfg.providers
                    if model and p.id == model.provider and p.enabled and p.protocol == "systemone"
                ),
                None,
            )
            if not model or not provider:
                continue
            ref = provider.api_key
            token = await self.vault.resolve(ref) if ref.startswith(("env:", "secret:")) else ref
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                async with self.semaphore:
                    response = await self._request(
                        provider,
                        "POST",
                        provider.base_url.rstrip("/") + "/systemone",
                        json={"model": model.model, "state": state, "questions": questions},
                        headers=headers,
                    )
                data = response.json()
                answers = data["answers"]
                if not isinstance(answers, dict) or answers.keys() != questions.keys():
                    raise ValueError("incomplete answers")
                await self.store.put(
                    "usage",
                    uid(),
                    {"model": model.id, "protocol": "systemone", "usage": data.get("usage", {})},
                )
                return answers
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                log.warning(f"决策模型 {model.id} 调用失败（{failure(provider.id, exc)}）", route)
                await self.store.activity("provider.failed", failure(provider.id, exc))
        return None
