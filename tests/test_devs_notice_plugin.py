import asyncio

import httpx
import pytest
from test_permissions import eventually
from test_proactive import Agent, Judge, Summarizer

from shiritaibot.config import PluginConfig

PLUGIN = "srtb_plugin_devs_notice"


def notice(nid, seq, revision=1, category="release", level="info", deleted=False):
    if deleted:
        return {"id": nid, "seq": seq, "revision": revision, "deleted": True}
    return {
        "id": nid,
        "seq": seq,
        "revision": revision,
        "deleted": False,
        "title": f"公告 {nid}",
        "body": f"正文 {nid}",
        "level": level,
        "category": category,
        "link": "",
        "author": "dev",
        "created_at": "2026-11-03T10:00:00+00:00",
        "updated_at": "2026-11-03T10:00:00+00:00",
    }


class FakeService:
    def __init__(self):
        self.notices: list[dict] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        after = int(request.url.params["after"])
        limit = int(request.url.params["limit"])
        latest = max((n["seq"] for n in self.notices), default=0)
        rows = sorted((n for n in self.notices if n["seq"] > after), key=lambda n: n["seq"])
        rows = rows[:limit]
        cursor = rows[-1]["seq"] if rows else after
        return httpx.Response(
            200,
            json={"latest": latest, "cursor": cursor, "more": cursor < latest, "notices": rows},
        )


@pytest.fixture
async def devs(app):
    service = FakeService()
    app.config.value.plugins = [PluginConfig(id=PLUGIN, enabled=True, settings={"poll": False})]
    await app.plugins.reload(PLUGIN)
    plugin = app.plugins.loaded[PLUGIN]
    plugin.transport = httpx.MockTransport(service.handler)
    return app, plugin, service


async def test_first_poll_sets_baseline_then_emits_new_notices(devs):
    app, plugin, service = devs
    service.notices = [notice(1, 1), notice(2, 2)]
    assert await plugin.sync_once() == 0
    assert await app.store.list("event") == []
    assert str(service.requests[0].url).startswith("https://notice.shiritaibot.dev/api/notices")

    service.notices.append(notice(3, 3, category="security", level="critical"))
    assert await plugin.sync_once() == 1
    assert await plugin.sync_once() == 0
    [event] = [e["event"] for e in await app.store.list("event")]
    assert event["source"] == "devs_notice" and event["kind"] == "devs_notice.published"
    assert event["urgency"] == "critical"
    assert event["payload"]["notice"]["title"] == "公告 3"
    assert event["dedup_key"] == "notice:3:r1"


async def test_categories_updates_and_deletes_are_filtered(devs):
    app, plugin, service = devs
    plugin.settings.categories = ["release", "security"]
    await plugin.sync_once()
    service.notices = [
        notice(1, 1, category="plugin"),
        notice(2, 2),
        notice(3, 3, deleted=True),
        notice(4, 5, revision=2),
    ]
    assert await plugin.sync_once() == 2
    kinds = sorted(e["event"]["kind"] for e in await app.store.list("event"))
    assert kinds == ["devs_notice.published", "devs_notice.updated"]

    plugin.settings.notify_updates = False
    service.notices.append(notice(2, 6, revision=2))
    assert await plugin.sync_once() == 0
    assert await plugin.get_kv_data("cursor") == 6


async def test_pages_through_large_backlog(devs):
    app, plugin, service = devs
    await plugin.sync_once()
    service.notices = [notice(i, i) for i in range(1, 121)]
    assert await plugin.sync_once() == 120
    assert await plugin.get_kv_data("cursor") == 120


async def test_settings_require_https_and_minimum_interval(app):
    for settings in (
        {"url": "http://notice.shiritaibot.dev/api"},
        {"poll_seconds": 599},
        {"categories": ["spam"]},
    ):
        app.config.value.plugins = [
            PluginConfig(id=PLUGIN, enabled=True, settings={"poll": False, **settings})
        ]
        with pytest.raises(Exception):
            await app.plugins.reload(PLUGIN)
        assert PLUGIN not in app.plugins.loaded


async def test_notice_reaches_proactive_loop_even_if_triage_discards(devs):
    app, plugin, service = devs
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)
    app.config.value.proactive.enabled = True
    app.proactive.llm = Judge(importance=0, decision="ignore", reason="无关")
    app.runtime.llm = Agent()
    app.short_term.llm = Summarizer()
    await plugin.sync_once()
    service.notices = [notice(1, 1)]
    assert await plugin.sync_once() == 1
    await app.proactive.tick()

    async def completed():
        return [t for t in await app.store.list("turn") if t["status"] == "completed"]

    assert len(await eventually(completed)) == 1
