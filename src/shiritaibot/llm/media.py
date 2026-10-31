"""Images in user messages, rendered per model at request time.

History keeps the adapter's text markers; image-capable models receive the recent images
themselves, others a cached caption from the vision route or a placeholder.
"""

from __future__ import annotations

import asyncio
import base64
import re

import httpx

from shiritaibot.adapters.onebot11 import qq_download_url
from shiritaibot.core import PublicError
from shiritaibot.core.logs import get_logger

log = get_logger("模型")

IMAGE_REF = re.compile(r"\[QQ 图片引用 ([0-9a-f]{32}): [^\]\n]*\]")
# Base64 of this stays within the strictest provider per-image limit (5 MB).
MAX_IMAGE_BYTES = 3_750_000
CAPTION_PROMPT = (
    "你是图像转述器，为看不到图片的模型描述图片。用中文客观描述图片内容："
    "主体、场景、人物动作与表情、关键细节；图中文字按原文转录。"
    "不要推测图片之外的事，不超过 300 字，只输出描述。"
)


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_part(url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url}}


class ImageResolver:
    """One session's image refs for one call; downloads and captions persist across calls."""

    def __init__(self, llm, scope: str, settings):
        self.llm, self.scope, self.cfg = llm, scope, settings
        self.images: dict[str, str | None] = {}
        self.captions: dict[str, str | None] = {}

    @staticmethod
    def present(messages: list[dict]) -> bool:
        return any(
            m["role"] == "user"
            and isinstance(m.get("content"), str)
            and IMAGE_REF.search(m["content"])
            for m in messages
        )

    async def image(self, ref: str) -> str | None:
        """A data URL, or None when the ref is foreign, expired or not an image."""
        if ref not in self.images:
            self.images[ref] = await self._load(ref)
        return self.images[ref]

    async def _load(self, ref: str) -> str | None:
        # Refs stay bound to their session even after the bytes are cached.
        row = await self.llm.store.get("qq_file", ref)
        if not row or row["scope"] != self.scope:
            return None
        path = self.llm.store.home / "media" / ref
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            data = await self._download(row["data"].get("url", ""))
            if data is None:
                return None
            # QQ CDN links expire, so later turns read the local copy.
            temp = path.with_suffix(".part")
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(temp.write_bytes, data)
            await asyncio.to_thread(temp.replace, path)
        mime = sniff(data)
        return f"data:{mime};base64,{base64.b64encode(data).decode()}" if mime else None

    async def _download(self, url: str) -> bytes | None:
        if not qq_download_url(url):
            return None
        chunks, size = [], 0
        try:
            async with self.llm.client.stream("GET", url, timeout=30) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(65536):
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        log.warning("图片超过大小上限，不发送给模型")
                        return None
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            log.warning(f"图片下载失败（{type(exc).__name__}）")
            return None
        data = b"".join(chunks)
        return data if sniff(data) else None

    async def caption(self, ref: str) -> str | None:
        if not self.cfg.vision.caption or not self.cfg.routes.get("vision"):
            return None
        if ref not in self.captions:
            self.captions[ref] = await self._caption(ref)
        return self.captions[ref]

    async def _caption(self, ref: str) -> str | None:
        cached = await self.llm.store.get("image_caption", ref)
        if cached and cached["scope"] == self.scope:
            return cached["text"]
        url = await self.image(ref)
        if url is None:
            return None
        try:
            reply = await self.llm.complete(
                "vision",
                CAPTION_PROMPT,
                [
                    {
                        "role": "user",
                        "content": [image_part(url), {"type": "text", "text": "请描述这张图片。"}],
                    }
                ],
                [],
                settings=self.cfg,
            )
        except PublicError as exc:
            log.warning(f"图像转述失败：{exc}", "vision")
            return None
        text = " ".join(reply.text.split())[:1000]
        if text:
            await self.llm.store.put("image_caption", ref, {"text": text}, self.scope)
        return text or None

    async def render(self, messages: list[dict], model) -> list[dict]:
        native = "image" in model.modalities
        refs = [
            match[1]
            for m in messages
            if m["role"] == "user" and isinstance(m.get("content"), str)
            for match in IMAGE_REF.finditer(m["content"])
        ]
        recent = (
            set(list(dict.fromkeys(reversed(refs)))[: self.cfg.vision.max_images])
            if native
            else set()
        )
        result = []
        for message in messages:
            content = message.get("content")
            if (
                message["role"] != "user"
                or not isinstance(content, str)
                or not IMAGE_REF.search(content)
            ):
                result.append(message)
                continue
            parts, text, end = [], "", 0
            for match in IMAGE_REF.finditer(content):
                ref = match[1]
                text += content[end : match.start()]
                end = match.end()
                url = await self.image(ref) if ref in recent else None
                if url:
                    parts += [{"type": "text", "text": text + f"[图片 ref={ref}]"}, image_part(url)]
                    text = ""
                    continue
                caption = await self.caption(ref)
                text += f"[图片 ref={ref}：{caption}]" if caption else f"[图片 ref={ref}]"
            text += content[end:]
            if not parts:
                result.append({**message, "content": text})
                continue
            if text.strip():
                parts.append({"type": "text", "text": text})
            result.append({**message, "content": parts})
        return result
