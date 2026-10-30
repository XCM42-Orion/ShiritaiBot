from pathlib import Path

import typer
import uvicorn

from shiritaibot.web import create_app, ensure_token

cli = typer.Typer(help="ShiritaiBot · Personal agent runtime")


@cli.command()
def run(
    home: Path = Path("data"),
    port: int = 8765,
    allowed_host: list[str] = typer.Option(
        [], help="额外允许的 Host（内网穿透/反向代理域名），可重复；支持 *.example.com"
    ),
    log_level: str = typer.Option("info", help="Bot 日志级别：debug / info / warning / error"),
):
    """Run the local control plane and configured adapters."""
    ensure_token(home)
    typer.echo(f"WebUI: http://127.0.0.1:{port} · 管理令牌文件: {home.resolve() / 'web.token'}")
    uvicorn.run(
        create_app(home.resolve(), allowed_hosts=allowed_host, log_level=log_level),
        host="127.0.0.1",
        port=port,
        access_log=False,
    )


@cli.command()
def token(home: Path = Path("data")):
    """Display the local admin token for signing into WebUI."""
    typer.echo(ensure_token(home.resolve()))


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
