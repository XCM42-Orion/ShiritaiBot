from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable

from shiritaibot.core import Context, ExternalEvent, PublicError, uid
from shiritaibot.core.logs import brief, get_logger
from shiritaibot.core.process import run_process

log = get_logger("子代理")


# Runs are unattended: stdin is closed after the prompt, so a CLI can never block on a
# question. Instead the agent ends its turn with this marker and the run becomes
# needs_input; Proactive Agent then answers (subagent_send) or asks the owner.
NEEDS_INPUT = "NEEDS_INPUT:"
UNATTENDED = (
    "\n\n---\n[运行说明] 你在无人值守的后台运行，运行期间没有人能回答你的问题。"
    "能自行合理决定的就直接决定并说明理由；只有缺少关键信息、需要确认不可逆或高风险的操作，"
    "或因权限不足无法继续时，才先完成能做的部分，然后在最终回复的最后一行单独写："
    f"{NEEDS_INPUT} <你需要的回答或权限>，再结束。收到回复后会在同一会话中继续。"
)


def command_for(config, workspace: Path, resume: str | None = None) -> list[str]:
    args = list(config.command)
    if config.kind == "codex":
        # `exec resume` rejects --sandbox/-C/--add-dir, so permissions go through -c overrides
        # (same keys as ~/.codex/config.toml) for both fresh and resumed runs.
        args += ["exec", "resume"] if resume else ["exec"]
        args += [
            "--json",
            "--skip-git-repo-check",
            "-c",
            f"sandbox_mode={json.dumps(config.sandbox)}",
        ]
        if config.sandbox == "workspace-write":
            args += [
                "-c",
                f"sandbox_workspace_write.network_access={str(config.network_access).lower()}",
            ]
            if config.add_dirs:
                args += [
                    "-c",
                    f"sandbox_workspace_write.writable_roots={json.dumps(config.add_dirs)}",
                ]
        if config.model:
            args += ["--model", config.model]
        if resume:
            args += [resume]
        else:
            args += ["-C", str(workspace)]
        args += ["-"]
    elif config.kind == "claude_code":
        args += [
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            config.permission_mode,
        ]
        if config.tools:
            args += ["--tools", ",".join(config.tools)]
        if config.allowed_tools:
            args += ["--allowedTools", ",".join(config.allowed_tools)]
        if config.disallowed_tools:
            args += ["--disallowedTools", ",".join(config.disallowed_tools)]
        for directory in config.add_dirs:
            args += ["--add-dir", directory]
        if config.model:
            args += ["--model", config.model]
        if resume:
            args += ["--resume", resume]
    elif config.kind == "deepseek_harness":
        # `--profile headless` is a launcher flag and must precede the app arguments. Policy
        # options apply per invocation (they are not saved), so resumes repeat them; resuming
        # also requires the same cwd, which the run's workspace guarantees. No model flag
        # exists: the route comes from the dsh profile.
        args += [
            "--profile",
            "headless",
            "--json",
            "--permission-mode",
            config.permission_preset,
            "--approval-policy",
            config.approval_policy,
        ]
        for directory in config.add_dirs:
            args += ["--add-dir", directory]
        if resume:
            args += ["--session-id", resume]
        args += ["-"]
    return args


def permissions(config) -> dict:
    """The effective permission settings of an instance, as the agent should see them."""
    if config.kind == "codex":
        return {
            "sandbox": config.sandbox,
            "network_access": config.sandbox == "danger-full-access"
            or (config.sandbox == "workspace-write" and config.network_access),
            "add_dirs": config.add_dirs,
        }
    if config.kind == "claude_code":
        return {
            "permission_mode": config.permission_mode,
            "tools": config.tools or "default",
            "allowed_tools": config.allowed_tools,
            "disallowed_tools": config.disallowed_tools,
            "add_dirs": config.add_dirs,
        }
    if config.kind == "deepseek_harness":
        return {
            "permission_preset": config.permission_preset,
            "approval_policy": config.approval_policy,
            "add_dirs": config.add_dirs,
        }
    return {}


# dsh reports a rejected approval or sandbox block as a tool_result error code.
DSH_DENIAL = ("APPROVAL", "DENIED", "REJECT", "PERMISSION", "SANDBOX")


def denial(tool: str, detail) -> str:
    target = next(
        (
            detail[k]
            for k in ("command", "file_path", "path", "url", "pattern")
            if isinstance(detail, dict) and k in detail
        ),
        "",
    )
    return f"{tool}({brief(str(target), 120)})" if target else tool


def parse_output(kind: str, stdout: str) -> dict:
    """Extract result text, native session ID, error flag and permission denials."""
    out = {"result": stdout, "native": None, "error": False, "denied": []}
    if kind == "claude_code":
        try:
            obj = json.loads(stdout)
        except ValueError:
            return out
        if isinstance(obj, dict):
            out.update(
                result=obj.get("result") or stdout,
                native=obj.get("session_id"),
                error=bool(obj.get("is_error")),
            )
            for denied in obj.get("permission_denials") or []:
                if isinstance(denied, dict):
                    tool = denied.get("tool_name", "?")
                    out["denied"].append(denial(tool, denied.get("tool_input") or {}))
    elif kind == "codex":
        texts = []
        for line in stdout.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            out["native"] = obj.get("thread_id", out["native"])
            item = obj.get("item") or {}
            if item.get("type") == "agent_message":
                texts.append(item.get("text", ""))
            if obj.get("type") in {"turn.failed", "error"}:
                out["error"] = True
        out["result"] = "\n".join(texts) or stdout
    elif kind == "deepseek_harness":
        # NDJSON from `dsh headless --json`: session ... final (or error without final).
        texts, calls, final = [], {}, None
        for line in stdout.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            event = obj.get("type")
            if event == "session":
                out["native"] = obj.get("sessionId") or out["native"]
            elif event == "text":
                texts.append(obj.get("text", ""))
            elif event == "tool_call":
                calls[obj.get("callId")] = (obj.get("tool", "?"), obj.get("input"))
            elif event == "tool_result" and isinstance(obj.get("error"), dict):
                code = str(obj["error"].get("code", "")).upper()
                if any(word in code for word in DSH_DENIAL):
                    out["denied"].append(denial(*calls.get(obj.get("callId"), ("?", None))))
            elif event == "error":
                out["error"] = True
                texts.append(obj.get("message", ""))
            elif event == "final":
                final = obj
                out["native"] = obj.get("sessionId") or out["native"]
        if final is not None:
            reason = final.get("reason") or {}
            out["error"] = out["error"] or reason.get("kind") != "completed"
        else:
            out["error"] = True
        out["result"] = (final or {}).get("text") or "\n".join(t for t in texts if t) or stdout
    return out


def question_in(text: str) -> str | None:
    """The NEEDS_INPUT question at the end of a reply, if the agent asked one."""
    for line in reversed(text.strip().splitlines()[-5:]):
        line = line.strip().strip("*`").strip()
        if line.upper().startswith(NEEDS_INPUT):
            return line[len(NEEDS_INPUT) :].strip() or "子代理请求补充输入"
    return None


class SubagentManager:
    def __init__(self, config, store, home: Path, emit):
        self.config, self.store, self.home, self.emit = config, store, home, emit
        self.tasks: dict[str, asyncio.Task] = {}
        self.semaphore = asyncio.Semaphore(3)
        # Environment for child CLIs (plugin-provided git identity); None inherits the host.
        self.env: Callable[[], dict[str, str] | None] = lambda: None

    def instances(self) -> list[dict]:
        """Describe enabled instances from live config without exposing CLI arguments."""
        return [
            {
                "id": instance.id,
                "kind": instance.kind,
                "model": instance.model,
                "permissions": permissions(instance),
            }
            for instance in self.config.value.subagents
            if instance.enabled
        ]

    def instance(self, name: str):
        config = next((x for x in self.config.value.subagents if x.id == name and x.enabled), None)
        if not config:
            raise PublicError("子代理实例不存在或未启用")
        return config.model_copy(deep=True)

    def launch(self, key: str, ctx: Context, config, workspace: Path, prompt: str, resume=None):
        task = asyncio.create_task(self.run(key, ctx, config, workspace, prompt, resume))
        self.tasks[key] = task
        task.add_done_callback(lambda _: self.tasks.pop(key, None))

    async def start(self, ctx: Context, instance: str, prompt: str) -> dict:
        config = self.instance(instance)
        if len(self.tasks) >= 20:
            raise PublicError("子代理队列已满")
        key = uid()
        workspace = self.home / "workspaces" / key
        await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
        row = await self.store.put(
            "subagent_run",
            key,
            {
                "instance": instance,
                "status": "queued",
                "workspace": str(workspace),
                "task": prompt[:16000],
                "round": 0,
            },
            ctx.session,
        )
        self.launch(key, ctx, config, workspace, prompt)
        log.log(f"已排队 {instance}（{config.kind}）：{brief(prompt, 80)}", key[:8])
        return row

    async def send(self, ctx: Context, key: str, message: str) -> dict:
        """Continue a finished or waiting run in its native CLI session."""
        row = await self.store.get("subagent_run", key)
        # Owner contexts include the Proactive loop that received the needs_input event.
        if not row or (row.get("scope") != ctx.session and not ctx.owner):
            raise PublicError("当前会话不存在该子代理任务")
        if key in self.tasks or row["status"] in {"queued", "running"}:
            raise PublicError("子代理任务仍在运行，请等它停下后再发送")
        if not row.get("native_session_id"):
            raise PublicError("该子代理任务没有可续接的原生会话（generic 类型或启动即失败）")
        if len(self.tasks) >= 20:
            raise PublicError("子代理队列已满")
        config = self.instance(row["instance"])
        if config.kind == "generic":
            raise PublicError("generic 类型的子代理不支持续接")
        row.update(status="queued", round=row.get("round", 0) + 1, reply=message[:4000])
        row.pop("question", None)
        row.pop("error", None)
        row = await self.store.put("subagent_run", key, row, row["scope"])
        origin = Context(row["scope"], ctx.actor, ctx.mode, ctx.owner, ctx.group)
        self.launch(key, origin, config, Path(row["workspace"]), message, row["native_session_id"])
        log.log(f"续接 {config.id}（第 {row['round']} 轮）：{brief(message, 80)}", key[:8])
        return row

    async def run(
        self, key: str, ctx: Context, config, workspace: Path, prompt: str, resume=None
    ) -> None:
        row = await self.store.get("subagent_run", key)
        try:
            async with self.semaphore:
                row["status"] = "running"
                await self.store.put("subagent_run", key, row, ctx.session)
                log.log(f"{config.id} 开始运行", key[:8])
                stdin = prompt if config.kind == "generic" else prompt + UNATTENDED
                result = await run_process(
                    command_for(config, workspace, resume),
                    workspace,
                    stdin,
                    config.timeout,
                    env=self.env(),
                    limit=256000,
                )
                parsed = parse_output(config.kind, result["stdout"])
                failed = result["exit_code"] != 0 or parsed["error"]
                summary = parsed["result"]
                question = None if config.kind == "generic" else question_in(summary)
                if parsed["denied"]:
                    denied = "、".join(dict.fromkeys(parsed["denied"]))
                    summary += f"\n\n[被拒绝的权限请求] {denied}"
                    # A denial the agent could not work around means it is stuck, not done.
                    question = question or f"运行中以下操作因权限不足被拒绝：{denied}"
                row.update(
                    status="needs_input"
                    if question and parsed["native"] and not (failed and not parsed["denied"])
                    else "failed"
                    if failed
                    else "completed",
                    result=summary[:16000],
                    native_session_id=parsed["native"] or row.get("native_session_id"),
                    exit_code=1
                    if parsed["error"] and not result["exit_code"]
                    else result["exit_code"],
                    truncated=result["truncated"],
                )
                if row["status"] == "needs_input":
                    row["question"] = question[:2000]
                # Raw stderr is deliberately not written into the activity feed.
        except asyncio.CancelledError:
            row["status"] = "cancelled"
            log.log(f"{config.id} 已取消", key[:8])
            raise
        except TimeoutError:
            row.update(
                status="failed",
                error="TimeoutError",
                result=f"运行超过 {config.timeout} 秒，已终止",
            )
            log.warning(f"{config.id} 运行超时", key[:8])
        except Exception as exc:
            row.update(status="failed", error=type(exc).__name__)
            log.error(f"{config.id} 运行失败（{type(exc).__name__}）", key[:8])
        finally:
            status = row["status"]
            if status in {"completed", "failed", "needs_input"} and "error" not in row:
                label = {"completed": "完成", "failed": "失败", "needs_input": "等待输入"}[status]
                (log.log if status == "completed" else log.warning)(
                    f"{config.id} {label}"
                    f"（退出码 {row.get('exit_code')}）：{brief(row.get('result', ''), 100)}",
                    key[:8],
                )
            await self.store.put("subagent_run", key, row, ctx.session)
            # Owner-requested work: PE delivers it as a high-priority turn instead of Main polling.
            waiting = status == "needs_input"
            await self.emit(
                ExternalEvent(
                    source="subagent",
                    kind="subagent.needs_input" if waiting else "subagent.completed",
                    # One event per round: a resumed run reports again under a new key.
                    dedup_key=f"{key}:{row.get('round', 0)}",
                    title=f"子代理 {config.id}: {'等待输入' if waiting else status}",
                    urgency="normal" if status == "cancelled" else "high",
                    payload={
                        "run_id": key,
                        "instance": config.id,
                        "status": status,
                        "session": ctx.session,
                        "task": row.get("task", prompt)[:2000],
                        **({"reply": prompt[:2000]} if resume else {}),
                        "result": row.get("result", "")[-4000:],
                        **({"question": row["question"]} if waiting else {}),
                        **({"error": row["error"]} if row.get("error") else {}),
                    },
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
