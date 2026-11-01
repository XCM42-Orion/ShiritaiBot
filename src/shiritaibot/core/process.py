from __future__ import annotations

import asyncio
import os
import shutil
import signal
from pathlib import Path
from typing import Callable

import psutil

from shiritaibot.core import PublicError

# Longest stdout line handed to on_line; anything longer is dropped, which also bounds memory.
LINE_LIMIT = 4 * 1024 * 1024


def resolve_command(command: list[str]) -> list[str]:
    if not command:
        raise PublicError("未配置可执行程序")
    executable = shutil.which(command[0])
    if not executable:
        raise PublicError("找不到配置的可执行程序")
    if Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise PublicError("请配置原生可执行程序，或 [node, CLI入口.js]；不运行 shell 包装脚本")
    return [executable, *command[1:]]


async def kill_tree(proc: asyncio.subprocess.Process) -> None:
    def stop() -> None:
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return
        try:
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
            if os.name != "nt":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                for child in reversed(children):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                parent.kill()
        except psutil.NoSuchProcess:
            pass

    await asyncio.to_thread(stop)
    await proc.wait()


def line_splitter(on_line: Callable[[bytes], None]) -> Callable[[bytes], None]:
    """Reassemble chunks into newline-separated lines; an empty chunk flushes the last line."""
    pending = bytearray()
    oversized = False

    def feed(chunk: bytes) -> None:
        nonlocal oversized
        *lines, rest = chunk.split(b"\n")
        for line in lines:
            pending.extend(line)
            if not oversized and len(pending) <= LINE_LIMIT:
                on_line(bytes(pending))
            pending.clear()
            oversized = False
        pending.extend(rest)
        if not chunk and pending and not oversized and len(pending) <= LINE_LIMIT:
            on_line(bytes(pending))
        if len(pending) > LINE_LIMIT:
            pending.clear()
            oversized = True

    return feed


async def run_process(
    command: list[str],
    cwd: Path,
    stdin: str = "",
    timeout: float = 60,  # noqa: ASYNC109 -- owns process cleanup on timeout
    env: dict[str, str] | None = None,
    limit: int = 64000,
    on_line: Callable[[bytes], None] | None = None,
) -> dict:
    """Run a command, keeping the first `limit` bytes of stdout and stderr.

    `on_line` sees every stdout line as it arrives, including those past `limit`, so callers
    can pick out events from the tail of a long stream. Lines over LINE_LIMIT are skipped.
    """
    argv = await asyncio.to_thread(resolve_command, command)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=0x00000200 if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )

    async def read(stream, sink: Callable[[bytes], None] | None = None) -> tuple[str, bool]:
        output = bytearray()
        size = 0
        while chunk := await stream.read(8192):
            size += len(chunk)
            output.extend(chunk[: max(0, limit - len(output))])
            if sink:
                sink(chunk)
        if sink:
            sink(b"")
        return output.decode("utf-8", errors="replace"), size > limit

    async def feed() -> None:
        assert proc.stdin is not None
        try:
            proc.stdin.write(stdin.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

    try:
        async with asyncio.timeout(timeout):
            async with asyncio.TaskGroup() as group:
                stdout = group.create_task(
                    read(proc.stdout, line_splitter(on_line) if on_line else None)
                )
                stderr = group.create_task(read(proc.stderr))
                group.create_task(feed())
                await proc.wait()
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.result()[0],
            "stderr": stderr.result()[0],
            "truncated": stdout.result()[1] or stderr.result()[1],
        }
    finally:
        # The process group may still contain descendants after the parent exits.
        await asyncio.shield(kill_tree(proc))
