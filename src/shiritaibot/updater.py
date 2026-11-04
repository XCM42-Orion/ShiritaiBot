"""Self-update from GitHub releases for installer-managed deployments.

The installer lays out ``<root>/app`` (source), ``<root>/data`` and ``<root>/runtime``. An
update downloads the chosen source archive into ``<root>/.update``, builds the WebUI there,
then swaps the top-level entries of ``app`` (keeping ``.venv`` and concrete plugins),
syncs Python dependencies in place and finally asks ``shiritaibot run`` to re-execute.
Any failure before the restart moves the previous source back.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import tarfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

from shiritaibot.core import PublicError
from shiritaibot.core.logs import get_logger
from shiritaibot.core.process import run_process

REPOSITORY = "ShiritaiBot-Devs/ShiritaiBot"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPOSITORY}"
ARCHIVE = f"https://codeload.github.com/{REPOSITORY}/tar.gz/"
MARKER = ".shiritaibot-release.json"
MAX_ARCHIVE = 200 * 1024 * 1024
CACHE_SECONDS = 300
MAX_RELEASE_PAGES = 10
REQUIRED = ("src/shiritaibot/cli.py", "pyproject.toml", "uv.lock", "web/package-lock.json")
# Entries of the deployed tree that survive an update untouched.
KEEP = {".venv"}
PLUGIN_DIR = Path("src/shiritaibot/plugin")
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
SHA = re.compile(r"^[0-9a-f]{40}$")

log = get_logger("更新")


def package_version() -> str:
    try:
        return metadata.version("shiritaibot")
    except metadata.PackageNotFoundError:
        from shiritaibot import __version__

        return __version__


def first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


class Updater:
    def __init__(
        self,
        app_dir: Path,
        restart: Callable[[], object] | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.app_dir = app_dir
        self.root = app_dir.parent
        self.work = self.root / ".update"
        self.restart = restart
        self.transport = transport
        self.cache: dict[str, tuple[float, Any]] = {}
        self.task: asyncio.Task | None = None
        self.output: deque[str] = deque(maxlen=60)
        self.state: dict = {"phase": "idle", "message": "", "target": None}

    # ----- availability and current version -------------------------------------------------

    def unavailable(self) -> str | None:
        if self.restart is None:
            return "当前启动方式不支持自动更新，请使用 shiritaibot run 启动"
        if not (self.root / ".shiritaibot-installer").is_file():
            return "仅支持一键部署脚本安装的实例；源码开发环境请使用 git 更新"
        return None

    def current(self) -> dict:
        try:
            marker = json.loads((self.app_dir / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = {}
        return {
            "version": package_version(),
            "kind": marker.get("kind"),
            "ref": marker.get("ref"),
            "sha": marker.get("sha"),
            "deployed_at": marker.get("deployed_at"),
        }

    def status(self) -> dict:
        return {
            **self.state,
            "output": list(self.output),
            "available": self.unavailable() is None,
            "reason": self.unavailable(),
            "current": self.current(),
            "repository": REPOSITORY,
        }

    # ----- GitHub queries --------------------------------------------------------------------

    def client(self, timeout: float = 20) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            transport=self.transport,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "ShiritaiBot-Updater",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def get(self, client: httpx.AsyncClient, path: str, **params):
        try:
            response = await client.get(API + path, params=params or None)
        except httpx.HTTPError:
            raise PublicError("无法连接 GitHub，请检查网络或代理设置") from None
        if (
            response.status_code in {403, 429}
            and response.headers.get("x-ratelimit-remaining") == "0"
        ):
            reset = response.headers.get("x-ratelimit-reset", "")
            when = (
                datetime.fromtimestamp(int(reset)).strftime("%H:%M") if reset.isdigit() else "稍后"
            )
            raise PublicError(f"GitHub API 访问次数已用完，请在 {when} 后重试")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise PublicError(f"GitHub 返回错误（HTTP {response.status_code}）")
        return response.json()

    async def cached(self, key: str, load: Callable[[], Awaitable[Any]], refresh: bool) -> Any:
        hit = self.cache.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = await load()
        self.cache[key] = (time.monotonic(), value)
        return value

    async def all_releases(self, refresh: bool = False) -> list[dict]:
        async def load():
            rows: list[dict] = []
            async with self.client() as client:
                for page in range(1, MAX_RELEASE_PAGES + 1):
                    batch = await self.get(client, "/releases", per_page=100, page=page) or []
                    rows.extend(batch)
                    if len(batch) < 100:
                        break
            return [
                {
                    "tag": row["tag_name"],
                    "name": row.get("name") or row["tag_name"],
                    "body": row.get("body") or "",
                    "prerelease": bool(row.get("prerelease")),
                    "published_at": row.get("published_at"),
                    "url": row.get("html_url"),
                }
                for row in rows
                if not row.get("draft") and TAG.match(row.get("tag_name") or "")
            ]

        return await self.cached("releases", load, refresh)

    async def releases(self, page: int, size: int, prerelease: bool, refresh: bool) -> dict:
        rows = await self.all_releases(refresh)
        if not prerelease:
            rows = [row for row in rows if not row["prerelease"]]
        start = (page - 1) * size
        return {"items": rows[start : start + size], "total": len(rows), "page": page}

    async def preview(self, refresh: bool = False) -> dict | None:
        """The head of the default branch when it has commits no release contains yet."""

        async def load():
            releases = await self.all_releases(refresh)
            async with self.client() as client:
                head = await self.get(client, f"/commits/{BRANCH}")
                if head is None:
                    return None
                base = releases[0]["tag"] if releases else None
                if base:
                    compare = await self.get(client, f"/compare/{base}...{head['sha']}")
                    if compare is None or compare.get("ahead_by", 0) == 0:
                        return None
                    ahead = compare["ahead_by"]
                    commits = list(reversed(compare.get("commits") or []))
                else:
                    commits = await self.get(client, "/commits", sha=head["sha"], per_page=30) or []
                    ahead = len(commits)
            return {
                "sha": head["sha"],
                "message": first_line(head["commit"]["message"]),
                "date": head["commit"]["committer"]["date"],
                "url": head.get("html_url"),
                "base": base,
                "ahead_by": ahead,
                "commits": [
                    {
                        "sha": row["sha"],
                        "message": first_line(row["commit"]["message"]),
                        "author": (row["commit"].get("author") or {}).get("name", ""),
                        "date": (row["commit"].get("committer") or {}).get("date"),
                    }
                    for row in commits[:100]
                ],
            }

        return await self.cached("preview", load, refresh)

    # ----- deployment ------------------------------------------------------------------------

    async def apply(self, kind: str, ref: str) -> dict:
        if reason := self.unavailable():
            raise PublicError(reason)
        if self.task and not self.task.done():
            raise PublicError("已有更新正在进行")
        if kind == "release":
            if not TAG.match(ref) or ref not in {r["tag"] for r in await self.all_releases()}:
                raise PublicError("找不到该版本，请刷新版本列表")
            archive = ARCHIVE + "refs/tags/" + ref
        elif kind == "commit":
            if not SHA.match(ref):
                raise PublicError("提交编号无效")
            archive = ARCHIVE + ref
        else:
            raise PublicError("未知的更新类型")
        self.output.clear()
        self.state = {
            "phase": "downloading",
            "message": "正在下载源码",
            "target": {"kind": kind, "ref": ref},
        }
        self.task = asyncio.create_task(self.run(kind, ref, archive))
        return self.status()

    def step(self, phase: str, message: str) -> None:
        self.state = {**self.state, "phase": phase, "message": message}
        self.output.append(f"» {message}")
        log.log(message)

    async def run(self, kind: str, ref: str, archive: str) -> None:
        stage = self.work / ("stage-" + secrets.token_hex(4))
        try:
            await asyncio.to_thread(self.work.mkdir, exist_ok=True)
            await asyncio.to_thread(self.cleanup_stages)
            self.step("downloading", f"正在下载 {ref[:12] if kind == 'commit' else ref}")
            source, sha = await self.download(archive, stage)
            self.step("building", "正在安装 WebUI 依赖")
            await self.command(self.npm("--prefix", "web", "ci", "--no-audit", "--no-fund"), source)
            self.step("building", "正在构建 WebUI")
            await self.command(self.npm("--prefix", "web", "run", "build"), source)
            if not (source / "web" / "dist" / "index.html").is_file():
                raise PublicError("WebUI 构建没有生成页面")
            marker = {
                "kind": kind,
                "ref": ref,
                "sha": sha if kind == "release" else ref,
                "deployed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            (source / MARKER).write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
            self.step("deploying", "正在替换程序文件")
            backup = stage / "previous"
            await asyncio.to_thread(self.swap, source, backup)
            try:
                self.step("deploying", "正在同步 Python 依赖")
                await self.sync()
            except BaseException:
                self.step("deploying", "部署失败，正在恢复原版本")
                await asyncio.to_thread(self.restore, backup, stage / "failed")
                await asyncio.shield(self.sync(quiet=True))
                raise
            await asyncio.to_thread(self.keep_previous, backup)
            self.step("restarting", "部署完成，正在重启")
            assert self.restart is not None
            self.restart()
        except Exception as error:
            message = str(error) if isinstance(error, PublicError) else f"更新失败：{error!r}"
            self.state = {**self.state, "phase": "failed", "message": message}
            self.output.append("✗ " + message)
            log.log(message)
        finally:
            await asyncio.to_thread(shutil.rmtree, stage, True)

    def cleanup_stages(self) -> None:
        for path in self.work.glob("stage-*"):
            shutil.rmtree(path, ignore_errors=True)

    async def download(self, url: str, stage: Path) -> tuple[Path, str]:
        await asyncio.to_thread(stage.mkdir, parents=True)
        path = stage / "source.tar.gz"
        size = 0
        try:
            async with self.client(timeout=120) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise PublicError(f"源码下载失败（HTTP {response.status_code}）")
                    with path.open("wb") as stream:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_ARCHIVE:
                                raise PublicError("源码包过大，已取消下载")
                            stream.write(chunk)
        except httpx.HTTPError:
            raise PublicError("源码下载失败，请检查网络连接") from None
        return await asyncio.to_thread(self.extract, path, stage / "source")

    @staticmethod
    def extract(path: Path, target: Path) -> tuple[Path, str]:
        try:
            with tarfile.open(path, "r:gz") as archive:
                archive.extractall(target, filter="data")
                # git archive records the commit in the global pax header.
                sha = archive.pax_headers.get("comment", "")
        except (tarfile.TarError, OSError) as error:
            raise PublicError(f"源码包无法解压：{error}") from None
        entries = list(target.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise PublicError("源码包结构异常")
        source = entries[0]
        if not all((source / name).is_file() for name in REQUIRED):
            raise PublicError("下载的源码不完整")
        return source, sha if SHA.match(sha) else ""

    def swap(self, source: Path, backup: Path) -> None:
        """Move current entries into backup, then the new source into place."""
        backup.mkdir()
        for entry in list(self.app_dir.iterdir()):
            if entry.name not in KEEP:
                entry.rename(backup / entry.name)
        try:
            for entry in list(source.iterdir()):
                if entry.name not in KEEP:
                    entry.rename(self.app_dir / entry.name)
            # Concrete plugins live beside the host core; carry over those the release lacks.
            old_plugins = backup / PLUGIN_DIR
            new_plugins = self.app_dir / PLUGIN_DIR
            if old_plugins.is_dir() and new_plugins.is_dir():
                for plugin in old_plugins.iterdir():
                    if plugin.is_dir() and not (new_plugins / plugin.name).exists():
                        shutil.copytree(plugin, new_plugins / plugin.name, symlinks=True)
        except BaseException:
            self.restore(backup, source.parent / "failed")
            raise

    def restore(self, backup: Path, discard: Path) -> None:
        discard.mkdir(exist_ok=True)
        for entry in list(self.app_dir.iterdir()):
            if entry.name not in KEEP:
                entry.rename(discard / entry.name)
        for entry in list(backup.iterdir()):
            entry.rename(self.app_dir / entry.name)

    def keep_previous(self, backup: Path) -> None:
        """Retain one previous deployment (without node_modules) for manual rollback."""
        previous = self.work / "previous"
        shutil.rmtree(previous, ignore_errors=True)
        shutil.rmtree(backup / "web" / "node_modules", ignore_errors=True)
        backup.rename(previous)

    def npm(self, *args: str) -> list[str]:
        if os.name == "nt":
            node = shutil.which("node")
            cli = (
                Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js" if node else None
            )
            if not node or not cli or not cli.is_file():
                raise PublicError("找不到 Node.js/npm，无法构建 WebUI")
            return [node, str(cli), *args]
        if not shutil.which("npm"):
            raise PublicError("找不到 npm，无法构建 WebUI")
        return ["npm", *args]

    async def sync(self, quiet: bool = False) -> None:
        uv = os.environ.get("UV") or shutil.which("uv")
        if not uv:
            raise PublicError("找不到 uv，无法安装 Python 依赖")
        # --inexact keeps pip and packages that plugins installed into the environment.
        # --no-install-project skips rewriting the project and its console script: on Windows
        # the running shiritaibot.exe is locked, so reinstalling it fails (WinError 32) and can
        # uninstall the package mid-update. The existing editable install already points at
        # this source tree, so new code loads on restart.
        try:
            await self.command(
                [uv, "sync", "--frozen", "--no-dev", "--inexact", "--no-install-project"],
                self.app_dir,
            )
        except Exception:
            if not quiet:
                raise

    async def command(self, argv: list[str], cwd: Path) -> None:
        def line(raw: bytes) -> None:
            text = raw.decode("utf-8", errors="replace").rstrip()
            if text:
                self.output.append(text[:300])

        env = {**os.environ, "CI": "1", "NO_COLOR": "1"}
        result = await run_process(argv, cwd, timeout=1200, env=env, on_line=line)
        for text in result["stderr"].splitlines()[-20:]:
            if text.strip():
                self.output.append(text[:300])
        if result["exit_code"] != 0:
            raise PublicError(f"{Path(argv[0]).name} 执行失败（退出码 {result['exit_code']}）")
