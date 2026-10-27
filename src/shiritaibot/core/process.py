from __future__ import annotations

import asyncio
import os
import shutil
import signal
from pathlib import Path

import psutil

from shiritaibot.core import PublicError


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


async def run_process(
    command: list[str],
    cwd: Path,
    stdin: str = "",
    timeout: float = 60,  # noqa: ASYNC109 -- owns process cleanup on timeout
    env: dict[str, str] | None = None,
    limit: int = 64000,
) -> dict:
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

    async def read(stream) -> tuple[str, bool]:
        output = bytearray()
        size = 0
        while chunk := await stream.read(8192):
            size += len(chunk)
            output.extend(chunk[: max(0, limit - len(output))])
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
                stdout = group.create_task(read(proc.stdout))
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
