from __future__ import annotations

import asyncio
import json
from pathlib import Path

from shiritaibot.core import Context, ExternalEvent, PublicError, uid
from shiritaibot.core.process import run_process


def command_for(config, workspace: Path) -> list[str]:
    args = list(config.command)
    if config.kind == "codex":
        args += [
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only" if config.permission == "read_only" else "workspace-write",
            "-C",
            str(workspace),
        ]
        if config.model:
            args += ["--model", config.model]
        args += ["-"]
    elif config.kind == "claude_code":
        args += [
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read,Glob,Grep" if config.permission == "read_only" else "Read,Glob,Grep,Edit,Write",
        ]
        if config.model:
            args += ["--model", config.model]
    return args


class SubagentManager:
    def __init__(self, config, store, home: Path, emit):
        self.config, self.store, self.home, self.emit = config, store, home, emit
        self.tasks: dict[str, asyncio.Task] = {}
        self.semaphore = asyncio.Semaphore(3)

    def instances(self) -> list[dict]:
        """Describe enabled instances from live config without exposing CLI arguments."""
        return [
            {
                "id": instance.id,
                "kind": instance.kind,
                "model": instance.model,
                "permission": instance.permission,
            }
            for instance in self.config.value.subagents
            if instance.enabled
        ]

    async def start(self, ctx: Context, instance: str, prompt: str) -> dict:
        config = next(
            (x for x in self.config.value.subagents if x.id == instance and x.enabled), None
        )
        if not config:
            raise PublicError("子代理实例不存在或未启用")
        if len(self.tasks) >= 20:
            raise PublicError("子代理队列已满")
        key = uid()
        workspace = self.home / "workspaces" / key
        await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
        row = await self.store.put(
            "subagent_run",
            key,
            {"instance": instance, "status": "queued", "workspace": str(workspace)},
            ctx.session,
        )
        task = asyncio.create_task(
            self.run(key, ctx, config.model_copy(deep=True), workspace, prompt)
        )
        self.tasks[key] = task
        task.add_done_callback(lambda _: self.tasks.pop(key, None))
        return row

    async def run(self, key: str, ctx: Context, config, workspace: Path, prompt: str) -> None:
        row = await self.store.get("subagent_run", key)
        try:
            async with self.semaphore:
                row["status"] = "running"
                await self.store.put("subagent_run", key, row, ctx.session)
                result = await run_process(
                    command_for(config, workspace), workspace, prompt, config.timeout, limit=256000
                )
                summary, native = result["stdout"], None
                if config.kind == "claude_code":
                    try:
                        obj = json.loads(summary)
                        summary, native = obj.get("result", summary), obj.get("session_id")
                        if obj.get("is_error"):
                            result["exit_code"] = 1
                    except ValueError:
                        pass
                elif config.kind == "codex":
                    texts = []
                    for line in summary.splitlines():
                        try:
                            obj = json.loads(line)
                            native = obj.get("thread_id", native)
                            if obj.get("item", {}).get("type") == "agent_message":
                                texts.append(obj["item"]["text"])
                        except ValueError:
                            pass
                    summary = "\n".join(texts) or summary
                row.update(
                    status="completed" if result["exit_code"] == 0 else "failed",
                    result=summary[:16000],
                    native_session_id=native,
                    exit_code=result["exit_code"],
                    truncated=result["truncated"],
                )
                # Raw stderr is deliberately not written into the activity feed.
        except asyncio.CancelledError:
            row["status"] = "cancelled"
            raise
        except Exception as exc:
            row.update(status="failed", error=type(exc).__name__)
        finally:
            await self.store.put("subagent_run", key, row, ctx.session)
            await self.emit(
                ExternalEvent(
                    source="subagent",
                    kind="subagent.completed",
                    dedup_key=key,
                    title=f"子代理 {config.id}: {row['status']}",
                    payload={"run_id": key, "result": row.get("result", "")[:4000]},
                )
            )

    async def cancel(self, key: str) -> None:
        if key in self.tasks:
            self.tasks[key].cancel()
            await asyncio.gather(self.tasks[key], return_exceptions=True)

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
