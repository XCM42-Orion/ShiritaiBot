from __future__ import annotations

import asyncio
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from shiritaibot.agent.commands import COMMANDS
from shiritaibot.app import Application
from shiritaibot.config import ModelConfig, ProviderConfig, Settings
from shiritaibot.core import Context, ExternalEvent, Model, PublicError, canonical, logs
from shiritaibot.core.process import run_process
from shiritaibot.plugin.core.packages import MAX_ARCHIVE, download_github, validate_id
from shiritaibot.routine import RoutineItem
from shiritaibot.store.statistics import statistics
from shiritaibot.tools.builtin import register_builtin


class SecretInput(Model):
    value: str = Field(min_length=1, max_length=10000)


class LogLevelInput(Model):
    level: str = Field(pattern=r"^(debug|info|warning|error)$")


class MessageInput(Model):
    text: str = Field(min_length=1, max_length=32000)
    session: str = Field(default="web:owner", pattern=r"^web:[a-zA-Z0-9_-]{1,80}$")


class ApprovalInput(Model):
    allow: bool


class ModelTestInput(Model):
    provider: ProviderConfig
    model: ModelConfig


class PluginRepositoryInput(Model):
    id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    url: str = Field(max_length=300)


def ensure_token(home: Path) -> str:
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home / "web.token"
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            stream.write(secrets.token_urlsafe(32))
    return path.read_text(encoding="utf-8").strip()


def create_app(
    home: Path,
    token: str | None = None,
    allowed_hosts: list[str] | None = None,
    log_level: str | None = None,
) -> FastAPI:
    runtime = Application(home)
    manager = logs.LogManager(home, log_level)
    web_log = logs.get_logger("WebUI")

    @asynccontextmanager
    async def lifespan(api):
        api.state.token = token or await asyncio.to_thread(ensure_token, home)
        await asyncio.to_thread(manager.install)
        try:
            await runtime.start()
            try:
                yield
            finally:
                await runtime.close()
        finally:
            manager.uninstall()

    api = FastAPI(title="ShiritaiBot", version="0.1.0", lifespan=lifespan)
    api.state.runtime = runtime
    api.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver", *(allowed_hosts or [])],
    )

    @api.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    async def auth(request: Request):
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not supplied or not hmac.compare_digest(supplied, api.state.token):
            raise HTTPException(401, "需要 WebUI 管理令牌")

    protected = [Depends(auth)]

    @api.exception_handler(PublicError)
    async def public_error(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @api.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default response includes rejected inputs, possibly credentials.
        detail = "\n".join(
            ".".join(map(str, error["loc"][1:])) + ": " + error["msg"] for error in exc.errors()
        )
        return JSONResponse(status_code=422, content={"detail": detail})

    @api.post("/api/providers/discover", dependencies=protected)
    async def discover(body: ProviderConfig):
        return await runtime.llm.discover(body)

    @api.get("/api/stats", dependencies=protected)
    async def stats(hours: int = 24):
        if hours not in {24, 72, 168}:
            raise HTTPException(422, "统计范围必须是 24、72 或 168 小时")
        return await statistics(runtime.store, hours)

    @api.get("/api/commands", dependencies=protected)
    async def commands():
        return [
            {"name": name, "description": description} for name, description in COMMANDS.items()
        ]

    @api.get("/api/health", dependencies=protected)
    async def health():
        return {
            "status": "ok",
            "version": "0.1.0",
            "config_version": runtime.config.version,
            "adapters": {k: x.status for k, x in runtime.adapters.instances.items()},
            "plugins": list(runtime.plugins.loaded),
            "tools": sorted(runtime.registry.tools),
            "actors": len(runtime.runtime.actors),
            "subagents": len(runtime.subagents.tasks),
        }

    @api.get("/api/config", dependencies=protected)
    async def config():
        return runtime.config.value.model_dump()

    @api.put("/api/config", dependencies=protected)
    async def save_config(body: Settings):
        await runtime.reconfigure(body)
        return {"ok": True, "version": runtime.config.version}

    @api.get("/api/config/schema", dependencies=protected)
    async def config_schema():
        return Settings.model_json_schema()

    @api.get("/api/tools", dependencies=protected)
    async def tools(session: str = "web:owner"):
        if not session.startswith(("web:", "private:", "group:")):
            raise HTTPException(422, "仅支持 Main 会话，Proactive Loop 请修改默认权限")
        row = await runtime.store.get("session", session) or {}
        owner = row.get("owner", session.startswith("web:"))
        if session.startswith("private:"):
            owner = owner or any(
                session == f"private:{adapter.id}:{adapter.owner_id}"
                for adapter in runtime.config.value.adapters
            )
        ctx = Context(
            session, row.get("actor", ""), owner=owner, group=session.startswith("group:")
        )
        ctx = runtime.permissions.context(ctx)
        return {
            "session": session,
            "policy": runtime.permissions.session_policy(ctx).model_dump(),
            "source": "session" if session in runtime.config.value.policy.sessions else "default",
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "plugin": tool.plugin,
                    "risk": tool.risk,
                    "safe": tool.safe,
                    "chat": tool.chat,
                    "permission": runtime.permissions.configured(ctx, tool),
                    "decision": runtime.permissions.decision(ctx, tool),
                }
                for tool in sorted(runtime.registry.tools.values(), key=lambda tool: tool.name)
            ],
        }

    @api.get("/api/secrets", dependencies=protected)
    async def secret_names():
        return [r["id"] for r in await runtime.store.list("secret")]

    @api.put("/api/secrets/{name}", dependencies=protected)
    async def secret_set(name: str, body: SecretInput):
        await runtime.vault.set(name, body.value)
        await register_builtin(runtime)
        return {"ok": True}

    @api.get("/api/records/{kind}", dependencies=protected)
    async def records(
        kind: str,
        page: int | None = Query(default=None, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        approval_filter: str = Query(default="", pattern=r"^(pending|history)?$"),
    ):
        if kind not in {
            "activity",
            "turn",
            "session",
            "approval",
            "routine",
            "event",
            "notification",
            "short_term",
            "proactive_trace",
            "memory_summary",
            "memory_judgment",
            "memory_extract",
            "subagent_run",
            "outbox",
            "reply",
            "usage",
        }:
            raise HTTPException(404)
        if page is not None:
            return await runtime.store.page(kind, page, page_size, approval_filter)
        return await runtime.store.list(kind)

    @api.get("/api/logs/raw", dependencies=protected)
    async def raw_logs(lines: int = 500, q: str = ""):
        lines = max(1, min(lines, 5000))
        return {"lines": await asyncio.to_thread(manager.tail, lines, q[:200])}

    @api.get("/api/logs", dependencies=protected)
    async def structured_logs(limit: int = 500, level: str = "", source: str = "", q: str = ""):
        limit = max(1, min(limit, 5000))
        return await asyncio.to_thread(manager.entries, limit, level[:10], source[:200], q[:200])

    @api.get("/api/logs/level", dependencies=protected)
    async def log_level_get():
        return {"level": manager.level_name}

    @api.put("/api/logs/level", dependencies=protected)
    async def log_level_set(body: LogLevelInput):
        before = manager.level_name
        # Announce at whichever of the two levels still lets a LOG line through.
        if body.level in {"warning", "error"}:
            web_log.log(f"日志级别 {before.upper()} → {body.level.upper()}")
        level = await asyncio.to_thread(manager.set_level, body.level)
        if body.level not in {"warning", "error"}:
            web_log.log(f"日志级别 {before.upper()} → {level.upper()}")
        return {"level": level}

    @api.get("/api/history", dependencies=protected)
    async def history(session: str = "web:owner"):
        return await runtime.store.history(session)

    @api.post("/api/messages", dependencies=protected)
    async def message(body: MessageInput):
        where = "" if body.session == "web:owner" else body.session
        web_log.log(logs.brief(body.text, 200), where, "主人")
        return {
            "status": await runtime.runtime.submit(
                Context(body.session, "owner", "agent", True), body.text
            )
        }

    @api.post("/api/approvals/{key}", dependencies=protected)
    async def approval(key: str, body: ApprovalInput):
        await runtime.permissions.resolve(key, body.allow)
        return {"ok": True}

    # Read-only: the Agent maintains memory through tools (DESIGN.md §1.1).
    @api.get("/api/memory", dependencies=protected)
    async def memories(
        kind: str = "",
        q: str = "",
        page: int | None = Query(default=None, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ):
        if kind not in {"", "entity", "fact", "event", "conflict"}:
            raise HTTPException(422, "无效的记忆类型")
        if page is not None:
            return await runtime.memory.browse_page(kind, q[:200], page, page_size)
        return await runtime.memory.browse(kind[:20], q[:200])

    @api.get("/api/memory/status", dependencies=protected)
    async def memory_status():
        return await runtime.memory.vectors.status(await runtime.memory.documents())

    @api.get("/api/memory/{key}", dependencies=protected)
    async def memory_detail(key: str):
        return await runtime.memory.get(key)

    @api.post("/api/routines", dependencies=protected)
    async def routine(body: RoutineItem):
        return await runtime.routine.create(body)

    @api.post("/api/routines/{key}/{status}", dependencies=protected)
    async def routine_status(key: str, status: str):
        try:
            return await runtime.routine.update_status(key, status)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @api.post("/api/events", dependencies=protected)
    async def event(body: ExternalEvent):
        return {"id": await runtime.proactive.emit(body)}

    @api.post("/api/events/batch", dependencies=protected)
    async def event_batch(body: list[ExternalEvent]):
        return {"ids": await runtime.proactive.emit_many(body)}

    @api.get("/api/events/{key}", dependencies=protected)
    async def event_status(key: str):
        row = await runtime.proactive.get_event(key)
        if row is None:
            raise HTTPException(404, "事件不存在")
        return row

    @api.get("/api/plugins", dependencies=protected)
    async def plugins():
        return await runtime.plugins.listing()

    @api.post("/api/plugins/reload", dependencies=protected)
    async def reload_plugins():
        async with runtime.configure_lock:
            return await runtime.plugins.reload_all()

    @api.post("/api/plugins/install/github", dependencies=protected)
    async def install_github_plugin(body: PluginRepositoryInput):
        validate_id(body.id)
        archive = await download_github(body.url)
        async with runtime.configure_lock:
            return await runtime.plugins.install(body.id, archive)

    @api.post("/api/plugins/install/zip/{key}", dependencies=protected)
    async def install_zip_plugin(key: str, request: Request):
        validate_id(key)
        archive = bytearray()
        async for chunk in request.stream():
            archive.extend(chunk)
            if len(archive) > MAX_ARCHIVE:
                raise HTTPException(413, "插件 ZIP 不能超过 20 MiB")
        async with runtime.configure_lock:
            return await runtime.plugins.install(key, bytes(archive))

    @api.delete("/api/plugins/{key}", dependencies=protected)
    async def remove_plugin(key: str):
        validate_id(key)
        async with runtime.configure_lock:
            return await runtime.plugins.remove(key)

    @api.post("/api/plugins/{key}/reload", dependencies=protected)
    async def reload(key: str):
        async with runtime.configure_lock, runtime.plugins.manage_lock:
            await runtime.plugins.sync_config()
            try:
                await runtime.plugins.reload(key)
            except Exception:
                raise PublicError(runtime.plugins.errors.get(key, "插件加载失败")) from None
            return await runtime.plugins.listing()

    @api.post("/api/subagents/{key}/cancel", dependencies=protected)
    async def cancel(key: str):
        await runtime.subagents.cancel(key)
        return {"ok": True}

    @api.post("/api/subagents/{key}/detect", dependencies=protected)
    async def detect(key: str):
        cfg = next((x for x in runtime.config.value.subagents if x.id == key), None)
        if not cfg:
            raise HTTPException(404)
        return await run_process([*cfg.command, "--version"], home, timeout=10)

    async def run_model_test(provider: ProviderConfig, model: ModelConfig):
        if model.provider != provider.id:
            raise PublicError("模型所属的提供商与当前配置不一致，请检查提供商名称")
        try:
            cfg = Settings(
                providers=[provider.model_copy(update={"enabled": True})],
                models=[model.model_copy(update={"enabled": True})],
                routes={"test": [model.id]},
            )
        except ValueError:
            raise PublicError(
                "请检查提供商地址和模型配置；地址须为不含账号和查询参数的 HTTP(S) 地址"
            ) from None
        if model.kind == "embedding":
            _, vectors = await runtime.llm.embed(["Connection test"], settings=cfg, route="test")
            return {"text": f"向量维度 {len(vectors[0])}", "usage": {}}
        if provider.protocol == "systemone":
            answers = await runtime.llm.decide(
                "test",
                "Connection test",
                {"ok": {"type": "noul", "instructions": "Is this a connection test?"}},
                settings=cfg,
            )
            if answers is None:
                raise PublicError("决策模型调用失败，请检查 Provider 地址、凭据与模型名称")
            return {"text": canonical(answers)[:1000], "usage": {}}
        reply = await runtime.llm.complete(
            "test",
            "Reply with OK.",
            [{"role": "user", "content": "Connection test"}],
            [],
            settings=cfg,
        )
        return {"text": reply.text[:1000], "usage": reply.usage}

    @api.post("/api/models/test", dependencies=protected)
    async def test_draft_model(body: ModelTestInput):
        return await run_model_test(body.provider, body.model)

    @api.post("/api/models/{key}/test", dependencies=protected)
    async def test_model(key: str):
        cfg = runtime.config.value
        model = next((m for m in cfg.models if m.id == key), None)
        if model is None:
            raise HTTPException(
                404, "找不到已保存的模型，可能尚未保存或已改名。请刷新页面，使用测试当前配置。"
            )
        provider = next((p for p in cfg.providers if p.id == model.provider), None)
        if provider is None:
            raise PublicError("模型对应的提供商不存在，请检查配置")
        return await run_model_test(provider, model)

    @api.websocket("/api/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        try:
            async with asyncio.timeout(5):
                supplied = await ws.receive_text()
            if not hmac.compare_digest(supplied, api.state.token):
                await ws.close(1008)
                return
            runtime.bus.subscribers.add(queue)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    event = {"kind": "heartbeat"}
                await ws.send_json(event)
        except (WebSocketDisconnect, TimeoutError, RuntimeError):
            pass
        finally:
            runtime.bus.subscribers.discard(queue)

    dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if dist.is_dir():
        api.mount("/", StaticFiles(directory=dist, html=True), name="webui")
    return api
