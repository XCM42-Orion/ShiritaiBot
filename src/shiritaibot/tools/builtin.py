from __future__ import annotations

import asyncio
import hashlib
from urllib.parse import urlparse

import httpx

from shiritaibot.core import PublicError, Tool, now
from shiritaibot.core.process import run_process
from shiritaibot.routine import RoutineItem
from shiritaibot.tools import TEXT, schema


async def register_builtin(app) -> None:
    registry = app.registry
    registry.remove_plugin("builtin")

    async def current_time(ctx, args):
        return {"utc": now().isoformat(), "timezone": app.config.value.proactive.timezone}

    async def search(ctx, args):
        token = await app.vault.resolve(app.config.value.tavily_key)
        if not token:
            raise PublicError("未配置 Tavily API Key")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "query": args["query"],
                    "max_results": args.get("max_results", 5),
                    "search_depth": "basic",
                },
            )
            response.raise_for_status()
            return response.json()

    async def shell(ctx, args):
        workspace = app.home / "workspaces" / hashlib.sha256(ctx.session.encode()).hexdigest()[:24]
        await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
        return await run_process(
            [*app.config.value.policy.shell_command, args["command"]],
            workspace,
            timeout=args.get("timeout", 60),
        )

    async def memory_search(ctx, args):
        return await app.memory.search(ctx, args["query"])

    async def memory_write(ctx, args):
        return await app.memory.add(ctx, args["content"], args.get("kind", "knowledge"))

    async def routine_list(ctx, args):
        return await app.store.list("routine", scope="owner")

    async def routine_create(ctx, args):
        return await app.routine.create(RoutineItem.model_validate(args))

    async def subagent_start(ctx, args):
        return await app.subagents.start(ctx, args["instance"], args["prompt"])

    async def subagent_list(ctx, args):
        return await app.store.list("subagent_run", scope=ctx.session)

    async def subagent_cancel(ctx, args):
        row = await app.store.get("subagent_run", args["id"])
        if not row or row["scope"] != ctx.session:
            raise PublicError("当前会话不存在该子代理任务")
        await app.subagents.cancel(args["id"])
        return {"cancelled": True}

    async def artifact_read(ctx, args):
        row = await app.store.get("artifact", args["id"])
        if not row or row["scope"] != ctx.session:
            raise PublicError("当前会话不能访问该产物")
        offset = args.get("offset", 0)
        return {"text": row["content"][offset : offset + 12000], "total": len(row["content"])}

    async def qq_download(ctx, args):
        row = await app.store.get("qq_file", args["ref"])
        if not row or row["scope"] != ctx.session:
            raise PublicError("文件引用不属于当前会话")
        bot = app.adapters.instances.get(row["adapter"])
        if not bot:
            raise PublicError("文件所属 OneBot 实例不可用")
        data = row["data"]
        file_id = data.get("file_id", data.get("id"))
        if not file_id:
            raise PublicError("文件缺少平台 ID")
        if row["group_id"]:
            resource = await bot.call(
                "get_group_file_url",
                {
                    "group_id": int(row["group_id"]),
                    "file_id": file_id,
                    "busid": data.get("busid", 0),
                },
            )
        else:
            resource = await bot.call("get_file", {"file_id": file_id})
        url = resource.get("url", "")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or not any(
                host == d or host.endswith("." + d) for d in ("qq.com", "qpic.cn", "gtimg.cn")
            )
        ):
            raise PublicError("OneBot 没有返回受支持的 QQ HTTPS 下载地址；不读取协议端本地路径")
        folder = app.home / "files" / hashlib.sha256(ctx.session.encode()).hexdigest()[:24]
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        temp = folder / (args["ref"] + ".part")
        digest, size = hashlib.sha256(), 0
        try:
            stream = await asyncio.to_thread(temp.open, "wb")
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes(65536):
                            size += len(chunk)
                            if size > 50 * 1024 * 1024:
                                raise PublicError("文件超过 50 MiB 上限")
                            digest.update(chunk)
                            await asyncio.to_thread(stream.write, chunk)
            finally:
                await asyncio.to_thread(stream.close)
            target = folder / digest.hexdigest()
            await asyncio.to_thread(temp.replace, target)
            return {
                "path": str(target),
                "bytes": size,
                "sha256": digest.hexdigest(),
                "name": data.get("name", "file"),
            }
        finally:
            await asyncio.to_thread(temp.unlink, missing_ok=True)

    entries = [
        Tool("current_time", "查询当前时间与时区", schema({}), current_time, chat=True),
        Tool(
            "shell",
            "在会话工作目录执行 bash。需要主人批准；这不是操作系统沙箱。",
            schema(
                {"command": TEXT, "timeout": {"type": "integer", "minimum": 1, "maximum": 120}},
                ["command"],
            ),
            shell,
            risk="dangerous",
            timeout=130,
        ),
        Tool(
            "memory_search", "检索主人的长期记忆", schema({"query": TEXT}, ["query"]), memory_search
        ),
        Tool(
            "memory_write",
            "记录主人明确要求记住的事实",
            schema({"content": TEXT, "kind": TEXT}, ["content"]),
            memory_write,
            risk="normal",
        ),
        Tool("routine_list", "查看日程、待办、提醒和定时任务", schema({}), routine_list),
        Tool(
            "routine_create",
            "创建待办、日程、显式提醒或定时任务。时间必须带时区。",
            RoutineItem.model_json_schema(),
            routine_create,
            risk="normal",
        ),
        Tool(
            "subagent_start",
            "启动已配置的外部 Agent 实例，返回任务 ID；需要批准",
            schema({"instance": TEXT, "prompt": TEXT}, ["instance", "prompt"]),
            subagent_start,
            risk="dangerous",
        ),
        Tool("subagent_list", "查看当前会话的子代理任务结果", schema({}), subagent_list),
        Tool(
            "subagent_cancel",
            "取消当前会话的子代理任务",
            schema({"id": TEXT}, ["id"]),
            subagent_cancel,
            risk="normal",
        ),
        Tool(
            "artifact_read",
            "读取当前会话的大段工具输出",
            schema({"id": TEXT, "offset": {"type": "integer", "minimum": 0}}, ["id"]),
            artifact_read,
        ),
        Tool(
            "qq_file_download",
            "下载当前聊天已接收到的 QQ 文件引用",
            schema({"ref": TEXT}, ["ref"]),
            qq_download,
            risk="normal",
            timeout=180,
        ),
    ]
    if await app.vault.resolve(app.config.value.tavily_key):
        entries.append(
            Tool(
                "web_search",
                "使用 Tavily 搜索网页，返回来源链接",
                schema(
                    {
                        "query": TEXT,
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    ["query"],
                ),
                search,
                chat=True,
            )
        )
    for tool in entries:
        registry.add(tool)
