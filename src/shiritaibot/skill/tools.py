from __future__ import annotations

import asyncio
from pathlib import Path

from shiritaibot.core import PublicError, Tool
from shiritaibot.tools import TEXT, schema
from shiritaibot.tools.qq import is_sensitive, session_dir

NAME = {"type": "string", "pattern": r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"}
PATH = {"type": "string", "minLength": 1, "maxLength": 300}
CONTENT = {"type": "string", "maxLength": 200000}


def skill_tools(app) -> list[Tool]:
    skills = app.skills

    def workspace(ctx) -> Path:
        return session_dir(app.home, "workspaces", ctx.session)

    async def skill_list(ctx, args):
        rows = await skills.listing()
        return {
            "skills": [
                {
                    "name": row["name"],
                    "description": row["description"],
                    "source": row["source"],
                    "enabled": row["enabled"],
                    **({"error": row["error"]} if row["error"] else {}),
                }
                for row in rows
            ]
        }

    async def skill_read(ctx, args):
        return await skills.read(args["name"], args.get("path", "SKILL.md"), args.get("offset", 0))

    async def skill_write(ctx, args):
        files = {item["path"]: item["content"] for item in args.get("files", [])}
        files["SKILL.md"] = args["skill_md"]
        row = await skills.create(args["name"], files, replace=args.get("replace", False))
        return {"written": row["name"], "files": row["files"], "path": row["path"]}

    async def skill_update(ctx, args):
        row = await skills.update(
            args["name"],
            args.get("path", "SKILL.md"),
            content=args.get("content"),
            old_text=args.get("old_text"),
            new_text=args.get("new_text"),
            delete=args.get("delete", False),
        )
        return {"updated": row["name"], "source": row["source"], "files": row["files"]}

    async def skill_install(ctx, args):
        def locate() -> Path:
            path = Path(args["path"]).expanduser()
            return (path if path.is_absolute() else workspace(ctx) / path).resolve()

        path = await asyncio.to_thread(locate)
        if is_sensitive(app.home, path):
            raise PublicError("框架内部敏感文件禁止读取")
        row = await skills.install_path(path, replace=args.get("replace", False))
        return {
            "installed": row["name"],
            "description": row["description"],
            "files": [f["path"] for f in row["files"]],
        }

    async def skill_remove(ctx, args):
        return await skills.remove(args["name"])

    async def skill_pack(ctx, args):
        folder = workspace(ctx)
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        result = await skills.pack(args["name"], folder)
        return {**result, "note": "可用 qq_file_send 把该 .skill 文件发给对方"}

    return [
        Tool(
            "skill_list",
            "列出已安装的技能（Skill）：名称、描述、来源（bundled 内置 / installed 已安装）与是否启用。",
            schema({}),
            skill_list,
            risk="safe",
        ),
        Tool(
            "skill_read",
            "读取技能文件。默认读取 SKILL.md（技能的使用说明）；path 可指定技能目录内的其他文件，"
            "如 references/xxx.md。长文件用 next_offset 作为 offset 分段读取。"
            "返回的 directory 是技能所在目录，可供 shell 执行技能附带的脚本。",
            schema(
                {"name": NAME, "path": PATH, "offset": {"type": "integer", "minimum": 0}}, ["name"]
            ),
            skill_read,
            risk="safe",
        ),
        Tool(
            "skill_write",
            "新建技能。skill_md 是 SKILL.md 全文，必须以 YAML frontmatter 开头，"
            "包含 name（与 name 参数一致，小写字母/数字/短横线）和 description（做什么、何时使用）；"
            "files 可附带其他文本文件（references/、scripts/、assets/ 下的相对路径）。"
            "同名技能已存在时报错，确需整体覆盖才设置 replace=true。",
            schema(
                {
                    "name": NAME,
                    "skill_md": {"type": "string", "minLength": 1, "maxLength": 200000},
                    "files": {
                        "type": "array",
                        "maxItems": 49,
                        "items": schema({"path": PATH, "content": CONTENT}, ["path", "content"]),
                    },
                    "replace": {"type": "boolean"},
                },
                ["name", "skill_md"],
            ),
            skill_write,
            risk="normal",
        ),
        Tool(
            "skill_update",
            "修改已有技能中的一个文件（path 默认 SKILL.md）。三选一："
            "content 整体替换（文件不存在则新建）；old_text + new_text 精确替换唯一片段；"
            "delete=true 删除该文件（不能删除 SKILL.md）。修改内置技能会先复制为已安装副本。",
            schema(
                {
                    "name": NAME,
                    "path": PATH,
                    "content": CONTENT,
                    "old_text": {"type": "string", "minLength": 1, "maxLength": 200000},
                    "new_text": CONTENT,
                    "delete": {"type": "boolean"},
                },
                ["name"],
            ),
            skill_update,
            risk="normal",
        ),
        Tool(
            "skill_install",
            "从本地文件或目录安装技能：.skill / .zip 技能包、单个 SKILL.md，或包含 SKILL.md 的目录。"
            "对方在聊天中发送的技能文件，先用 qq_file_download 下载，再把返回的 path 传给本工具。"
            "相对路径基于当前会话工作目录。同名技能已安装时需 replace=true 才覆盖。",
            schema({"path": TEXT, "replace": {"type": "boolean"}}, ["path"]),
            skill_install,
            risk="sensitive",
        ),
        Tool(
            "skill_remove",
            "删除一个已安装的技能（不可恢复）。内置技能不能删除。",
            schema({"name": NAME}, ["name"]),
            skill_remove,
            risk="dangerous",
        ),
        Tool(
            "skill_pack",
            "把技能打包为 <name>.skill 文件，保存到当前会话工作目录并返回路径。",
            schema({"name": NAME}, ["name"]),
            skill_pack,
            risk="normal",
        ),
    ]
