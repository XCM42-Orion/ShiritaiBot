"""Absolute-time rules for long-term memory (docs/MEMORY.md §6)."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from shiritaibot.core import PublicError

WEEKDAYS = "一二三四五六日"
# Stored memory must never depend on when it is read.
RELATIVE = re.compile(
    r"大?[前昨今明后]天|[今昨明]早|[今昨明]晚|今晨|"
    r"[上下本这]个?(?:周|星期|礼拜|月)|[今去明前]年|"
    r"刚才|刚刚|最近|近期|前几天|过几天|前阵子|这几天|上次|下次|"
    r"\b(?:yesterday|today|tomorrow|tonight|last (?:week|month|year)|next (?:week|month|year)|recently)\b",
    re.IGNORECASE,
)
FORMATS = (
    (re.compile(r"^\d{4}$"), "year"),
    (re.compile(r"^\d{4}-\d{2}$"), "month"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "day"),
)
ABSOLUTE_HINT = "时间必须是绝对时间：YYYY、YYYY-MM、YYYY-MM-DD 或 YYYY-MM-DDTHH:MM（可带时区）"


def relative_words(text: str) -> list[str]:
    return sorted({m.group(0) for m in RELATIVE.finditer(text)})


def parse_time(value: str, zone: ZoneInfo) -> tuple[float, str]:
    """(UTC timestamp, granularity) for an absolute time; naive values use the owner's zone."""
    value = value.strip()
    try:
        for pattern, granularity in FORMATS:
            if pattern.match(value):
                parts = [int(p) for p in value.split("-")] + [1, 1]
                return datetime(*parts[:3], tzinfo=zone).timestamp(), granularity
        stamp = datetime.fromisoformat(value.replace(" ", "T", 1))
    except ValueError:
        raise PublicError(ABSOLUTE_HINT) from None
    if "T" not in value and " " not in value:
        raise PublicError(ABSOLUTE_HINT)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=zone)
    return stamp.timestamp(), "minute"


def parse_bound(value: str, zone: ZoneInfo, *, end: bool) -> float:
    """A search bound; a date-only `until` covers the whole period it names."""
    stamp, granularity = parse_time(value, zone)
    if not end or granularity == "minute":
        return stamp
    local = datetime.fromtimestamp(stamp, zone)
    if granularity == "year":
        following = local.replace(year=local.year + 1)
    elif granularity == "month":
        following = (
            local.replace(year=local.year + 1, month=1)
            if local.month == 12
            else local.replace(month=local.month + 1)
        )
    else:
        return stamp + 86400 - 0.001
    return following.timestamp() - 0.001


def render_time(stamp: float | None, granularity: str, zone: ZoneInfo, text: str = "") -> str:
    if stamp is None or granularity == "fuzzy":
        return f"时间不详：{text}" if text else "时间不详"
    local = datetime.fromtimestamp(stamp, zone)
    weekday = f"（周{WEEKDAYS[local.weekday()]}）"
    if granularity == "year":
        return f"{local.year} 年"
    if granularity == "month":
        return local.strftime("%Y-%m")
    if granularity == "day":
        return local.strftime("%Y-%m-%d") + weekday
    return local.strftime("%Y-%m-%d %H:%M") + weekday


def month(stamp: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(stamp, zone).strftime("%Y-%m")


def minute(stamp: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(stamp, zone).strftime("%Y-%m-%d %H:%M")
