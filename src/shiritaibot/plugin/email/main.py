"""Mailbox as an event source: IMAP polling → `email.received` events → proactive loop.

Optionally also an SMTP channel for proactive notifications. Both use stdlib clients in a
worker thread so the shared event loop never blocks.
"""

import asyncio
import imaplib
import json
import re
import smtplib
import ssl
from datetime import date
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    # Optional notification channel; disabled unless smtp_host and notify_to are set.
    smtp_host: str = ""
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Security = "ssl"
    sender: str = ""
    notify_to: list[str] = Field(default_factory=list)
    subject_prefix: str = "[ShiritaiBot]"
    timeout: int = Field(default=30, ge=5, le=120)


def describe(raw: bytes, max_chars: int) -> dict:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    text = ""
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        try:
            text = body.get_content()
        except (LookupError, ValueError):
            text = ""
        if body.get_content_type() == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
    return {
        "message_id": str(message.get("Message-ID", "")),
        "from": str(message.get("From", "")),
        "to": str(message.get("To", "")),
        "subject": str(message.get("Subject", "")) or "(无主题)",
        "date": str(message.get("Date", "")),
        "snippet": " ".join(text.split())[:max_chars],
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
            fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
            raise PublicError(f"邮件插件配置无效：{fields}") from None
        self.state_path = self.context.data_dir / "imap_state.json"
        self.lock = asyncio.Lock()
        if self.settings.smtp_host and self.settings.notify_to:
            self.context.register_notifier(self.notify)
        self.task = asyncio.create_task(self.poll()) if self.settings.poll else None

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def sync_once(self) -> int:
        """Emit one event per new message. The first sync (or a UIDVALIDITY change) only
        records a baseline, so an existing inbox never floods the owner."""
        async with self.lock:
            state = None
            if await asyncio.to_thread(self.state_path.exists):
                state = json.loads(
                    await asyncio.to_thread(self.state_path.read_text, encoding="utf-8")
                )
            password = await self.context.resolve_secret(self.settings.password)
            validity, last, mails = await asyncio.to_thread(self.fetch, state, password)
            for uid, mail in mails:
                await self.context.emit(
                    ExternalEvent(
                        source="email",
                        kind="email.received",
                        dedup_key=mail["message_id"] or f"{validity}:{uid}",
                        title=f"新邮件：{mail['subject']}"[:1000],
                        payload={**mail, "uid": uid, "mailbox": self.settings.mailbox},
                    )
                )
                last = max(last, uid)

            def save():
                temp = self.state_path.with_suffix(".tmp")
                temp.write_text(json.dumps({"uidvalidity": validity, "last_uid": last}), "utf-8")
                temp.replace(self.state_path)

            await asyncio.to_thread(save)
            return len(mails)

    def fetch(self, state: dict | None, password: str) -> tuple[int, int, list]:
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
            client.select(cfg.mailbox, readonly=True)
            validity = int(client.response("UIDVALIDITY")[1][0])
            fresh = state is None or state.get("uidvalidity") != validity
            last = 0 if fresh else int(state["last_uid"])
            # "n:*" always matches the newest message, so filter to strictly newer UIDs.
            _, data = client.uid("SEARCH", f"UID {last + 1}:*")
            uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) > last)
            if fresh:
                return validity, max(uids, default=0), []
            mails = []
            for uid in uids[: cfg.max_per_poll]:
                _, data = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next((part[1] for part in data if isinstance(part, tuple)), b"")
                mails.append((uid, describe(raw, cfg.max_body_chars)))
            return validity, last, mails
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
