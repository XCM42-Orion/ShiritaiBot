"""SMTP channel for proactive notifications. Uses stdlib smtplib in a worker thread."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shiritaibot.plugin_api import Plugin, PublicError


class EmailSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    port: int = Field(default=465, ge=1, le=65535)
    security: Literal["ssl", "starttls", "none"] = "ssl"
    username: str = ""
    password: str = ""
    sender: str = ""
    to: list[str] = Field(min_length=1)
    subject_prefix: str = "[ShiritaiBot]"
    timeout: int = Field(default=30, ge=5, le=120)


class Entry(Plugin):
    async def start(self):
        try:
            self.settings = EmailSettings.model_validate(self.context.settings)
        except ValidationError as exc:
            # Field names only: the rejected input may contain credentials.
            fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
            raise PublicError(f"邮件插件配置无效：{fields}") from None
        self.context.register_notifier(self.notify)

    async def notify(self, source: str, text: str) -> None:
        cfg = self.settings
        password = await self.context.resolve_secret(cfg.password)
        message = EmailMessage()
        headline = text.strip().splitlines()[0] if text.strip() else source
        message["Subject"] = f"{cfg.subject_prefix} {headline[:80]}".strip()
        message["From"] = cfg.sender or cfg.username
        message["To"] = ", ".join(cfg.to)
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=cfg.host)
        message.set_content(f"{text}\n\n-- \n来源：{source}\n")
        await asyncio.to_thread(self.deliver, message, password)

    def deliver(self, message: EmailMessage, password: str) -> None:
        cfg = self.settings
        context = ssl.create_default_context()
        if cfg.security == "ssl":
            client = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=cfg.timeout, context=context)
        else:
            client = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)
        with client:
            if cfg.security == "starttls":
                client.starttls(context=context)
            if cfg.username:
                client.login(cfg.username, password)
            client.send_message(message)
