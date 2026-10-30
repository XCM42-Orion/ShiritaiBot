"""LogManager: colored console output, rolling text log and the WebUI's structured feed.

Components log through ``Logger`` instances (``get_logger("Proactive")``). Every line reads
``[LEVEL][time][source][tag…]: message`` so the terminal stays legible without JSON dumps.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOG_NAME = "shiritaibot.log"
FEED_NAME = "shiritaibot.jsonl"
ROOT = "shiritaibot"
# uvicorn's own logger does not propagate to root, so it is attached separately.
TARGETS = ("", "uvicorn", "uvicorn.access")
# INFO is presented as LOG, matching Logger.log.
LABELS = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "LOG",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "ERROR",
}
RANKS = {"DEBUG": 0, "LOG": 1, "WARNING": 2, "ERROR": 3}
COLORS = {"DEBUG": "\033[90m", "LOG": "\033[32m", "WARNING": "\033[33m", "ERROR": "\033[31m"}
DIM, CYAN, BLUE, RESET = "\033[2m", "\033[36m", "\033[34m", "\033[0m"


def level_number(level: str | int) -> int:
    if isinstance(level, int):
        return level
    name = level.strip().upper()
    value = logging.getLevelName({"LOG": "INFO", "WARN": "WARNING"}.get(name, name))
    return value if isinstance(value, int) else logging.INFO


def brief(value: Any, limit: int = 80) -> str:
    """One readable line: whitespace collapsed, mappings as k=v, long text cut with …"""
    if isinstance(value, str):
        text = " ".join(value.split())
    elif isinstance(value, dict):
        text = ", ".join(
            f"{key}={brief(item, 40)}"
            for key, item in value.items()
            if item not in (None, "", [], {})
        )
    elif isinstance(value, (list, tuple)):
        if value and all(isinstance(item, (str, int, float)) for item in value):
            text = "、".join(brief(item, 30) for item in value[:5])
            text += f" 等 {len(value)} 项" if len(value) > 5 else ""
        else:
            text = f"{len(value)} 项"
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_session(session: str) -> str:
    """Human label for a session key such as ``group:qq:123``."""
    kind, _, rest = session.partition(":")
    adapter, _, target = rest.partition(":")
    if kind == "private" and target:
        return f"{adapter}·私聊 {target}"
    if kind == "group" and target:
        return f"{adapter}·群 {target}"
    if kind == "web":
        return "WebUI" if rest == "owner" else f"WebUI·{rest}"
    if session.startswith("background:proactive:"):
        return "事件 " + session.removeprefix("background:proactive:")[:8]
    if kind == "background":
        return "后台·" + rest
    return session


def _parts(record: logging.LogRecord) -> tuple[str, str, tuple[str, ...], str]:
    label = LABELS.get(record.levelno, record.levelname)
    source = getattr(record, "source", "") or record.name.removeprefix(ROOT + ".") or ROOT
    return label, source, tuple(getattr(record, "tags", ())), record.getMessage()


class TextFormatter(logging.Formatter):
    def __init__(self, color: bool = False):
        super().__init__(datefmt="%Y-%m-%d %H:%M:%S")
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        label, source, tags, message = _parts(record)
        stamp = self.formatTime(record, self.datefmt)
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        if not self.color:
            return (
                f"[{label}][{stamp}][{source}]" + "".join(f"[{t}]" for t in tags) + ": " + message
            )
        tint = COLORS.get(label, "")
        body = message if label == "LOG" else f"{tint}{message}{RESET}"
        return (
            f"{tint}[{label}]{RESET}{DIM}[{stamp}]{RESET}{CYAN}[{source}]{RESET}"
            + "".join(f"{BLUE}[{t}]{RESET}" for t in tags)
            + ": "
            + body
        )


class FeedFormatter(logging.Formatter):
    """One JSON object per line; consumed only by LogManager.entries."""

    def format(self, record: logging.LogRecord) -> str:
        label, source, tags, message = _parts(record)
        entry = {
            "at": datetime.fromtimestamp(record.created).astimezone().isoformat(),
            "level": label,
            "source": source,
            "tags": list(tags),
            "message": message,
        }
        if record.exc_info and record.exc_info[1] is not None:
            entry["error"] = type(record.exc_info[1]).__name__
        return json.dumps(entry, ensure_ascii=False)


def _tail(path: Path, lines: int, factor: int = 1) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        chunk = min(size, lines * 400 * factor)
        stream.seek(size - chunk)
        text = stream.read().decode("utf-8", errors="replace")
    rows = text.splitlines()
    if chunk < size and rows:
        rows = rows[1:]
    return rows


class Logger:
    """Component logger. Extra tags render as further ``[...]`` brackets after the source."""

    def __init__(self, source: str, *tags: Any):
        self.source = source
        self.tags = tuple(str(tag) for tag in tags if tag not in (None, ""))
        self._logger = logging.getLogger(f"{ROOT}.{source}")

    def bind(self, *tags: Any) -> Logger:
        """A logger with the same source and additional fixed tags."""
        return Logger(self.source, *self.tags, *tags)

    def enabled(self, level: str | int) -> bool:
        return self._logger.isEnabledFor(level_number(level))

    def _emit(
        self, level: int, message: Any, tags: tuple, exc: BaseException | None = None
    ) -> None:
        if not self._logger.isEnabledFor(level):
            return
        self._logger.log(
            level,
            "%s",
            message,
            exc_info=(type(exc), exc, exc.__traceback__) if exc is not None else None,
            extra={
                "source": self.source,
                "tags": (*self.tags, *(str(t) for t in tags if t not in (None, ""))),
            },
        )

    def debug(self, message: Any, *tags: Any, exc: BaseException | None = None) -> None:
        self._emit(logging.DEBUG, message, tags, exc)

    def log(self, message: Any, *tags: Any, exc: BaseException | None = None) -> None:
        self._emit(logging.INFO, message, tags, exc)

    info = log

    def warning(self, message: Any, *tags: Any, exc: BaseException | None = None) -> None:
        self._emit(logging.WARNING, message, tags, exc)

    def error(self, message: Any, *tags: Any, exc: BaseException | None = None) -> None:
        self._emit(logging.ERROR, message, tags, exc)


def get_logger(source: str, *tags: Any) -> Logger:
    return Logger(source, *tags)


class LogManager:
    """Owns the handlers: colored terminal, rolling text file and rolling JSON feed."""

    def __init__(self, home: Path, level: str | int = "INFO", console: bool = True):
        self.directory = home / "logs"
        self.level = level_number(level)
        self.console = console
        self.handlers: list[tuple[str, logging.Handler]] = []

    get = staticmethod(get_logger)

    def install(self) -> LogManager:
        self.directory.mkdir(parents=True, exist_ok=True)
        text = RotatingFileHandler(
            self.directory / LOG_NAME, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        text.setFormatter(TextFormatter())
        feed = RotatingFileHandler(
            self.directory / FEED_NAME, maxBytes=5 * 1024 * 1024, backupCount=1, encoding="utf-8"
        )
        feed.setFormatter(FeedFormatter())
        root = logging.getLogger()
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        for noisy in ("httpx", "httpcore", "websockets", "aiosqlite", "jieba"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        bot = logging.getLogger(ROOT)
        bot.setLevel(self.level)
        self.handlers = [(name, text) for name in TARGETS] + [(ROOT, feed)]
        if self.console:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(TextFormatter(color=self._colorful(sys.stderr)))
            self.handlers.append((ROOT, stream))
        for name, handler in self.handlers:
            logging.getLogger(name).addHandler(handler)
        return self

    @staticmethod
    def _colorful(stream) -> bool:
        if os.environ.get("NO_COLOR") or not hasattr(stream, "isatty") or not stream.isatty():
            return False
        if os.name == "nt":
            os.system("")  # Enables ANSI escape processing in the Windows console.
        return True

    def uninstall(self) -> None:
        closed = set()
        for name, handler in self.handlers:
            logging.getLogger(name).removeHandler(handler)
            if id(handler) not in closed:
                closed.add(id(handler))
                handler.close()
        self.handlers = []

    def tail(self, lines: int, query: str = "") -> list[str]:
        """Last lines of the rolling text log, optionally filtered by a substring."""
        rows = _tail(self.directory / LOG_NAME, lines, 8 if query else 1)
        if query:
            needle = query.lower()
            rows = [row for row in rows if needle in row.lower()]
        return rows[-lines:]

    def entries(self, limit: int = 500, level: str = "", source: str = "", query: str = "") -> dict:
        """Recent structured entries plus the sources seen, for the WebUI log viewer."""
        filtered = bool(source or query or RANKS.get(level.upper(), 0))
        rows = []
        for line in _tail(self.directory / FEED_NAME, limit, 8 if filtered else 1):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and "message" in entry:
                rows.append(entry)
        sources = sorted({row.get("source", "") for row in rows} - {""})
        minimum = RANKS.get(level.upper(), 0)
        needle = query.lower()
        rows = [
            row
            for row in rows
            if RANKS.get(row.get("level", ""), 1) >= minimum
            and (not source or row.get("source") == source)
            and (
                not needle
                or needle in row["message"].lower()
                or any(needle in str(tag).lower() for tag in row.get("tags", []))
            )
        ]
        return {"entries": rows[-limit:], "sources": sources}
