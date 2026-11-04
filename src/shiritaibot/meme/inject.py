"""Meme injectors (docs/MEMES.md §3).

- prompt(): usage instructions appended to the system prompt of QQ sessions. It lists
  only configured categories, never library contents, so it stays byte-stable and
  keeps provider prompt caches warm while memes are collected.
- split(): runs between the send function and the protocol side. <meme>tag</meme>
  markers become separate sticker messages; a marker inside a sentence splits one
  reply into text + sticker + text.
"""

from __future__ import annotations

import re

# Chat models sometimes drift on the tag name ("<mememe>"), so accept any run of
# "me" and let the opening and closing tag differ; a stray tag is stripped below.
MARKER = re.compile(r"<(?:me){2,}>\s*([A-Za-z0-9_]{1,40})\s*</(?:me){2,}>", re.IGNORECASE)
STRAY = re.compile(r"</?(?:me){2,}>", re.IGNORECASE)


def meme_prompt(cfg) -> str:
    meme = cfg.meme
    if not (meme.enabled and meme.inject):
        return ""
    categories = "\n".join(
        f"- {c.name}：{c.description}" if c.description else f"- {c.name}" for c in meme.categories
    )
    return (
        "\n<memes>\n你可以在 QQ 回复里发表情包：在想发表情的位置写 <meme>分类</meme>，"
        "会随机发出该分类的一张表情；写 <meme>表情ID</meme> 发送指定表情（ID 来自 meme_search）。"
        "表情作为单独的消息发出，标记写在句子中间时，回复会在该处拆成前后两条消息。\n"
        f"可用分类：\n{categories}\n"
        f"每条回复最多 {meme.max_per_reply} 个表情，多余的会被忽略；某分类暂时没有表情时标记也会被忽略。"
        "标签必须逐字写 <meme>…</meme>，不要写成 <mememe>、<meme >、<表情> 等变体，"
        "写错的表情标记不会被发送。"
        "表情是点缀，贴合语气时再用，不要每句都发，也不要在标记前后解释表情内容。"
        "想找贴合语境的具体表情可调用 meme_search，看图确认可调用 meme_view；"
        "对方发来的图片适合做表情时，可用 meme_save 收藏。\n</memes>"
    )


class MemeInjector:
    def __init__(self, core, config):
        self.core, self.config = core, config

    def prompt(self, cfg=None) -> str:
        return meme_prompt(cfg or self.config.value)

    def split(self, text: str) -> list[str | dict]:
        """Text pieces and meme records in order; unknown or excess markers are dropped."""
        cfg = self.config.value.meme
        if not cfg.enabled or not STRAY.search(text):
            return [text]
        pieces: list[str | dict] = []
        buffer, end, used = "", 0, 0
        for match in MARKER.finditer(text):
            buffer += text[end : match.start()]
            end = match.end()
            row = self.core.resolve(match[1]) if used < cfg.max_per_reply else None
            if row is None:
                continue
            used += 1
            if buffer.strip():
                pieces.append(STRAY.sub("", buffer).strip())
            pieces.append(row)
            buffer = ""
        buffer += text[end:]
        if buffer.strip():
            pieces.append(STRAY.sub("", buffer).strip())
        return [piece for piece in pieces if piece]

    @staticmethod
    def label(row: dict) -> str:
        """Readable stand-in for a sent sticker in group context and WebUI replies."""
        description = row["description"]
        return f"[表情：{description[:40] + '…' if len(description) > 40 else description}]"

    def plain(self, text: str) -> str:
        """Markers rendered as readable labels, for surfaces that cannot show stickers."""
        return "\n".join(
            piece if isinstance(piece, str) else self.label(piece) for piece in self.split(text)
        )
