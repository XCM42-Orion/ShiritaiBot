"""Skill Manager: installable instruction packages in the Agent Skills format.

A skill is a directory whose ``SKILL.md`` starts with YAML frontmatter naming the skill
and describing when to use it; any other files (references, scripts, assets) are read
on demand. A ``.skill`` file is a ZIP of that directory. Skills are instructions, never
executed by the host: running a bundled script still goes through the shell tool.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import yaml

from shiritaibot.config import SKILL_NAME
from shiritaibot.core import PublicError
from shiritaibot.core.logs import get_logger
from shiritaibot.plugin.core.packages import check_entries, extract_entries

log = get_logger("技能")

SKILL_FILE = "SKILL.md"
MAX_ARCHIVE = 10 * 1024 * 1024
MAX_UNPACKED = 50 * 1024 * 1024
MAX_FILE_CHARS = 200_000
MAX_FILES = 500
MAX_DESCRIPTION = 1024
READ_CHUNK = 12000


def parse_skill(text: str) -> tuple[dict, str]:
    """Split SKILL.md into validated frontmatter and the Markdown body."""
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        raise PublicError("SKILL.md 必须以 --- 包围的 YAML frontmatter 开头")
    parts = text.split("\n", 1)
    rest = parts[1] if len(parts) > 1 else ""
    end = rest.find("\n---")
    if rest.startswith("---"):
        end = -1
        header, body = "", rest[3:]
    elif end < 0:
        raise PublicError("SKILL.md 的 frontmatter 缺少结束的 ---")
    else:
        header, body = rest[:end], rest[end + 4 :]
    try:
        meta = yaml.safe_load(header) or {}
    except yaml.YAMLError:
        raise PublicError("SKILL.md 的 frontmatter 不是有效的 YAML") from None
    if not isinstance(meta, dict):
        raise PublicError("SKILL.md 的 frontmatter 必须是键值表")
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not SKILL_NAME.fullmatch(name):
        raise PublicError(
            "技能 name 只能包含小写字母、数字和短横线（1–64 字符，不以短横线开头结尾）"
        )
    if not isinstance(description, str) or not description.strip():
        raise PublicError("技能必须填写 description，说明做什么、何时使用")
    if len(description) > MAX_DESCRIPTION:
        raise PublicError(f"技能 description 不能超过 {MAX_DESCRIPTION} 字符")
    return meta, body.lstrip("\n")


def check_name(name: str) -> None:
    if not SKILL_NAME.fullmatch(name):
        raise PublicError("技能名称只能包含小写字母、数字和短横线")


def relative(path: str) -> PurePosixPath:
    """A safe path inside a skill directory; rejects traversal and hidden parts."""
    value = PurePosixPath(path.strip())
    if (
        not value.parts
        or value.is_absolute()
        or "\\" in path
        or ":" in path
        or any(part in {"", ".", ".."} or part.startswith(".") for part in value.parts)
        or len(path) > 300
    ):
        raise PublicError("文件路径必须是技能目录内的相对路径，不能包含 .. 或隐藏文件")
    return value


def tree_files(directory: Path) -> list[Path]:
    """Regular files under a directory, refusing symlinks anywhere in the tree."""
    files = []
    for item in sorted(directory.rglob("*")):
        if item.is_symlink():
            raise PublicError("技能目录不能包含符号链接")
        rel = item.relative_to(directory)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        if item.is_file():
            files.append(item)
    if len(files) > MAX_FILES:
        raise PublicError(f"技能文件数量不能超过 {MAX_FILES}")
    return files


class SkillManager:
    def __init__(self, home: Path, config):
        self.home, self.config = home, config
        self.root = home / "skills"
        # Skills shipped with ShiritaiBot; an installed skill of the same name overrides one.
        self.bundled = Path(__file__).parent / "builtin"
        self.lock = asyncio.Lock()

    # ----- discovery -------------------------------------------------------------------

    def _scan(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for source, root in (("bundled", self.bundled), ("installed", self.root)):
            if not root.is_dir() or root.is_symlink():
                continue
            for directory in sorted(root.iterdir()):
                if directory.is_symlink() or not (directory / SKILL_FILE).is_file():
                    continue
                if not SKILL_NAME.fullmatch(directory.name):
                    continue
                entry = {
                    "name": directory.name,
                    "description": "",
                    "source": source,
                    "path": str(directory),
                    "overrides": source == "installed" and directory.name in result,
                    "error": "",
                }
                try:
                    meta, _ = parse_skill((directory / SKILL_FILE).read_text("utf-8"))
                    if meta["name"] != directory.name:
                        raise PublicError("frontmatter 的 name 与目录名不一致")
                    entry["description"] = meta["description"].strip()
                    entry["license"] = str(meta.get("license", ""))
                except PublicError as exc:
                    entry["error"] = str(exc)
                except (OSError, UnicodeDecodeError):
                    entry["error"] = "无法读取 SKILL.md"
                result[directory.name] = entry
        return result

    async def listing(self) -> list[dict]:
        disabled = set(self.config.value.skills.disabled)
        rows = await asyncio.to_thread(self._scan)
        return [{**row, "enabled": row["name"] not in disabled} for row in rows.values()]

    async def active(self) -> list[dict]:
        return [row for row in await self.listing() if row["enabled"] and not row["error"]]

    async def _locate(self, name: str, *, enabled_only: bool = False) -> dict:
        check_name(name)
        row = (await asyncio.to_thread(self._scan)).get(name)
        if row is None:
            raise PublicError(f"技能 {name} 不存在，可用 skill_list 查看已安装技能")
        if enabled_only and name in self.config.value.skills.disabled:
            raise PublicError(f"技能 {name} 已停用")
        return row

    async def prompt(self) -> str:
        """System-prompt block listing usable skills (progressive disclosure, level 1)."""
        cfg = self.config.value.skills
        if not cfg.enabled or not cfg.inject:
            return ""
        rows = await self.active()
        if not rows:
            return ""
        lines = "\n".join(f"- {row['name']}：{row['description']}" for row in rows)
        return (
            "\n\n<skills>\n以下是已安装的技能（Skill）。当任务与某个技能的描述相符时，"
            "先调用 skill_read 读取它的 SKILL.md，再严格按其中说明执行；"
            "SKILL.md 提到的其他文件也用 skill_read 按需读取，不要一次全部读入。\n"
            f"{lines}\n</skills>"
        )

    # ----- reading ---------------------------------------------------------------------

    async def detail(self, name: str) -> dict:
        row = await self._locate(name)
        directory = Path(row["path"])

        def collect():
            files = tree_files(directory)
            text = (directory / SKILL_FILE).read_text("utf-8", errors="replace")
            return text, [
                {"path": f.relative_to(directory).as_posix(), "bytes": f.stat().st_size}
                for f in files
            ]

        content, files = await asyncio.to_thread(collect)
        return {
            **row,
            "enabled": name not in self.config.value.skills.disabled,
            "content": content,
            "files": files,
        }

    async def read(self, name: str, path: str = SKILL_FILE, offset: int = 0) -> dict:
        row = await self._locate(name, enabled_only=True)
        directory = Path(row["path"])
        rel = relative(path)

        def load():
            target = directory.joinpath(*rel.parts)
            if target.is_symlink() or not target.is_file():
                available = [f.relative_to(directory).as_posix() for f in tree_files(directory)]
                raise PublicError(f"技能内没有文件 {path}；可用文件：{', '.join(available)}")
            data = target.read_bytes()
            if b"\0" in data[:4096]:
                return {"binary": True, "bytes": len(data)}
            return data.decode("utf-8", errors="replace")

        text = await asyncio.to_thread(load)
        if isinstance(text, dict):
            return {
                "name": name,
                "path": path,
                **text,
                "note": "二进制文件，请用 shell 按需处理",
                "directory": str(directory),
            }
        end = offset + READ_CHUNK
        return {
            "name": name,
            "path": rel.as_posix(),
            "directory": str(directory),
            "content": text[offset:end],
            "total_chars": len(text),
            "next_offset": end if len(text) > end else None,
        }

    # ----- writing ---------------------------------------------------------------------

    def _check_writable(self) -> None:
        if self.root.is_symlink():
            raise PublicError("技能目录不能是符号链接")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def create(self, name: str, files: dict[str, str], replace: bool = False) -> dict:
        """Write a new skill from text files; SKILL.md is required."""
        check_name(name)
        if SKILL_FILE not in files:
            raise PublicError("新技能必须包含 SKILL.md")
        if len(files) > 50:
            raise PublicError("一次最多写入 50 个文件")
        paths = {relative(path): content for path, content in files.items()}
        if any(len(content) > MAX_FILE_CHARS for content in paths.values()):
            raise PublicError(f"单个文件不能超过 {MAX_FILE_CHARS} 字符")
        meta, _ = parse_skill(files[SKILL_FILE])
        if meta["name"] != name:
            raise PublicError("SKILL.md frontmatter 的 name 必须与技能名称一致")
        async with self.lock:

            def write():
                self._check_writable()
                with tempfile.TemporaryDirectory(prefix=".write-", dir=self.root) as staging:
                    stage = Path(staging) / name
                    for rel, content in paths.items():
                        target = stage.joinpath(*rel.parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(content, encoding="utf-8")
                    self._commit(stage, name, replace)

            await asyncio.to_thread(write)
        log.log(f"已写入技能 {name}（{len(files)} 个文件）")
        return await self.detail(name)

    async def update(
        self,
        name: str,
        path: str = SKILL_FILE,
        *,
        content: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        delete: bool = False,
    ) -> dict:
        """Change one file of a skill. A bundled skill is copied to the data home first."""
        row = await self._locate(name)
        rel = relative(path)
        if sum(x is not None for x in (content, old_text)) + int(delete) != 1:
            raise PublicError("content、old_text/new_text 与 delete 三选一")
        if old_text is not None and new_text is None:
            raise PublicError("使用 old_text 时必须同时提供 new_text")
        if delete and rel.as_posix() == SKILL_FILE:
            raise PublicError("不能删除 SKILL.md；要删除整个技能请使用 skill_remove")
        async with self.lock:

            def apply():
                self._check_writable()
                directory = self.root / name
                if row["source"] == "bundled" and not directory.exists():
                    shutil.copytree(
                        row["path"], directory, ignore=shutil.ignore_patterns("__pycache__", ".*")
                    )
                target = directory.joinpath(*rel.parts)
                if target.is_symlink():
                    raise PublicError("不能修改符号链接")
                previous = target.read_text("utf-8") if target.is_file() else None
                if delete:
                    if previous is None:
                        raise PublicError(f"文件 {path} 不存在")
                    target.unlink()
                    return
                if old_text is not None:
                    if previous is None:
                        raise PublicError(f"文件 {path} 不存在")
                    count = previous.count(old_text)
                    if count != 1:
                        raise PublicError(
                            "old_text 未找到"
                            if count == 0
                            else f"old_text 出现了 {count} 次，请提供更长的唯一片段"
                        )
                    text = previous.replace(old_text, new_text or "", 1)
                else:
                    text = content or ""
                if len(text) > MAX_FILE_CHARS:
                    raise PublicError(f"单个文件不能超过 {MAX_FILE_CHARS} 字符")
                if rel.as_posix() == SKILL_FILE:
                    meta, _ = parse_skill(text)
                    if meta["name"] != name:
                        raise PublicError("不能通过修改 name 改名；请新建技能后删除旧技能")
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(target.name + ".tmp")
                temp.write_text(text, encoding="utf-8")
                temp.replace(target)

            await asyncio.to_thread(apply)
        log.log(f"已修改技能 {name} 的 {rel.as_posix()}")
        return await self.detail(name)

    def _commit(self, stage: Path, name: str, replace: bool) -> None:
        """Move a validated staging directory into place (runs in a worker thread)."""
        destination = self.root / name
        if destination.is_symlink():
            raise PublicError("同名技能目录是符号链接，拒绝覆盖")
        if destination.exists():
            if not replace:
                raise PublicError(f"技能 {name} 已安装；如需覆盖请设置 replace")
            trash = stage.parent / ".old"
            destination.rename(trash)
        stage.rename(destination)

    def _install_stage(self, stage: Path, replace: bool) -> str:
        candidates = (
            [stage]
            if (stage / SKILL_FILE).is_file()
            else [d for d in stage.iterdir() if d.is_dir() and (d / SKILL_FILE).is_file()]
        )
        if len(candidates) != 1:
            raise PublicError("技能包中应只有一个包含 SKILL.md 的技能目录")
        directory = candidates[0]
        files = tree_files(directory)
        if sum(f.stat().st_size for f in files) > MAX_UNPACKED:
            raise PublicError("技能包解压后超过 50 MiB")
        meta, _ = parse_skill((directory / SKILL_FILE).read_text("utf-8"))
        name = meta["name"]
        if directory == stage:
            named = stage.parent / name
            stage.rename(named)
            directory = named
        elif directory.name != name:
            directory = directory.rename(directory.with_name(name))
        self._commit(directory, name, replace)
        return name

    async def install_bytes(self, data: bytes, replace: bool = False) -> dict:
        """Install a .skill / .zip archive, or a bare SKILL.md."""
        if len(data) > MAX_ARCHIVE:
            raise PublicError("技能包不能超过 10 MiB")
        async with self.lock:

            def install():
                self._check_writable()
                with tempfile.TemporaryDirectory(prefix=".install-", dir=self.root) as staging:
                    stage = Path(staging) / "content"
                    stage.mkdir()
                    if data.startswith(b"PK"):
                        try:
                            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                                check_entries(archive, MAX_UNPACKED, "技能包")
                                extract_entries(archive, stage)
                        except zipfile.BadZipFile:
                            raise PublicError("技能包不是有效的 ZIP（.skill）文件") from None
                    else:
                        try:
                            text = data.decode("utf-8")
                        except UnicodeDecodeError:
                            raise PublicError(
                                "无法识别的技能文件：需要 .skill/.zip 包或 SKILL.md"
                            ) from None
                        (stage / SKILL_FILE).write_text(text, encoding="utf-8")
                    return self._install_stage(stage, replace)

            name = await asyncio.to_thread(install)
        log.log(f"已安装技能 {name}")
        return await self.detail(name)

    async def install_path(self, path: Path, replace: bool = False) -> dict:
        """Install from a local .skill/.zip/SKILL.md file or a skill directory."""
        if await asyncio.to_thread(path.is_dir):
            if not await asyncio.to_thread((path / SKILL_FILE).is_file):
                raise PublicError("目录中没有 SKILL.md")
            async with self.lock:

                def copy():
                    self._check_writable()
                    files = tree_files(path)
                    if sum(f.stat().st_size for f in files) > MAX_UNPACKED:
                        raise PublicError("技能目录超过 50 MiB")
                    with tempfile.TemporaryDirectory(prefix=".install-", dir=self.root) as staging:
                        stage = Path(staging) / "content"
                        for item in files:
                            target = stage / item.relative_to(path)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(item, target)
                        return self._install_stage(stage, replace)

                name = await asyncio.to_thread(copy)
            log.log(f"已从目录安装技能 {name}")
            return await self.detail(name)
        if not await asyncio.to_thread(path.is_file):
            raise PublicError("文件不存在")
        if (await asyncio.to_thread(path.stat)).st_size > MAX_ARCHIVE:
            raise PublicError("技能包不能超过 10 MiB")
        return await self.install_bytes(await asyncio.to_thread(path.read_bytes), replace)

    async def remove(self, name: str) -> dict:
        row = await self._locate(name)
        if row["source"] == "bundled":
            raise PublicError("内置技能不能删除，可在设置中停用")
        async with self.lock:
            await asyncio.to_thread(shutil.rmtree, self.root / name)
        log.log(f"已删除技能 {name}")
        restored = (await asyncio.to_thread(self._scan)).get(name)
        return {"removed": name, "bundled_restored": restored is not None}

    async def pack(self, name: str, folder: Path) -> dict:
        """Write <name>.skill (a ZIP with a top-level <name>/ directory) into folder."""
        row = await self._locate(name)
        directory = Path(row["path"])

        def build():
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{name}.skill"
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                for item in tree_files(directory):
                    archive.write(item, f"{name}/{item.relative_to(directory).as_posix()}")
            return target

        target = await asyncio.to_thread(build)
        return {"path": str(target), "bytes": target.stat().st_size}
