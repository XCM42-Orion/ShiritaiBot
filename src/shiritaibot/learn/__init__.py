import asyncio
import hashlib
import json
import os

from shiritaibot.plugin_api.base import ExternalEvent, Plugin, PublicError, run_process, schema

from .protocol import LearnRequest, LearnResponse


class OneLearnPlugin(Plugin):
    """School-independent tool registration, stdio transport, baseline sync and events.

    School plugins supply their implementation command and login settings.
    No THU URLs, SSO logic or school-specific clients belong in this module.
    """

    def __init__(self, context):
        super().__init__(context)
        self.task = None

    async def start(self):
        self.lock = asyncio.Lock()
        self.context.register_tool(
            "learn_query",
            "查询课程、公告、未提交作业、课件与作业附件；需要身份验证时，使用学校插件的登录和验证码工具。",
            schema(
                {
                    "operation": {
                        "type": "string",
                        "enum": ["courses", "notices", "homework", "files", "homework_attachments"],
                    },
                    "course_id": {"type": "string", "maxLength": 128},
                    "homework_id": {"type": "string", "maxLength": 128},
                },
                ["operation"],
            ),
            self.query,
            risk="safe",
            timeout=240,
        )
        self.context.register_tool(
            "learn_download",
            "下载课件或作业附件到该插件私有目录。",
            schema(
                {
                    "operation": {
                        "type": "string",
                        "enum": ["download", "download_homework_attachment"],
                    },
                    "course_id": {"type": "string", "maxLength": 128},
                    "file_id": {"type": "string", "maxLength": 128},
                    "homework_id": {"type": "string", "maxLength": 128},
                    "attachment_id": {"type": "string", "maxLength": 128},
                },
                ["operation"],
            ),
            self.query,
            risk="normal",
            timeout=240,
        )
        interval = max(300, int(self.context.settings.get("poll_seconds", 900)))
        self.task = (
            asyncio.create_task(self.poll(interval))
            if self.context.settings.get("poll", False)
            else None
        )

    async def invoke(self, operation, params, owner="owner"):
        async with self.lock:
            command = self.context.settings.get("command", [])
            if not command:
                raise PublicError("请配置 OneLearn 适配进程 command")
            password = await self.context.resolve_secret(self.context.settings.get("password", ""))
            request = LearnRequest.model_validate(
                {
                    "version": 1,
                    "operation": operation,
                    "params": params,
                    "owner": owner,
                    "config": {
                        "username": self.context.settings.get("username", ""),
                        "password": password,
                    },
                    "data_dir": str(self.context.data_dir),
                }
            )
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            result = await run_process(
                command,
                self.context.data_dir,
                request.model_dump_json(),
                timeout=230,
                env=env,
                limit=1000000,
            )
            if result["exit_code"] or result["truncated"]:
                raise PublicError("网络学堂适配端失败或输出超限")
            try:
                return LearnResponse.model_validate_json(result["stdout"]).model_dump(
                    exclude_defaults=True
                )
            except ValueError:
                raise PublicError("OneLearn 适配端没有返回有效 JSON") from None

    async def query(self, ctx, args):
        return await self.invoke(
            args["operation"], {k: v for k, v in args.items() if k != "operation"}
        )

    async def sync_once(self):
        result = await self.invoke("homework", {})
        if not result.get("ok"):
            self.context.logger.warning(f"网络学堂登录状态异常：{result.get('status', 'unknown')}")
            await self.context.emit(
                ExternalEvent(
                    source="learn",
                    kind="learn.auth.expired",
                    dedup_key="auth:" + result.get("status", "unknown"),
                    title="网络学堂需要检查登录状态",
                    payload={"status": result.get("status", "unknown")},
                )
            )
            return
        snapshot = self.context.data_dir / "assignments.json"
        old = None
        if await asyncio.to_thread(snapshot.exists):
            old = json.loads(await asyncio.to_thread(snapshot.read_text, encoding="utf-8"))
        current = {}
        for item in result["data"]:
            digest = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
            key = str(item.get("homework_id", digest))
            current[key] = digest
            # The first successful sync is only a baseline, never an announcement flood.
            if old is not None and old.get(key) != digest:
                await self.context.emit(
                    ExternalEvent(
                        source="learn",
                        kind="learn.assignment.updated"
                        if key in old
                        else "learn.assignment.created",
                        dedup_key=key + ":" + digest,
                        title=str(item.get("title", "网络学堂作业变化")),
                        payload=item,
                    )
                )

        def save():
            temp = snapshot.with_suffix(".tmp")
            temp.write_text(json.dumps(current), encoding="utf-8")
            temp.replace(snapshot)

        await asyncio.to_thread(save)
        changed = sum(
            1 for key, digest in current.items() if old is not None and old.get(key) != digest
        )
        self.context.logger.log(
            f"同步完成：{len(current)} 项作业" + (f"，{changed} 项有变化" if changed else "")
            if old is not None
            else f"首次同步，记录 {len(current)} 项作业作为基线"
        )

    async def poll(self, interval):
        while True:
            try:
                await self.sync_once()
            except Exception as exc:
                self.context.logger.warning(f"同步失败（{type(exc).__name__}）")
                await self.context.emit(
                    ExternalEvent(
                        source="learn",
                        kind="learn.sync.failed",
                        dedup_key="sync:" + type(exc).__name__,
                        title="网络学堂同步失败",
                        payload={"error": type(exc).__name__},
                    )
                )
            await asyncio.sleep(interval)

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
