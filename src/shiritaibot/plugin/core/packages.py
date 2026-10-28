"""Bounded ZIP installation. Plugin code is only executed by PluginManager."""

from __future__ import annotations

import io
import re
import shutil
import stat
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

import httpx
from jsonschema import Draft202012Validator

from shiritaibot.core import PublicError

MAX_ARCHIVE = 20 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024


def validate_id(key: str) -> None:
    if not re.fullmatch(r"[a-z0-9_]{1,64}", key) or key in {"core", "__pycache__"}:
        raise PublicError("插件标识只能包含小写字母、数字和下划线，且不能为保留名称")


def install_archive(root: Path, key: str, data: bytes) -> None:
    validate_id(key)
    if len(data) > MAX_ARCHIVE:
        raise PublicError("插件 ZIP 不能超过 20 MiB")
    if root.is_symlink():
        raise PublicError("插件安装目录不能是符号链接")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / key
    if destination.exists() or destination.is_symlink():
        raise PublicError("同名插件目录已存在")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(i.file_size for i in entries) > MAX_UNPACKED:
                raise PublicError("插件解压后过大或文件数量超过 2000")
            for item in entries:
                path = PurePosixPath(item.filename)
                mode = item.external_attr >> 16
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or ":" in item.filename
                    or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))
                ):
                    raise PublicError("插件 ZIP 包含不安全的路径或文件类型")
            with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as staging:
                stage = Path(staging)
                for item in entries:
                    path = PurePosixPath(item.filename)
                    if any(p in {".git", "__pycache__"} for p in path.parts):
                        continue
                    target = stage.joinpath(*path.parts)
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(item) as source, target.open("xb") as output:
                            shutil.copyfileobj(source, output)
                manifests = list(stage.rglob("plugin.toml"))
                manifest = stage / "plugin.toml"
                if not manifest.is_file():
                    if len(manifests) != 1:
                        raise PublicError("ZIP 中应包含一个插件及其 plugin.toml")
                    manifest = manifests[0]
                directory = manifest.parent
                meta = tomllib.loads(manifest.read_text("utf-8"))
                if meta.get("api") != 1 or not (directory / "main.py").is_file():
                    raise PublicError("插件需要 api = 1 的清单和 main.py 入口")
                schema = meta.get("settings_schema", {"type": "object"})
                Draft202012Validator.check_schema(schema)
                if schema.get("type") != "object" or not isinstance(meta.get("settings", {}), dict):
                    raise PublicError("插件配置定义无效")
                compile((directory / "main.py").read_text("utf-8"), "main.py", "exec")
                directory.rename(destination)
    except PublicError:
        raise
    except Exception:
        raise PublicError("插件 ZIP 无效，请检查清单、配置定义和 Python 入口") from None


def remove_directory(root: Path, key: str) -> None:
    validate_id(key)
    directory = root / key
    if root.is_symlink() or directory.is_symlink():
        raise PublicError("不能删除符号链接形式的插件目录")
    if directory.is_dir():
        shutil.rmtree(directory)


async def download_github(url: str) -> bytes:
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", url.strip()
    )
    if not match or any(p in {".", ".."} for p in match.groups()):
        raise PublicError("请填写公开 GitHub 仓库地址：https://github.com/作者/仓库")
    owner, repo = match.groups()
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            async with client.stream(
                "GET",
                f"https://api.github.com/repos/{owner}/{repo}/zipball",
                headers={"Accept": "application/vnd.github+json"},
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_ARCHIVE:
                        raise PublicError("GitHub 插件包不能超过 20 MiB")
                return bytes(data)
    except httpx.HTTPError:
        raise PublicError("GitHub 下载失败，请检查仓库是否公开、网络连接和 API 访问限额") from None
