from __future__ import annotations

import asyncio

from shiritaibot.core import PublicError, uid

COMMANDS = {
    "help": "查看命令帮助",
    "status": "查看当前会话、模型和运行状态",
    "models": "列出可用模型",
    "model": "查看当前模型；/model 模型标识 切换；/model default 恢复路由",
    "new": "开始新上下文，保留历史与长期记忆（仅主人）",
    "clear": "同 /new",
    "stop": "停止当前任务及自己的排队消息",
    "tools": "查看当前会话允许使用的工具",
    "permissions": "查看当前工具的权限与审批要求",
    "tasks": "查看后台子代理任务（仅主人私聊）",
    "memory": "查看长期记忆；/memory 关键词 搜索（仅主人私聊）",
    "approve": "/approve 审批编号，同意一次操作（仅主人私聊）",
    "deny": "/deny 审批编号，拒绝操作（仅主人私聊）",
}


async def command(runtime, ctx, text):
    parts = text.strip()[1:].split(maxsplit=1)
    name, argument = parts[0] if parts else "help", parts[1] if len(parts) > 1 else ""
    name, argument = name.lower(), argument.strip()
    name = {"cancel": "stop", "reset": "clear"}.get(name, name)
    cfg = runtime.config.value
    private = ctx.owner and not ctx.group and ctx.mode == "agent" and not ctx.background
    actor = runtime.actors.get(ctx.session)
    try:
        if name in {"tasks", "memory", "approve", "deny"} and not private:
            raise PublicError("此命令仅限主人私聊或 WebUI 使用。")
        capability = {"memory": "memory_search", "tasks": "subagent_list"}.get(name)
        if capability:
            await runtime.registry.permissions.check(ctx, runtime.registry.tools[capability], {})
        if name in {"new", "clear"} or (name == "model" and argument):
            if not ctx.owner or ctx.background:
                raise PublicError("只有主人可以重置会话或切换模型。")
        if name == "help":
            answer = "ShiritaiBot 命令（直接执行，不消耗模型 tokens）\n" + "\n".join(
                f"/{key} — {value}"
                for key, value in COMMANDS.items()
                if private or key not in {"tasks", "memory", "approve", "deny"}
            )
        elif name in {"new", "clear"}:
            if actor:
                actor.task.cancel()
                await asyncio.gather(actor.task, return_exceptions=True)
                if runtime.actors.get(ctx.session) is actor:
                    runtime.actors.pop(ctx.session, None)
            await runtime.store.reset_history(ctx.session)
            answer = "已开始新上下文。历史记录和长期记忆已保留。"
        elif name == "stop":
            if actor:
                await actor.submit(ctx, "/stop")
            answer = "已发送停止请求；仅会停止你有权控制的任务。"
        elif name == "models":
            providers = {p.id for p in cfg.providers if p.enabled}
            answer = "可用模型：\n" + (
                "\n".join(
                    f"{m.id} · {m.provider} / {m.model}"
                    for m in cfg.models
                    if m.enabled and m.provider in providers
                )
                or "尚未配置，请在 WebUI 添加提供商和模型。"
            )
        elif name in {"model", "status"}:
            if name == "model" and argument:
                providers = {p.id for p in cfg.providers if p.enabled}
                if argument != "default" and not any(
                    m.id == argument and m.enabled and m.provider in providers for m in cfg.models
                ):
                    raise PublicError("模型不存在或未启用。使用 /models 查看可用标识。")
                await runtime.store.put(
                    "session_model",
                    ctx.session,
                    {"model": "" if argument == "default" else argument},
                )
            selected = await runtime.store.get("session_model", ctx.session)
            model = selected.get("model") if selected else None
            route = cfg.routes.get("agent" if ctx.mode == "agent" else "chat", [])
            answer = f"会话：{ctx.session}\n模式：{ctx.mode}\n模型：{model or ' → '.join(route) or '未配置'}"
            if argument:
                answer += "\n模型设置从下一轮开始生效。"
            if name == "status":
                answer += f"\n状态：{'执行中' if actor and actor.current else '空闲'}\n排队：{actor.queue.qsize() if actor else 0}"
        elif name in {"tools", "permissions"}:
            permissions = runtime.registry.permissions
            answer = "当前会话的工具权限：\n" + (
                "\n".join(
                    f"{tool.name} · {'需审批' if decision == 'approve' else '允许'}"
                    for tool in sorted(runtime.registry.tools.values(), key=lambda tool: tool.name)
                    if (decision := permissions.decision(ctx, tool)) != "deny"
                )
                or "没有可用工具"
            )
            answer += "\n命令不会绕过作用域、群聊限制或操作审批。"
        elif name == "tasks":
            rows = await runtime.store.list("subagent_run", limit=20)
            answer = "最近子代理任务：\n" + (
                "\n".join(f"{row['id']} · {row.get('status', '')}" for row in rows) or "暂无任务"
            )
        elif name == "memory":
            rows = (
                await runtime.memory.search(ctx, argument)
                if argument
                else await runtime.memory.list()
            )
            answer = "长期记忆：\n" + (
                "\n".join(
                    row["content"][:500]
                    for row in rows[:10]
                    if row.get("status", "active") == "active"
                )
                or "没有匹配的记忆"
            )
        elif name in {"approve", "deny"}:
            if not argument:
                raise PublicError(f"用法：/{name} 审批编号")
            await runtime.registry.permissions.resolve(argument, name == "approve")
            answer = "审批已同意。" if name == "approve" else "审批已拒绝。"
        else:
            answer = "未知命令。输入 /help 查看支持的命令。"
    except PublicError as exc:
        answer = str(exc)
    turn = uid()
    await runtime.store.append(
        ctx.session, turn, {"role": "user", "content": text, "_command": True}
    )
    await runtime.store.append(
        ctx.session, turn, {"role": "assistant", "content": answer, "_command": True}
    )
    await runtime.deliver(ctx.session, answer)
    return "command"
