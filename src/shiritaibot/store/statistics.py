from __future__ import annotations

import json
from datetime import datetime, timezone

from shiritaibot.core import now

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_tokens", "total_tokens")


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


def cost(counts: dict, model) -> float | None:
    """Estimated spend from per-million prices; None when the model has no prices set."""
    if model is None or not (model.price_input or model.price_cached or model.price_output):
        return None
    cached = min(counts["cached_tokens"], counts["input_tokens"])
    return (
        (counts["input_tokens"] - cached) * model.price_input
        + cached * (model.price_cached or model.price_input)
        + counts["output_tokens"] * model.price_output
    ) / 1_000_000


async def statistics(store, hours: int, end: datetime | None = None, models=()):
    """`models` are the configured ModelConfig rows; they price the per-model breakdown."""
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
            "models": {},
        }
        for hour in range(first, last + 1)
    ]
    rows = await store.query(
        "SELECT kind,data,created FROM records WHERE kind IN ('usage','turn') AND created>=? AND created<=?",
        (start, end_stamp),
    )
    per_model: dict[str, dict] = {}
    for row in rows:
        bucket = buckets[int(row["created"] // 3600) - first]
        data = json.loads(row["data"])
        if row["kind"] == "usage":
            bucket["llm_calls"] += 1
            name = str(data.get("model") or "")
            entry = per_model.setdefault(
                name,
                {"model": name, "llm_calls": 0, **dict.fromkeys(TOKEN_FIELDS, 0)},
            )
            entry["llm_calls"] += 1
            counted = tokens(data.get("usage") or {}, data.get("protocol", ""))
            for key, value in counted.items():
                bucket[key] += value
                entry[key] += value
            bucket["models"][name] = bucket["models"].get(name, 0) + counted["total_tokens"]
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
    configured = {model.id: model for model in models}
    costs: dict[str, float] = {}
    for entry in per_model.values():
        model = configured.get(entry["model"])
        price = cost(entry, model)
        entry["cost"] = price
        currency = model.currency if model is not None and price is not None else None
        entry["currency"] = currency
        entry["cache_hit_rate"] = (
            entry["cached_tokens"] / entry["input_tokens"] if entry["input_tokens"] else None
        )
        if price is not None and currency is not None:
            costs[currency] = costs.get(currency, 0) + price
    return {
        "hours": hours,
        "timezone": "Asia/Shanghai",
        "buckets": buckets,
        "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "end": datetime.fromtimestamp(end_stamp, timezone.utc).isoformat(),
        "totals": {key: sum(bucket[key] for bucket in buckets) for key in fields},
        "models": sorted(per_model.values(), key=lambda row: (-row["total_tokens"], row["model"])),
        "costs": costs,
    }
