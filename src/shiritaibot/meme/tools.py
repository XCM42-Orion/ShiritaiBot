"""Model tools for the meme library: search, view and save (docs/MEMES.md §4)."""

from __future__ import annotations

import asyncio

from shiritaibot.core import PublicError, Tool
from shiritaibot.llm.media import view_marker
from shiritaibot.meme.core import MEME_ID, download
from shiritaibot.tools import schema


def meme_tools(app) -> list[Tool]:
    core = app.memes.core

    def available(ctx) -> bool:
        return app.config.value.meme.enabled

    async def meme_search(ctx, args):
        results = await core.search(args["query"], args.get("category", ""), args.get("limit", 5))
        return {
            "memes": results,
            "note": "用 <meme>ID</meme> 发送其中一个表情；description 是转述模型写的文字描述。",
        }

    async def meme_view(ctx, args):
        row = core.get(args["id"])
        if row is None:
            raise PublicError("表情不存在，请先用 meme_search 查询 ID")
        # Rendering attaches the image for image-capable models and errors for others.
        return {
            "id": row["id"],
            "category": row["category"],
            "image": view_marker(row["id"]),
        }

    async def meme_save(ctx, args):
        record = await app.store.get("qq_file", args["ref"])
        if not record or record["scope"] != ctx.session:
            raise PublicError("图片引用不属于当前会话")
        cached = app.store.home / "media" / args["ref"]
        try:
            data = await asyncio.to_thread(cached.read_bytes)
        except OSError:
            data = await download(app.llm.client, record["data"].get("url", ""))
        if data is None:
            raise PublicError("图片已过期或不是受支持的图片")
        row = await core.ingest(
            data,
            source="agent",
            origin=ctx.session,
            category=args.get("category", ""),
            description=args.get("description", ""),
            enforce_capacity=True,
        )
        return {"id": row["id"], "category": row["category"], "description": row["description"]}

    return [
        Tool(
            "meme_search",
            "按语义检索表情库，返回表情 ID、分类与文字描述；可限定分类。"
            "找到合适的表情后在回复里写 <meme>ID</meme> 发送。",
            schema(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 200},
                    "category": {"type": "string", "maxLength": 32},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ["query"],
            ),
            meme_search,
            risk="safe",
            chat=True,
            available=available,
        ),
        Tool(
            "meme_view",
            "查看表情库中某个表情的图片（id 来自 meme_search）。仅支持图像输入的模型可用，"
            "其他模型调用会返回错误，请改看 meme_search 的文字描述。",
            schema({"id": {"type": "string", "pattern": MEME_ID.pattern}}, ["id"]),
            meme_view,
            risk="safe",
            chat=True,
            available=available,
        ),
        Tool(
            "meme_save",
            "把当前会话收到的一张图片（[图片 ref=...] 中的 ref）收藏进表情库。"
            "category 留空时自动判断分类，description 留空时由表情转述模型描述。",
            schema(
                {
                    "ref": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                    "category": {"type": "string", "maxLength": 32},
                    "description": {"type": "string", "maxLength": 300},
                },
                ["ref"],
            ),
            meme_save,
            risk="normal",
            chat=True,
            available=available,
            timeout=120,
        ),
    ]
