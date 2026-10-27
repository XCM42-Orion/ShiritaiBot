from __future__ import annotations

import json
from datetime import datetime, timezone

from shiritaibot.core import now


def tokens(usage: dict, protocol: str = "") -> dict:
    def number(key, source=None):
        value = (usage if source is None else source).get(key, 0)
        return max(0, int(value or 0))

    if "promptTokenCount" in usage or protocol == "gemini":
        incoming = number("promptTokenCount")
        outgoing = number("candidatesTokenCount") + number("thoughtsTokenCount")
        cached = number("cachedContentTokenCount")
        total = number("totalTokenCount") or incoming + outgoing
    elif (
        protocol == "anthropic"
        or "cache_read_input_tokens" in usage
        or "cache_creation_input_tokens" in usage
    ):
        cached = number("cache_read_input_tokens")
        incoming = number("input_tokens") + cached + number("cache_creation_input_tokens")
        outgoing = number("output_tokens")
        total = incoming + outgoing
    else:
        incoming = number("input_tokens") if "input_tokens" in usage else number("prompt_tokens")
        outgoing = (
            number("output_tokens") if "output_tokens" in usage else number("completion_tokens")
        )
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        cached = number("cached_tokens", details)
        total = number("total_tokens") or incoming + outgoing
    return {
        "input_tokens": incoming,
        "output_tokens": outgoing,
        "cached_tokens": cached,
        "total_tokens": total,
    }


async def statistics(store, hours: int, end: datetime | None = None):
    end_stamp = (end or now()).timestamp()
    start = end_stamp - hours * 3600
    first, last = int(start // 3600), int(end_stamp // 3600)
    fields = [
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "total_tokens",
        "llm_calls",
        "incoming_messages",
        "outgoing_messages",
        "messages",
        "failed_turns",
    ]
    buckets = [
        {
            "hour": datetime.fromtimestamp(hour * 3600, timezone.utc).isoformat(),
            **dict.fromkeys(fields, 0),
        }
        for hour in range(first, last + 1)
    ]
    rows = await store.query(
        "SELECT kind,data,created FROM records WHERE kind IN ('usage','turn') AND created>=? AND created<=?",
        (start, end_stamp),
    )
    for row in rows:
        bucket = buckets[int(row["created"] // 3600) - first]
        data = json.loads(row["data"])
        if row["kind"] == "usage":
            bucket["llm_calls"] += 1
            for key, value in tokens(data.get("usage") or {}, data.get("protocol", "")).items():
                bucket[key] += value
        elif data.get("status") == "failed":
            bucket["failed_turns"] += 1
    rows = await store.query(
        "SELECT data,created FROM messages WHERE turn!='handoff' AND created>=? AND created<=?",
        (start, end_stamp),
    )
    for row in rows:
        data = json.loads(row["data"])
        role = data.get("role")
        if role not in {"user", "assistant"} or data.get("tool_calls"):
            continue
        bucket = buckets[int(row["created"] // 3600) - first]
        bucket["incoming_messages" if role == "user" else "outgoing_messages"] += 1
        bucket["messages"] += 1
    return {
        "hours": hours,
        "timezone": "Asia/Shanghai",
        "buckets": buckets,
        "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "end": datetime.fromtimestamp(end_stamp, timezone.utc).isoformat(),
        "totals": {key: sum(bucket[key] for bucket in buckets) for key in fields},
    }
