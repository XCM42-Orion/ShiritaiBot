"""Mailbox as an event source: IMAP polling → `email.received` events → proactive loop.

Optionally also an SMTP channel for proactive notifications. Both use stdlib clients in a
worker thread so the shared event loop never blocks.
"""

import asyncio
import hashlib
import html
import imaplib
import json
import re
import smtplib
import ssl
from datetime import UTC, date, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid
from html.parser import HTMLParser
from time import mktime
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from shiritaibot.plugin_api import ExternalEvent, Plugin, PublicError

Security = Literal["ssl", "starttls", "none"]


class EmailSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    imap_host: str = Field(min_length=1)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_security: Security = "ssl"
    username: str = Field(min_length=1)
    password: str = ""
    mailbox: str = Field(default="INBOX", pattern=r"^[^\s\"\\]+$")
    poll: bool = True
    poll_seconds: int = Field(default=300, ge=60, le=86400)
    max_per_poll: int = Field(default=20, ge=1, le=200)
    max_body_chars: int = Field(default=2000, ge=200, le=20000)
    time_filter: Literal["startup", "last_poll", "none", "custom"] = "startup"
    filter_before: AwareDatetime | None = None
    # Optional notification channel; disabled unless smtp_host and notify_to are set.
    smtp_host: str = ""
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Security = "ssl"
    sender: str = ""
    notify_to: list[str] = Field(default_factory=list)
    subject_prefix: str = "[ShiritaiBot]"
    timeout: int = Field(default=30, ge=5, le=120)

    @field_validator("filter_before", mode="before")
    @classmethod
    def empty_filter_before(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def require_filter_before(self):
        if self.time_filter == "custom" and self.filter_before is None:
            raise ValueError("filter_before is required for custom time_filter")
        return self


class _TextExtractor(HTMLParser):
    """Collect human-readable body text, dropping styles, scripts and other markup."""

    SKIP = {"head", "style", "script", "noscript", "template", "svg", "title", "xml"}
    BLOCK = {
        "address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre",
        "section", "table", "tbody", "td", "th", "thead", "tr", "ul",
    }  # fmt: skip

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag == "img" and not self.skipping:
            alt = (dict(attrs).get("alt") or "").strip()
            if alt:
                self.parts.append(f" {alt} ")

    def handle_startendtag(self, tag, attrs):
        if tag not in self.SKIP:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


_INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufeff\u00ad\u034f]")
_LOOKS_HTML = re.compile(r"<\s*(html|body|div|table|p|br|style|span)\b", re.I)


def html_to_text(markup: str) -> str:
    """Strip markup, inline CSS and scripts; keep the readable body with line breaks."""
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return clean_text(text)


def clean_text(text: str) -> str:
    text = _INVISIBLE.sub("", text).replace("\xa0", " ")
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def describe(raw: bytes, max_chars: int) -> dict:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    text = ""
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        try:
            text = body.get_content()
        except (LookupError, ValueError):
            text = ""
        # Some senders put HTML in their text/plain part as well.
        if body.get_content_type() == "text/html" or _LOOKS_HTML.search(text):
            text = html_to_text(text)
        else:
            text = clean_text(text)
    return {
        "message_id": str(message.get("Message-ID", "")),
        "from": str(message.get("From", "")),
        "to": str(message.get("To", "")),
        "subject": str(message.get("Subject", "")) or "(无主题)",
        "date": str(message.get("Date", "")),
        "snippet": text[:max_chars],
        "attachments": [p.get_filename() for p in message.iter_attachments() if p.get_filename()][
            :20
        ],
    }


class Entry(Plugin):
    def __init__(self, context):
        super().__init__(context)
        self.task = None

    async def start(self):
        try:
            self.settings = EmailSettings.model_validate(self.context.settings)
        except ValidationError as exc:
            # Field names only: the rejected input may contain credentials.
            fields = ", ".join(
                ".".join(map(str, e["loc"])) or "time_filter / filter_before" for e in exc.errors()
            )
            raise PublicError(f"邮件插件配置无效：{fields}") from None
        self.state_path = self.context.data_dir / "imap_state.json"
        self.started_at = datetime.now(UTC)
        self.lock = asyncio.Lock()
        if self.settings.smtp_host and self.settings.notify_to:
            self.context.register_notifier(self.notify)
        self.task = asyncio.create_task(self.poll()) if self.settings.poll else None

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def sync_once(self) -> int:
        """Emit at most one event per poll, then commit the UID and time cursors."""
        async with self.lock:
            state = None
            if await asyncio.to_thread(self.state_path.exists):
                state = json.loads(
                    await asyncio.to_thread(self.state_path.read_text, encoding="utf-8")
                )
            password = await self.context.resolve_secret(self.settings.password)
            polled_at = datetime.now(UTC)
            cutoff = self.cutoff(state)
            validity, last, mails, more = await asyncio.to_thread(
                self.fetch, state, password, cutoff
            )
            if mails:
                identities = [mail["message_id"] or str(uid) for uid, mail in mails]
                batch = json.dumps([self.settings.mailbox, validity, identities])
                await self.context.emit(
                    ExternalEvent(
                        source="email",
                        kind="email.received",
                        dedup_key="batch:" + hashlib.sha256(batch.encode()).hexdigest(),
                        title=(
                            f"新邮件：{mails[0][1]['subject']}"
                            if len(mails) == 1
                            else f"收到 {len(mails)} 封新邮件："
                            + "、".join(mail["subject"] for _, mail in mails)
                        )[:1000],
                        payload={
                            "mailbox": self.settings.mailbox,
                            "count": len(mails),
                            "messages": [{**mail, "uid": uid} for uid, mail in mails],
                        },
                    )
                )

            next_state = {
                "uidvalidity": validity,
                "last_uid": last,
                "last_polled_at": polled_at.isoformat(),
            }
            # Keep the same window until all pages have been read. Advancing the
            # last-poll cutoff early would silently drop the remaining messages.
            if more and cutoff is not None and self.settings.time_filter == "last_poll":
                next_state["pending_filter_before"] = cutoff.isoformat()

            def save():
                temp = self.state_path.with_suffix(".tmp")
                temp.write_text(json.dumps(next_state), "utf-8")
                temp.replace(self.state_path)

            await asyncio.to_thread(save)
            return len(mails)

    def cutoff(self, state: dict | None) -> datetime | None:
        mode = self.settings.time_filter
        if mode == "none":
            return None
        if mode == "custom":
            return self.settings.filter_before
        if mode == "last_poll" and state:
            previous = state.get("pending_filter_before") or state.get("last_polled_at")
            if previous:
                return datetime.fromisoformat(previous)
        return self.started_at

    def fetch(
        self, state: dict | None, password: str, cutoff: datetime | None
    ) -> tuple[int, int, list, bool]:
        cfg = self.settings
        context = ssl.create_default_context()
        if cfg.imap_security == "ssl":
            client = imaplib.IMAP4_SSL(
                cfg.imap_host, cfg.imap_port, ssl_context=context, timeout=cfg.timeout
            )
        else:
            client = imaplib.IMAP4(cfg.imap_host, cfg.imap_port, timeout=cfg.timeout)
        try:
            if cfg.imap_security == "starttls":
                client.starttls(context)
            client.login(cfg.username, password)
            status, _ = client.select(cfg.mailbox, readonly=True)
            if status != "OK":
                raise imaplib.IMAP4.error("mailbox selection failed")
            validity = int(client.response("UIDVALIDITY")[1][0])
            fresh = state is None or state.get("uidvalidity") != validity
            last = 0 if fresh else int(state["last_uid"])
            # "n:*" always matches the newest message, so filter to strictly newer UIDs.
            query = f"UID {last + 1}:*"
            if cutoff is not None:
                # IMAP SINCE only has day precision and ignores offsets. Include
                # the preceding UTC day, then compare exact INTERNALDATE below.
                day = cutoff.astimezone(UTC) - timedelta(days=1)
                month = (
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                    "Oct",
                    "Nov",
                    "Dec",
                )[day.month - 1]
                query += f" SINCE {day.day:02d}-{month}-{day.year}"
            status, data = client.uid("SEARCH", query)
            if status != "OK":
                raise imaplib.IMAP4.error("mailbox search failed")
            uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) > last)
            mails = []
            for uid in uids[: cfg.max_per_poll]:
                status, data = client.uid("FETCH", str(uid), "(INTERNALDATE BODY.PEEK[])")
                part = next((part for part in data if isinstance(part, tuple)), None)
                if status != "OK" or part is None:
                    raise imaplib.IMAP4.error("message fetch failed")
                received = imaplib.Internaldate2tuple(part[0])
                if cutoff is not None and received is None:
                    raise imaplib.IMAP4.error("message INTERNALDATE missing")
                received_at = datetime.fromtimestamp(mktime(received), UTC) if received else None
                if cutoff is None or (received_at is not None and received_at >= cutoff):
                    mail = describe(part[1], cfg.max_body_chars)
                    mail["received_at"] = received_at.isoformat() if received_at else None
                    mails.append((uid, mail))
                last = uid
            return validity, last, mails, len(uids) > cfg.max_per_poll
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    async def poll(self):
        while True:
            try:
                await self.sync_once()
            except Exception as exc:
                await self.context.emit(
                    ExternalEvent(
                        source="email",
                        kind="email.sync.failed",
                        dedup_key=f"sync:{type(exc).__name__}:{date.today().isoformat()}",
                        title="邮箱收件失败",
                        payload={"error": type(exc).__name__},
                    )
                )
            await asyncio.sleep(self.settings.poll_seconds)

    async def notify(self, source: str, text: str) -> None:
        cfg = self.settings
        password = await self.context.resolve_secret(cfg.password)
        message = EmailMessage()
        headline = text.strip().splitlines()[0] if text.strip() else source
        message["Subject"] = f"{cfg.subject_prefix} {headline[:80]}".strip()
        message["From"] = cfg.sender or cfg.username
        message["To"] = ", ".join(cfg.notify_to)
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=cfg.smtp_host)
        message.set_content(f"{text}\n\n-- \n来源：{source}\n")
        await asyncio.to_thread(self.deliver, message, password)

    def deliver(self, message: EmailMessage, password: str) -> None:
        cfg = self.settings
        context = ssl.create_default_context()
        if cfg.smtp_security == "ssl":
            client = smtplib.SMTP_SSL(
                cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout, context=context
            )
        else:
            client = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout)
        with client:
            if cfg.smtp_security == "starttls":
                client.starttls(context=context)
            client.login(cfg.username, password)
            client.send_message(message)
