from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


def now() -> datetime:
    return datetime.now(UTC)


def uid() -> str:
    return uuid.uuid4().hex


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExternalEvent(Model):
    source: str
    kind: str
    dedup_key: str
    title: str = Field(max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    urgency: Literal["low", "normal", "high", "critical"] = "normal"
    occurred_at: AwareDatetime = Field(default_factory=now)


@dataclass(frozen=True, slots=True)
class Context:
    session: str
    actor: str
    mode: str = "chat"
    owner: bool = False
    group: bool = False
    background: bool = False
    turn: str = ""
    allowed_tools: tuple[str, ...] | None = None


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[Context, dict[str, Any]], Awaitable[Any]]
    risk: str = "safe"
    chat: bool = False
    timeout: float = 60
    plugin: str = "builtin"

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }


@dataclass(slots=True)
class Reply:
    text: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    opaque: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    async def complete(
        self, route: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Reply: ...


class RuntimePort(Protocol):
    async def submit(self, ctx: Context, text: str) -> str: ...


class ApprovalPort(Protocol):
    async def resolve(self, key: str, allow: bool) -> None: ...


class EventBus:
    """Bounded UI fanout. Slow subscribers lose old activity, never block agents."""

    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()

    def emit(self, event: dict[str, Any]) -> None:
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


class PublicError(Exception):
    """A deliberately sanitized error suitable for the owner or model."""
