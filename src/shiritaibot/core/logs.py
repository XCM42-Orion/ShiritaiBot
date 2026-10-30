"""Rolling file log behind the WebUI's raw log viewer."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "shiritaibot.log"
# uvicorn's own logger does not propagate to root, so it is attached separately.
TARGETS = ("", "uvicorn", "uvicorn.access")


def install(home: Path) -> logging.Handler:
    directory = home / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / LOG_NAME, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    root = logging.getLogger()
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for name in TARGETS:
        logging.getLogger(name).addHandler(handler)
    return handler


def uninstall(handler: logging.Handler) -> None:
    for name in TARGETS:
        logging.getLogger(name).removeHandler(handler)
    handler.close()


def tail(home: Path, lines: int, query: str = "") -> list[str]:
    """Last lines of the current log file, optionally filtered by a substring."""
    path = home / "logs" / LOG_NAME
    if not path.exists():
        return []
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        # Read enough tail to cover the request; filtered views scan further back.
        chunk = min(size, (lines * 400) * (8 if query else 1))
        stream.seek(size - chunk)
        text = stream.read().decode("utf-8", errors="replace")
    rows = text.splitlines()
    if chunk < size and rows:
        rows = rows[1:]
    if query:
        needle = query.lower()
        rows = [row for row in rows if needle in row.lower()]
    return rows[-lines:]
