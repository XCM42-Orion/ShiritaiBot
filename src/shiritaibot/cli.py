import logging
from copy import copy, deepcopy
from pathlib import Path
from urllib.parse import urlencode

import typer
import uvicorn
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import DefaultFormatter

from shiritaibot.web import create_app, ensure_token

cli = typer.Typer(help="ShiritaiBot · Personal agent runtime")


class LoginLinkFormatter(DefaultFormatter):
    """Add the sign-in link only to terminal output, not shared log records."""

    def __init__(self, *, token: str, **kwargs):
        super().__init__(**kwargs)
        self.token = token

    def formatMessage(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        prefix, suffix = "Uvicorn running on ", " (Press CTRL+C to quit)"
        if record.name == "uvicorn.error" and message.startswith(prefix + "http"):
            address = message.removeprefix(prefix).removesuffix(suffix)
            record = copy(record)
            record.msg = f"{prefix}{address}/token#{urlencode({'token': self.token})}{suffix}"
            record.args = ()
            record.message = record.getMessage()
            record.__dict__["color_message"] = record.message
        return super().formatMessage(record)


@cli.command()
def run(
    home: Path = Path("data"),
    port: int = 8765,
    allowed_host: list[str] = typer.Option(
        [], help="额外允许的 Host（内网穿透/反向代理域名），可重复；支持 *.example.com"
    ),
    log_level: str | None = typer.Option(
        None,
        help="本次运行的 Bot 日志级别：debug / info / warning / error；不填则使用 WebUI 中保存的级别",
    ),
):
    """Run the local control plane and configured adapters."""
    home = home.resolve()
    admin_token = ensure_token(home)
    log_config = deepcopy(LOGGING_CONFIG)
    log_config["formatters"]["default"].update({"()": LoginLinkFormatter, "token": admin_token})
    uvicorn.run(
        create_app(home, token=admin_token, allowed_hosts=allowed_host, log_level=log_level),
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_config=log_config,
    )


@cli.command()
def token(
    home: Path = Path("data"),
    create: bool = typer.Option(False, help="目录尚未启动过时生成新令牌"),
):
    """Display the local admin token for signing into WebUI."""
    home = home.resolve()
    if create:
        typer.echo(ensure_token(home))
        return
    path = home / "web.token"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise typer.BadParameter(
            f"无法读取管理令牌：{path}（{error.strerror}）。请使用启动时的 --home 目录。"
        ) from error
    if not value:
        raise typer.BadParameter(f"管理令牌文件为空：{path}")
    typer.echo(value)


@cli.command()
def doctor():
    """Check executable availability without contacting providers."""
    import shutil
    import sqlite3
    import sys

    typer.echo(f"Python {sys.version.split()[0]} · SQLite {sqlite3.sqlite_version}")
    for name in ["bash", "codex", "claude", "lark-cli", "node"]:
        typer.echo(f"{name}: {shutil.which(name) or 'not found'}")
    typer.echo("Windows 的 .cmd/.ps1 入口须改用原生 executable 或 node + CLI.js。")


if __name__ == "__main__":
    cli()
