"""Images in user messages, rendered per model at request time.

History keeps the adapter's text markers; image-capable models receive the recent images
themselves, others a cached caption from the vision route or a placeholder.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re

import httpx

from shiritaibot.adapters.onebot11 import qq_download_url
from shiritaibot.core import PublicError, canonical
from shiritaibot.core.logs import get_logger

log = get_logger("模型")

IMAGE_REF = re.compile(r"\[QQ 图片引用 ([0-9a-f]{32}): [^\]\n]*\]")
# meme_view results carry this marker; rendering attaches the image or reports an error.
MEME_VIEW = re.compile(r"\[表情图片 meme=([0-9a-f]{16})\]")
NO_VISION = canonical(
    {
        "error": "当前模型不支持图像输入，meme_view 无法查看表情图片；请改用 meme_search 返回的文字描述"
    }
)
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


def view_marker(key: str) -> str:
    return f"[表情图片 meme={key}]"


def first_frame(data: bytes) -> bytes | None:
    """PNG of the first frame; GIF and animated WebP are not accepted by every provider."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            out = io.BytesIO()
            image.convert("RGBA").save(out, "PNG", optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


def model_image(data: bytes) -> str | None:
    """A data URL for model input, or None when the bytes are not a supported image."""
    mime = sniff(data)
    if mime is None:
        return None
    if mime == "image/gif" or (mime == "image/webp" and b"ANIM" in data[:64]):
        frame = first_frame(data)
        if frame is not None:
            data, mime = frame, "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class ImageResolver:
    """One session's image refs for one call; downloads and captions persist across calls."""

    def __init__(self, llm, scope: str, settings):
        self.llm, self.scope, self.cfg = llm, scope, settings
        self.images: dict[str, str | None] = {}
        self.captions: dict[str, str | None] = {}

    @staticmethod
    def present(messages: list[dict]) -> bool:
        return any(
            isinstance(m.get("content"), str)
            and (
                (m["role"] == "user" and IMAGE_REF.search(m["content"]))
                or (m["role"] == "tool" and MEME_VIEW.search(m["content"]))
            )
            for m in messages
        )

    async def meme(self, key: str) -> str | None:
        """A stored meme as a model-ready data URL; memes are global, not session-bound."""
        if key not in self.images:
            try:
                data = await asyncio.to_thread((self.llm.store.home / "memes" / key).read_bytes)
            except OSError:
                data = None
            self.images[key] = await asyncio.to_thread(model_image, data) if data else None
        return self.images[key]

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
        return await asyncio.to_thread(model_image, data)

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
        views = [
            match[1]
            for m in messages
            if m["role"] == "tool" and isinstance(m.get("content"), str)
            for match in MEME_VIEW.finditer(m["content"])
        ]
        limit = self.cfg.vision.max_images
        recent = set(list(dict.fromkeys(reversed(refs)))[:limit]) if native else set()
        recent_views = set(list(dict.fromkeys(reversed(views)))[:limit]) if native else set()
        result: list[dict] = []
        # Viewed memes follow the whole tool-result group as one user message, so
        # providers still see every tool result right after its call.
        attached: list[dict] = []

        def flush() -> None:
            if attached:
                result.append({"role": "user", "content": list(attached)})
                attached.clear()

        for message in messages:
            content = message.get("content")
            if message["role"] != "tool":
                flush()
            if message["role"] == "tool" and isinstance(content, str) and MEME_VIEW.search(content):
                if not native:
                    result.append({**message, "content": NO_VISION})
                    continue
                for key in dict.fromkeys(MEME_VIEW.findall(content)):
                    url = await self.meme(key) if key in recent_views else None
                    if url:
                        note = f"[表情图片 meme={key}，图片附在随后的消息中]"
                        attached += [
                            {"type": "text", "text": f"[meme_view 查看的表情 meme={key}]"},
                            image_part(url),
                        ]
                    else:
                        note = f"[表情图片 meme={key}，图片已不再附带]"
                    content = content.replace(view_marker(key), note)
                result.append({**message, "content": content})
                continue
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
        flush()
        return result
