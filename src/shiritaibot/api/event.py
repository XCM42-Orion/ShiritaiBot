"""Objects passed to plugin handlers and hooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from shiritaibot.core import Context


@dataclass
class MessageResult:
    """A reply produced by a message handler (``yield`` it, return it, or ``event.send`` it)."""

    text: str


@dataclass(eq=False)
class MessageEvent:
    """An incoming message from QQ or the WebUI (AstrBot's ``AstrMessageEvent``).

    ``text`` is the plain text the user typed; file, image and reply references the model
    receives are not part of it. Edits to the fields do not change what the model sees —
    use ``on_llm_request`` for that.
    """

    session: str  # Unified session id, e.g. ``private:qq:12345``; pass to send_message.
    text: str
    sender_id: str
    sender_name: str = ""
    platform: str = "qq"  # "qq" | "web"
    adapter: str = ""  # Adapter instance id for QQ messages.
    group_id: str = ""
    message_id: str = ""
    is_owner: bool = False
    # False for group chatter that neither mentions the bot nor contains a wake keyword.
    is_wake: bool = True
    segments: list[dict] = field(default_factory=list)  # OneBot message segments.
    raw: dict = field(default_factory=dict)
    _sender: Callable[[str], Awaitable[None]] | None = field(default=None, repr=False)
    _stopped: bool = field(default=False, repr=False)

    @property
    def is_group(self) -> bool:
        return bool(self.group_id)

    @property
    def is_private(self) -> bool:
        return not self.group_id

    def get_sender_name(self) -> str:
        return self.sender_name or self.sender_id

    def plain_result(self, text: str) -> MessageResult:
        return MessageResult(str(text))

    async def send(self, result: MessageResult | str) -> None:
        text = result.text if isinstance(result, MessageResult) else str(result)
        if self._sender is None:
            raise RuntimeError("this event cannot send replies")
        if text.strip():
            await self._sender(text)

    def stop_event(self) -> None:
        """Stop later plugin handlers and keep the message from reaching the model."""
        self._stopped = True

    @property
    def is_stopped(self) -> bool:
        return self._stopped


@dataclass(eq=False)
class OutgoingMessage:
    """Text about to be sent to a session (replies, command output, notifications)."""

    session: str
    text: str
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass(frozen=True)
class Turn:
    """One agent turn: a Main conversation turn or a Proactive Loop."""

    id: str
    session: str
    actor: str
    owner: bool
    group: bool
    background: bool
    mode: str  # "chat" | "agent"
    route: str  # "chat" | "agent" | "agent_chat"
    text: str  # The user input (or the Proactive prompt) that started the turn.
    context: Context = field(repr=False, compare=False)

    @property
    def proactive(self) -> bool:
        return self.session.startswith("background:proactive:")

    @property
    def event_id(self) -> str:
        """Receipt id of the proactive event being handled; empty for other turns."""
        return self.session.removeprefix("background:proactive:") if self.proactive else ""


@dataclass(eq=False)
class LLMRequest:
    """One model request; edits apply to this request only and are never stored."""

    system_prompt: str
    messages: list[dict]
    tools: list[dict]
    route: str
    step: int

    def add_context(self, text: str) -> None:
        """Append text to the latest user message of this request."""
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = [*content, {"type": "text", "text": text}]
            else:
                content = f"{content or ''}\n{text}"
            self.messages[index] = {**message, "content": content}
            return
        self.messages.append({"role": "user", "content": text})


@dataclass(eq=False)
class LLMResponse:
    """The model's reply; ``text`` may be rewritten before it is stored and delivered."""

    text: str
    tool_calls: tuple[dict, ...] = ()


@dataclass(frozen=True)
class AgentResult:
    status: str  # "completed" | "failed" | "cancelled"
    steps: int
    text: str  # Final reply (or error message) delivered to the session.


@dataclass(eq=False)
class ProactiveEvent:
    """A proactive event as it starts processing.

    ``title``, ``payload`` and ``urgency`` may be edited in ``on_proactive_event``; the
    rest is read-only. ``protected`` events (owner reminders, scheduled jobs, results of
    delegated subagent runs) cannot be dropped.
    """

    id: str  # Receipt id, as returned by Context.emit.
    source: str
    kind: str
    title: str
    payload: dict[str, Any]
    urgency: str
    occurred_at: str
    actors: list[dict]
    protected: bool = False
    dropped: bool = False
    drop_reason: str = ""

    def drop(self, reason: str = "") -> None:
        """Discard the event before triage (ignored for protected events)."""
        self.dropped = True
        self.drop_reason = reason


@dataclass(eq=False)
class Triage:
    """The importance filter's decision; edits are validated, invalid ones are ignored.

    importance 0 discards the event; 1–4 start a Proactive Loop (4 must notify).
    Owner-scheduled and delegated events keep their host-enforced minimum.
    """

    importance: int
    decision: str  # "notify" | "digest" | "log" | "ignore"
    message: str
    reason: str
    prepare: str


@dataclass(eq=False)
class ProactiveOutcome:
    """The Proactive Loop's conclusion just before the notification is queued.

    Setting ``notify`` to False cancels the notification unless ``forced`` (importance 4 or
    an owner reminder), in which case only ``message`` can change.
    """

    notify: bool
    message: str
    reason: str
    importance: int
    failed: bool
    forced: bool
