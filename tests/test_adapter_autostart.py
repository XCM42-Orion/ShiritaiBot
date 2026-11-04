import asyncio

from shiritaibot.adapters import onebot11
from shiritaibot.adapters.onebot11 import OneBot
from shiritaibot.config import AdapterConfig


class Store:
    def __init__(self):
        self.events = []

    async def activity(self, kind, detail):
        self.events.append((kind, detail))


class Vault:
    async def resolve(self, ref):
        return ""


def make_bot(auto_start):
    return OneBot(
        AdapterConfig(id="qq", owner_id="1", self_id="100", auto_start=auto_start),
        Vault(),
        Store(),
        sink=None,
    )


async def test_auto_start_waits_for_streak_and_rate_limits(monkeypatch):
    bot = make_bot(["launcher.bat", "--headless"])
    bot.config.auto_start_after = 30
    clock = [100.0]
    launched = []

    async def fake_exec(*command, **kwargs):
        launched.append(command)
        return object()

    monkeypatch.setattr(onebot11.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await bot.try_auto_start()
    assert launched == [] and bot.failed_since == 100.0
    clock[0] = 129.9
    await bot.try_auto_start()
    assert launched == []
    clock[0] = 130.0
    await bot.try_auto_start()
    assert launched == [("launcher.bat", "--headless")]
    clock[0] = 300.0
    await bot.try_auto_start()
    assert len(launched) == 1
    clock[0] = 430.0
    await bot.try_auto_start()
    assert len(launched) == 2


async def test_auto_start_is_off_by_default_and_reports_failures(monkeypatch):
    bot = make_bot([])
    clock = [0.0]
    monkeypatch.setattr(onebot11.time, "monotonic", lambda: clock[0])

    async def fake_exec(*command, **kwargs):
        raise FileNotFoundError(command[0])

    await bot.try_auto_start()
    assert bot.failed_since is None

    failing = make_bot(["missing.exe"])
    failing.config.auto_start_after = 5
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await failing.try_auto_start()
    clock[0] = 10.0
    await failing.try_auto_start()
    assert any(kind == "adapter.autostart_failed" for kind, _ in failing.store.events)
