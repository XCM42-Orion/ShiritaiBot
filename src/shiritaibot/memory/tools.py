"""Agent tools over long-term memory (docs/MEMORY.md §5)."""

from __future__ import annotations

from shiritaibot.core import Context, PublicError, Tool
from shiritaibot.tools import TEXT, schema

SHORT = {"type": "string", "minLength": 1, "maxLength": 200}
NAMES = {"type": "array", "items": SHORT, "maxItems": 20}
DATE = {
    "type": "string",
    "maxLength": 40,
    "description": "绝对时间：YYYY、YYYY-MM、YYYY-MM-DD 或 YYYY-MM-DDTHH:MM（无时区按主人时区）",
}
POLICY = (
    "注入的记忆只是部分候选；没查到不等于没发生，不要编造。某人出现在记录里只说明他参与过。"
    "记忆库是关于过去的权威来源，没有记录就直接说没有记录。"
)


def private(ctx: Context) -> None:
    # The structural guard: only owner Agent sessions and Proactive loops reach memory.
    if not ctx.owner or ctx.mode != "agent" or ctx.group:
        raise PublicError("当前上下文无权访问私人记忆")


def memory_tools(memory) -> list[Tool]:
    async def search(ctx, args):
        private(ctx)
        return await memory.search(
            query=args.get("query", ""),
            entity=args.get("entity", ""),
            kind=args.get("kind", ""),
            since=args.get("since", ""),
            until=args.get("until", ""),
            include_history=args.get("include_history", False),
            limit=args.get("limit", 10),
        )

    async def get(ctx, args):
        private(ctx)
        return await memory.get(args["id"])

    async def save(ctx, args):
        private(ctx)
        kind = args["kind"]
        if kind == "entity":
            if not args.get("name") and not args.get("entity"):
                raise PublicError("保存实体需要 name，或用 entity 指定要更新的实体")
            return await memory.save_entity(
                ctx,
                args.get("name", ""),
                args.get("entity_kind", ""),
                args.get("aliases"),
                args.get("summary"),
                args.get("entity", ""),
                args.get("merge"),
            )
        if not args.get("content") or not args.get("evidence"):
            raise PublicError("保存事实或事件需要 content 和 evidence")
        if kind == "fact":
            return await memory.save_fact(
                ctx,
                args["content"],
                args["evidence"],
                args.get("subject", ""),
                args.get("valid_from", ""),
                args.get("replaces", ""),
            )
        return await memory.save_event(
            ctx,
            args["content"],
            args["evidence"],
            args.get("time", ""),
            args.get("time_text", ""),
            args.get("phase", "happened"),
            args.get("participants"),
            args.get("replaces", ""),
        )

    async def forget(ctx, args):
        private(ctx)
        return await memory.forget(args["id"], args["reason"])

    async def resolve(ctx, args):
        private(ctx)
        return await memory.resolve_conflict(
            ctx, args["conflict"], args["decision"], args["owner_quote"], args.get("note", "")
        )

    return [
        Tool(
            "memory_search",
            "检索主人的长期记忆（人物、事物、事实、事件）。可按关键词、实体（名字或 e 编号）、"
            "类型和时间范围过滤；include_history 时也返回已被取代或遗忘的条目。" + POLICY,
            schema(
                {
                    "query": {"type": "string", "maxLength": 500},
                    "entity": SHORT,
                    "kind": {"type": "string", "enum": ["entity", "fact", "event"]},
                    "since": DATE,
                    "until": DATE,
                    "include_history": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                }
            ),
            search,
            risk="safe",
        ),
        Tool(
            "memory_get",
            "查看一条记忆的详情：原文证据与来源、取代关系、相关冲突；实体则返回别名、事实和相关事件。",
            schema({"id": {"type": "string", "pattern": "^[efvc][0-9]+$"}}, ["id"]),
            get,
            risk="safe",
        ),
        Tool(
            "memory_save",
            "写入或更正长期记忆。kind=entity 建立/更新人物或事物（名字、别名、简介，merge 合并重复实体）；"
            "kind=fact 记录关于某个实体的事实（会过时的状态也可以，例如年级、职位）；"
            "kind=event 记录发生过或计划中的事，time 必须是绝对时间，按对话中的日期换算“昨天”等说法，"
            "实在不确定时用 time_text 保留原话。content 用第三人称、不含相对时间词。"
            "evidence 必须原样摘自本轮主人消息或工具结果，摘自主人消息时视为主人确认；"
            "只记录主人或当事人明确说过的事，不要写入自己的推断。replaces 用于更正已有条目。",
            schema(
                {
                    "kind": {"type": "string", "enum": ["entity", "fact", "event"]},
                    "name": SHORT,
                    "entity": SHORT,
                    "entity_kind": {"type": "string", "enum": ["person", "thing"]},
                    "aliases": NAMES,
                    "summary": {"type": "string", "maxLength": 500},
                    "merge": NAMES,
                    "subject": SHORT,
                    "content": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "valid_from": DATE,
                    "time": DATE,
                    "time_text": {"type": "string", "maxLength": 200},
                    "phase": {"type": "string", "enum": ["happened", "planned", "cancelled"]},
                    "participants": NAMES,
                    "evidence": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "replaces": {"type": "string", "pattern": "^[fv][0-9]+$"},
                },
                ["kind"],
            ),
            save,
            risk="normal",
        ),
        Tool(
            "memory_forget",
            "遗忘一条记忆（归档，不再参与检索，也不会被自动重新学回来）。仅在主人要求或确认记错时使用。",
            schema(
                {"id": {"type": "string", "pattern": "^[efv][0-9]+$"}, "reason": TEXT},
                ["id", "reason"],
            ),
            forget,
            risk="normal",
        ),
        Tool(
            "memory_resolve",
            "按主人的答复处理记忆中的待确认冲突（编号 c 开头）。decision：keep:<编号> 保留其中一条，"
            "both_valid 都成立，neither 都不成立，same_entity 确认是同一人/物并合并，"
            "different_entity 确认不是同一个。owner_quote 必须原样摘自主人本轮的答复。",
            schema(
                {
                    "conflict": {"type": "string", "pattern": "^c[0-9]+$"},
                    "decision": {"type": "string", "maxLength": 40},
                    "owner_quote": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "note": {"type": "string", "maxLength": 500},
                },
                ["conflict", "decision", "owner_quote"],
            ),
            resolve,
            risk="normal",
        ),
    ]
