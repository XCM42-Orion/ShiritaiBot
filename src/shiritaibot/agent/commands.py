from __future__ import annotations

import asyncio

from shiritaibot.config import SessionPolicy
from shiritaibot.core import PublicError, uid

COMMANDS = {
    "help": "查看命令帮助",
    "status": "查看当前会话、模型和运行状态",
    "models": "列出可用模型",
    "model": "查看当前模型；/model 模型标识 切换；/model default 恢复路由",
    "personas": "列出可用人格的标识和名称",
    "persona": "查看当前人格；/persona 人格标识 切换；/persona default 恢复默认（切换仅主人）",
    "new": "开始新上下文，保留历史与长期记忆（仅主人）",
    "clear": "同 /new",
    "stop": "停止当前任务及自己的排队消息",
    "tools": "列出所有工具权限；/tools 工具名 deny|manual|auto|allow|inherit；/tools preset 预设",
    "permissions": "查看当前工具的权限与审批要求",
    "mode": "查看会话模式；/mode agent|chat 切换（仅主人）",
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
    private = ctx.owner and not ctx.group and not ctx.background
    actor = runtime.actors.get(ctx.session)
    try:
        if name in {"tasks", "memory", "approve", "deny"} and not private:
            raise PublicError("此命令仅限主人私聊或 WebUI 使用。")
        capability = {"memory": "memory_search", "tasks": "subagent_list"}.get(name)
        if capability:
            await runtime.registry.permissions.check(ctx, runtime.registry.tools[capability], {})
        if name in {"new", "clear"} or (name in {"model", "persona"} and argument):
            if not ctx.owner or ctx.background:
                raise PublicError("只有主人可以重置会话、切换模型或人格。")
        if name == "help":
            answer = f"{cfg.assistant_name} 命令（直接执行，不消耗模型 tokens）\n" + "\n".join(
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
        elif name == "personas":
            answer = "可用人格（标识 · 名称）：\n" + (
                "\n".join(f"{p.id} · {p.name}" for p in cfg.personas if p.enabled)
                or "尚未添加人格，使用基础人设。"
            )
        elif name == "persona":
            if argument:
                if argument != "default" and not any(
                    p.id == argument and p.enabled for p in cfg.personas
                ):
                    raise PublicError("人格不存在或未启用。使用 /personas 查看可用标识。")
                await runtime.store.put(
                    "session_persona",
                    ctx.session,
                    {"persona": "" if argument == "default" else argument},
                )
            selected = await runtime.store.get("session_persona", ctx.session)
            persona = (
                cfg.selected_persona(selected.get("persona", "") if selected else "")
                or cfg.selected_persona()
            )
            answer = f"当前人格：{persona.id + ' · ' + persona.name if persona else '基础人设'}"
            if argument:
                answer += "\n人格设置从下一轮开始生效。"
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
            if model:
                models = model
            elif ctx.mode != "agent":
                models = " → ".join(cfg.routes.get("chat", [])) or "未配置"
            else:
                models = " → ".join(cfg.routes.get("agent", [])) or "未配置"
                if cfg.agent_split and cfg.routes.get("agent_chat"):
                    models = (
                        f"Work {models}；Chat "
                        + " → ".join(cfg.routes["agent_chat"])
                        + "（按消息自动分流）"
                    )
            answer = f"会话：{ctx.session}\n模式：{ctx.mode}\n模型：{models}"
            if argument:
                answer += "\n模型设置从下一轮开始生效。"
            if name == "status":
                selected = await runtime.store.get("session_persona", ctx.session)
                persona = (
                    cfg.selected_persona(selected.get("persona", "") if selected else "")
                    or cfg.selected_persona()
                )
                answer += f"\n人格：{persona.name if persona else '基础人设'}"
                answer += f"\n状态：{'执行中' if actor and actor.current else '空闲'}\n排队：{actor.queue.qsize() if actor else 0}"
        elif name in {"tools", "permissions", "mode"}:
            permissions = runtime.registry.permissions
            profile = permissions.session_policy(ctx).model_copy(deep=True)
            if argument:
                if not ctx.owner or ctx.background:
                    raise PublicError("只有主人可以修改 Main 会话的模式和工具权限。")
                args = argument.split()
                if name == "mode":
                    args = ["mode", *args]
                data = profile.model_dump()
                if args == ["reset"]:
                    async with permissions.policy_lock:
                        settings = runtime.config.value.model_copy(deep=True)
                        settings.policy.sessions.pop(ctx.session, None)
                        await runtime.config.save(settings)
                    profile = permissions.session_policy(ctx)
                else:
                    if len(args) != 2:
                        raise PublicError(
                            "用法：/tools 工具名 deny|manual|auto|allow|inherit；/tools mode agent|chat；/tools preset all_manual|safe_manual|safe_auto|full_access；/tools reset"
                        )
                    target, value = args
                    if target == "mode" and value in {"agent", "chat"}:
                        data["mode"] = value
                    elif target == "preset" and value in {
                        "all_manual",
                        "safe_manual",
                        "safe_auto",
                        "full_access",
                    }:
                        data.update(preset=value, tools={})
                    elif target in runtime.registry.tools and value in {
                        "deny",
                        "manual",
                        "auto",
                        "allow",
                        "inherit",
                    }:
                        if value == "inherit":
                            data["tools"].pop(target, None)
                        else:
                            data["tools"][target] = value
                    else:
                        raise PublicError("工具、模式、预设或权限值无效。使用 /tools 查看工具。")
                    profile = SessionPolicy.model_validate(data)
                    await permissions.set_session(ctx, profile)
            labels = {
                "deny": "禁止使用",
                "manual": "需要人工审批",
                "auto": "模型自动审批",
                "allow": "无需审批",
            }
            answer = f"当前会话：{ctx.session}\n模式：{profile.mode}\n预设：{profile.preset}"
            if name != "mode":
                answer += "\n" + "\n".join(
                    f"{tool.name} · {labels[permissions.configured(ctx, tool)]}"
                    + ("（当前不可用）" if permissions.decision(ctx, tool) == "deny" else "")
                    for tool in sorted(runtime.registry.tools.values(), key=lambda tool: tool.name)
                )
            if profile.mode == "chat":
                answer += "\nChat 模式仅开放基础对话工具，扩展功能需切换为 Agent。"
            if argument:
                answer += "\n已保存。模式从下一轮生效，工具权限立即应用。"
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
