from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from shiritaibot.core import PublicError, Reply, canonical, uid


def plain(message: dict) -> dict:
    return {k: v for k, v in message.items() if not k.startswith("_")}


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


class ProviderManager:
    def __init__(self, config, vault, store):
        self.config, self.vault, self.store = config, vault, store
        self.client = httpx.AsyncClient(follow_redirects=False, timeout=120)
        self.semaphore = asyncio.Semaphore(config.value.concurrency)

    async def close(self) -> None:
        await self.client.aclose()

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
        base = provider.base_url.rstrip("/")
        params, found, cursors = {}, {}, set()
        try:
            for _ in range(100):
                response = await self.client.get(
                    base + "/models", headers=headers, params=params, timeout=provider.timeout
                )
                response.raise_for_status()
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
            for model_id in candidates:
                model = next((m for m in cfg.models if m.id == model_id and m.enabled), None)
                provider = next(
                    (p for p in cfg.providers if model and p.id == model.provider and p.enabled),
                    None,
                )
                if not model or not provider:
                    continue
                path, body = request_payload(
                    provider.protocol, model.model, system, messages, tools, model.max_output
                )
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
                for attempt in range(3):
                    try:
                        response = await self.client.post(
                            provider.base_url.rstrip("/") + path,
                            json=body,
                            headers=headers,
                            timeout=provider.timeout,
                        )
                        if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                            try:
                                delay = min(
                                    20,
                                    max(0, float(response.headers.get("retry-after", 2**attempt))),
                                )
                            except ValueError:
                                delay = 2**attempt
                            await asyncio.sleep(delay)
                            continue
                        response.raise_for_status()
                        reply = parse_reply(provider.protocol, model.model, response.json())
                        await self.store.put(
                            "usage",
                            uid(),
                            {
                                "model": model.id,
                                "protocol": provider.protocol,
                                "usage": reply.usage,
                            },
                        )
                        return reply
                    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                        await self.store.activity(
                            "provider.failed", f"{provider.id}: {type(exc).__name__}"
                        )
                        break
        raise PublicError("所有候选模型调用失败，请检查 Provider 地址、凭据与模型名称")
