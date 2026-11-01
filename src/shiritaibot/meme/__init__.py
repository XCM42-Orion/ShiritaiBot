"""Meme manager (docs/MEMES.md): data core, group hooker and injectors.

Submodules are imported explicitly (shiritaibot.meme.core and friends) so that the
adapter and agent layers can depend on this package without import cycles.
"""

from __future__ import annotations


class MemeManager:
    """The three parts wired together; the Application owns one instance."""

    def __init__(self, store, config, llm):
        from shiritaibot.meme.core import MemeCore
        from shiritaibot.meme.hooker import MemeHooker
        from shiritaibot.meme.inject import MemeInjector

        self.core = MemeCore(store, config, llm)
        self.hooker = MemeHooker(self.core, config)
        self.injector = MemeInjector(self.core, config)

    async def open(self) -> None:
        await self.core.open()

    async def run(self) -> None:
        await self.core.run()
