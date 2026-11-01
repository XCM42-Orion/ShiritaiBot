"""Meme hooker: collects stickers other members send in group chats (docs/MEMES.md §2).

Each sticker message is taken with probability collect_rate × (1 − stored / capacity),
so collection slows as the library fills and stops at capacity. A taken sticker is
described by the meme route, judged against the default persona and categorized by
the decision model (text models as fallback), then stored by the data core.
"""

from __future__ import annotations

import asyncio
import random
from collections import OrderedDict

from shiritaibot.adapters.onebot11 import qq_download_url
from shiritaibot.core import PublicError
from shiritaibot.core.logs import brief, describe_session, get_logger
from shiritaibot.meme.core import MemeCore, MemeRejected, download, meme_id

log = get_logger("表情")
REJECTED_KEPT = 2000


def sticker_url(part: dict, stickers_only: bool) -> str | None:
    """The CDN link of a sticker segment (NapCat: image sub_type 1, or mface)."""
    kind = part.get("type")
    data = part.get("data") or {}
    if kind == "mface":
        accepted = True
    elif kind == "image":
        sub_type = str(data.get("sub_type", data.get("subType", 0)) or 0)
        accepted = not stickers_only or sub_type != "0" or "表情" in str(data.get("summary", ""))
    else:
        return None
    url = str(data.get("url") or "")
    return url if accepted and qq_download_url(url) else None


class MemeHooker:
    def __init__(self, core: MemeCore, config):
        self.core, self.config = core, config
        self.task: asyncio.Task | None = None
        # Stickers judged unsuitable are not described or judged again.
        self.rejected: OrderedDict[str, None] = OrderedDict()

    def probability(self, cfg) -> float:
        fill = self.core.count / cfg.meme.capacity
        return cfg.meme.collect_rate * max(0.0, 1.0 - fill)

    def observe(self, session: str, parts: list[dict]) -> None:
        """Called for every whitelisted group message from someone else; never blocks."""
        cfg = self.config.value
        if not (cfg.meme.enabled and cfg.meme.collect) or not session.startswith("group:"):
            return
        if self.task is not None and not self.task.done():
            return
        urls = [url for part in parts if (url := sticker_url(part, cfg.meme.stickers_only))]
        if not urls or random.random() >= self.probability(cfg):
            return
        self.task = asyncio.create_task(self.collect(session, urls[0]))

    async def collect(self, session: str, url: str) -> dict | None:
        where = describe_session(session)
        data = await download(self.core.llm.client, url)
        if data is None:
            return None
        key = meme_id(data)
        if key in self.core.items or key in self.rejected:
            return None
        try:
            row = await self.core.ingest(
                data,
                source="group",
                origin=session,
                persona_check=self.config.value.meme.persona_check,
                enforce_capacity=True,
            )
        except MemeRejected as exc:
            self.rejected[key] = None
            while len(self.rejected) > REJECTED_KEPT:
                self.rejected.popitem(last=False)
            log.debug(f"未收录表情 {key}：{exc}", where)
            return None
        except PublicError as exc:
            log.warning(f"表情收录失败：{exc}", where)
            return None
        except Exception as exc:
            log.error(f"表情收录出错（{type(exc).__name__}）", where, exc=exc)
            return None
        log.log(
            f"收录表情 {row['id']}（{row['category']}）：{brief(row['description'], 60)}", where
        )
        return row
