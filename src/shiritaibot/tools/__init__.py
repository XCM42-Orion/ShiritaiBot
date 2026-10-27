from __future__ import annotations

import asyncio
import json

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from shiritaibot.core import Context, PublicError, Tool, canonical, uid


class ToolRegistry:
    def __init__(self, permissions, store):
        self.permissions, self.store = permissions, store
        self.tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        Draft202012Validator.check_schema(tool.schema)
        self.tools[tool.name] = tool

    def remove_plugin(self, plugin: str) -> None:
        self.tools = {k: v for k, v in self.tools.items() if v.plugin != plugin}

    def specs(self, ctx: Context) -> list[dict]:
        return [
            tool.spec()
            for _, tool in sorted(self.tools.items())
            if self.permissions.decision(ctx, tool) != "deny"
        ]

    async def execute(self, ctx: Context, name: str, arguments: str | dict) -> str:
        try:
            tool = self.tools.get(name)
            if not tool:
                raise PublicError("工具不存在或未启用")
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            Draft202012Validator(tool.schema).validate(args)
            await self.permissions.check(ctx, tool, args)
            async with asyncio.timeout(tool.timeout):
                value = await tool.handler(ctx, args)
            result = canonical(value)
            if len(result) > 16000:
                key = uid()
                await self.store.put("artifact", key, {"content": result}, ctx.session)
                result = canonical({"preview": result[:12000], "artifact": key, "truncated": True})
            await self.store.activity("tool.completed", name, ctx.session)
            return result
        except (ValidationError, json.JSONDecodeError):
            return canonical({"error": "工具参数不符合 JSON Schema"})
        except PublicError as exc:
            return canonical({"error": str(exc)})
        except TimeoutError:
            return canonical({"error": "工具超时，执行已中止"})
        except Exception as exc:
            await self.store.activity("tool.failed", f"{name}: {type(exc).__name__}", ctx.session)
            return canonical({"error": f"工具失败 ({type(exc).__name__})，请检查配置或连接"})


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TEXT = {"type": "string", "minLength": 1, "maxLength": 16000}
