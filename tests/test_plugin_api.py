import json
import shutil
from pathlib import Path

import pytest
from test_group_context import make_receiver
from test_permissions import eventually

from shiritaibot.api import MessageEvent
from shiritaibot.core import Context, ExternalEvent, Reply
from shiritaibot.plugin.core.manifest import (
    ManifestError,
    convert_schema,
    read_manifest,
    version_matches,
)

EXAMPLE = Path(__file__).parents[1] / "examples/plugins/hello_world"
OWNER = Context("web:owner", "owner", "agent", True)
METADATA = "name: probe\ndisplay_name: Probe\ndesc: test plugin\nversion: 0.1.0\n"


def write_plugin(app, key: str, source: str, conf: dict | None = None, meta: str = METADATA):
    directory = app.home / "plugins" / key
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "metadata.yaml").write_text(meta, encoding="utf-8")
    (directory / "main.py").write_text(source, encoding="utf-8")
    if conf is not None:
        (directory / "_conf_schema.json").write_text(json.dumps(conf), encoding="utf-8")
    return directory


async def enable(app, key: str, **settings):
    await app.plugins.sync_config()
    cfg = next(p for p in app.config.value.plugins if p.id == key)
    cfg.enabled = True
    cfg.settings.update(settings)
    await app.plugins.reload(key)
    return app.plugins.loaded[key]


async def install_example(app, **settings):
    shutil.copytree(EXAMPLE, app.home / "plugins" / "hello_world")
    return await enable(app, "hello_world", **settings)


def web_event(text: str, **fields) -> MessageEvent:
    return MessageEvent(
        session=fields.pop("session", "web:owner"),
        text=text,
        sender_id=fields.pop("sender_id", "owner"),
        platform=fields.pop("platform", "web"),
        is_owner=fields.pop("is_owner", True),
        **fields,
    )


async def replies(app, session="web:owner") -> list[str]:
    rows = await app.store.list("reply", scope=session)
    return [row["text"] for row in sorted(rows, key=lambda row: row["at"])]


# Manifest -------------------------------------------------------------------------------


def test_conf_schema_converts_astrbot_fields_to_json_schema():
    schema = convert_schema(
        {
            "token": {"type": "string", "description": "Token", "secret": True},
            "mode": {
                "type": "string",
                "options": ["a", "b"],
                "labels": ["甲", "乙"],
                "default": "a",
            },
            "level": {"type": "int", "slider": {"min": 1, "max": 9, "step": 2}},
            "note": {"type": "text", "hint": "说明", "obvious_hint": True, "invisible": True},
            "tags": {"type": "list", "options": ["x", "y"]},
            "nested": {
                "type": "object",
                "items": {"size": {"type": "float", "default": 1.5}, "on": {"type": "bool"}},
            },
            "extra": {
                "type": "dict",
                "items": {},
                "template_schema": {"temperature": {"type": "float", "name": "Temperature"}},
            },
            "rules": {
                "type": "template_list",
                "templates": {"t1": {"name": "规则", "items": {"word": {"type": "string"}}}},
            },
        }
    )
    props = schema["properties"]
    assert props["token"] == {"type": "string", "title": "Token", "writeOnly": True, "default": ""}
    assert props["mode"]["enum"] == ["a", "b"] and props["mode"]["x-labels"] == ["甲", "乙"]
    assert props["level"]["minimum"] == 1 and props["level"]["x-step"] == 2
    assert props["level"]["x-widget"] == "slider" and props["level"]["default"] == 0
    assert props["note"]["x-widget"] == "textarea" and props["note"]["description"] == "说明"
    assert props["note"]["x-obvious-hint"] and props["note"]["x-invisible"]
    assert props["tags"]["items"] == {"type": "string", "enum": ["x", "y"]}
    assert props["nested"]["default"] == {"size": 1.5, "on": False}
    assert props["extra"]["x-template-schema"]["temperature"]["x-name"] == "Temperature"
    assert props["rules"]["x-templates"]["t1"]["properties"]["word"]["type"] == "string"
    assert props["rules"]["items"]["properties"]["__template_key"]["enum"] == ["t1"]


@pytest.mark.parametrize(
    "conf",
    [
        {"x": {"type": "file"}},
        {"x": {"type": "object"}},
        {"x": {"type": "bool", "secret": True}},
        {"x": "string"},
    ],
)
def test_conf_schema_rejects_unsupported_fields(conf):
    with pytest.raises(ManifestError):
        convert_schema(conf)


def test_version_ranges():
    assert version_matches(">=0.2,<1", "0.2.0")
    assert not version_matches(">=0.3", "0.2.9")
    assert version_matches("!=0.1", "0.2")
    with pytest.raises(ManifestError):
        version_matches("~=0.2", "0.2")


def test_manifest_errors_do_not_raise(tmp_path):
    (tmp_path / "metadata.yaml").write_text("name: x\nshiritaibot_version: '>=99'\n")
    (tmp_path / "main.py").write_text("")
    entry = read_manifest(tmp_path)
    assert entry["api"] == 2 and "99" in entry["error"]


# Loading -------------------------------------------------------------------------------


async def test_example_plugin_registers_commands_tools_and_hooks(app):
    plugin = await install_example(app)
    assert plugin.config["greeting"] == "你好，ShiritaiBot！"
    listing = {p["id"]: p for p in await app.plugins.listing()}["hello_world"]
    assert listing["api"] == 2 and listing["version"] == "1.0.0" and listing["status"] == "loaded"
    commands = {c["command"]: c for c in listing["commands"]}
    assert set(commands) == {"/hello [name]", "/calc add <a:int> <b:int>", "/hello_reset"}
    assert commands["/hello_reset"]["owner_only"]
    assert {h["event"] for h in listing["hooks"]} >= {
        "message",
        "llm_request",
        "decorating_result",
        "proactive_event",
        "proactive_judged",
    }
    assert listing["tools"] == [{"name": "hello_world", "risk": "safe", "safe": True}]
    tool = app.registry.tools["hello_world"]
    assert tool.chat and tool.schema["properties"]["name"]["type"] == "string"
    assert tool.schema["required"] == []
    result = json.loads(await app.registry.execute(OWNER, "hello_world", {"name": "小明"}))
    assert result == {"message": "你好，ShiritaiBot！ 小明", "session": "web:owner"}


async def test_config_kv_and_save_config(app):
    write_plugin(
        app,
        "probe",
        "from shiritaibot.api import Plugin\nclass Probe(Plugin):\n    pass\n",
        {"token": {"type": "string", "secret": True}, "count": {"type": "int", "default": 3}},
    )
    plugin = await enable(app, "probe")
    assert dict(plugin.config) == {"token": "", "count": 3}
    plugin.config["token"] = "plain-token"
    plugin.config["count"] = 4
    await plugin.config.save_config()
    saved = next(p for p in app.config.value.plugins if p.id == "probe").settings
    assert saved["count"] == 4 and saved["token"].startswith("secret:")
    assert await app.vault.resolve(saved["token"]) == "plain-token"
    await plugin.put_kv_data("seen", {"n": 1})
    assert await plugin.get_kv_data("seen") == {"n": 1}
    await plugin.delete_kv_data("seen")
    assert await plugin.get_kv_data("seen", "none") == "none"


async def test_schema_changes_add_defaults_and_drop_removed_fields(app):
    directory = write_plugin(
        app,
        "probe",
        "from shiritaibot.api import Plugin\nclass Probe(Plugin):\n    pass\n",
        {"old": {"type": "string", "default": "o"}, "keep": {"type": "int", "default": 1}},
    )
    await enable(app, "probe", keep=5)
    (directory / "_conf_schema.json").write_text(
        json.dumps({"keep": {"type": "int"}, "new": {"type": "bool", "default": True}})
    )
    await app.plugins.reload_all()
    assert next(p for p in app.config.value.plugins if p.id == "probe").settings == {
        "keep": 5,
        "new": True,
    }


async def test_plugin_class_and_command_conflicts_fail_loading(app):
    write_plugin(app, "probe", "from shiritaibot.api import Plugin\n")
    await app.plugins.sync_config()
    next(p for p in app.config.value.plugins if p.id == "probe").enabled = True
    with pytest.raises(Exception):
        await app.plugins.reload("probe")
    assert "Plugin 子类" in app.plugins.errors["probe"]

    write_plugin(
        app,
        "probe",
        "from shiritaibot.api import Plugin, hook\n"
        "class Probe(Plugin):\n"
        "    @hook.command('status')\n"
        "    async def status(self, event):\n"
        "        return 'mine'\n",
    )
    with pytest.raises(Exception):
        await app.plugins.reload("probe")
    assert "status" in app.plugins.errors["probe"] and "probe" not in app.plugins.loaded


# Messages ------------------------------------------------------------------------------


async def test_commands_parse_arguments_and_record_history(app):
    await install_example(app)
    hooks = app.plugins.hooks
    assert await hooks.dispatch(web_event("/calc add 2 40"))
    assert await hooks.dispatch(web_event("/calc add two 40"))
    assert await hooks.dispatch(web_event("/calc"))

    async def count(n):
        found = await replies(app)
        return found if len(found) == n else None

    await eventually(lambda: count(3))
    # Commands run in the background; wait so the example's KV counter is sequential.
    assert await hooks.dispatch(web_event("/你好 小红"))
    await eventually(lambda: count(4))
    assert await hooks.dispatch(web_event("/hello"))

    async def done():
        return await count(5)

    found = await eventually(done)
    assert "42" in found
    assert "用法：/calc add <a:int> <b:int>" in found
    assert any(text.startswith("可用的子指令：") for text in found)
    assert any(text.endswith("小红（第 1 次）") for text in found)
    assert any(text.endswith("owner（第 2 次）") for text in found)
    history = await app.store.history("web:owner")
    assert all(row.get("_command") for row in history)
    assert not await app.store.history("web:owner", include_commands=False)


async def test_owner_only_commands_and_builtins_stay_with_the_host(app):
    await install_example(app)
    hooks = app.plugins.hooks
    stranger = dict(session="private:qq:7", sender_id="7", platform="qq", is_owner=False)
    assert await hooks.dispatch(web_event("/hello_reset", **stranger))
    outbox = await app.store.list("outbox", scope="private:qq:7")
    assert [row["text"] for row in outbox] == ["此指令仅限主人使用。"]
    for text in ["/help", "/stop", "停", "/unknown"]:
        assert not await hooks.dispatch(web_event(text))
    await app.runtime.submit(OWNER, "/help")
    help_text = (await replies(app))[-1]
    assert "/hello — 打个招呼" in help_text and "/hello_reset" in help_text


async def test_listeners_stop_events_and_see_unwoken_group_chatter(app):
    write_plugin(
        app,
        "probe",
        "from shiritaibot.api import MessageType, Plugin, hook\n"
        "class Probe(Plugin):\n"
        "    seen = []\n"
        "    @hook.message_type(MessageType.GROUP, priority=5)\n"
        "    async def watch(self, event):\n"
        "        self.seen.append((event.text, event.is_wake, event.sender_name))\n"
        "    @hook.regex('^ping$')\n"
        "    async def ping(self, event):\n"
        "        yield event.plain_result('pong')\n"
        "        event.stop_event()\n",
    )
    plugin = await enable(app, "probe")
    submitted = []

    async def submit(ctx, text):
        submitted.append(text)
        return "queued"

    app.adapters.runtime.submit = submit
    receive = make_receiver(app)
    await receive("随便聊聊", name="小明")
    await receive("ping", mention=True)
    await receive("你好", mention=True)
    assert plugin.seen == [
        ("随便聊聊", False, "小明"),
        ("ping", True, "昵称3"),
        ("你好", True, "昵称3"),
    ]
    assert submitted == ["你好"]
    outbox = await app.store.list("outbox", scope="group:qq:9")
    assert [row["text"] for row in outbox] == ["pong"]


def test_web_chat_dispatches_plugin_commands(tmp_path):
    from fastapi.testclient import TestClient

    from shiritaibot.web import create_app

    shutil.copytree(EXAMPLE, tmp_path / "plugins" / "hello_world")
    (tmp_path / "config.toml").write_text('[[plugins]]\nid = "hello_world"\nenabled = true\n')
    with TestClient(create_app(tmp_path, token="admin")) as client:
        client.headers["Authorization"] = "Bearer admin"
        response = client.post("/api/messages", json={"text": "ping"})
        assert response.json() == {"status": "plugin"}
        response = client.post("/api/messages", json={"text": "/stop"})
        assert response.json() != {"status": "plugin"}


async def test_decorating_hook_rewrites_and_cancels_outgoing_text(app):
    write_plugin(
        app,
        "probe",
        "from shiritaibot.api import Plugin, hook\n"
        "class Probe(Plugin):\n"
        "    sent = []\n"
        "    @hook.on_decorating_result()\n"
        "    async def decorate(self, message):\n"
        "        if 'secret' in message.text:\n"
        "            message.cancel()\n"
        "        message.text = message.text.upper()\n"
        "    @hook.after_message_sent()\n"
        "    async def after(self, message):\n"
        "        self.sent.append(message.text)\n",
    )
    plugin = await enable(app, "probe")
    await app.adapters.enqueue("web:owner", "hello")
    await app.adapters.enqueue("web:owner", "a secret")
    await app.adapters.enqueue("web:owner", "approval", decorate=False)
    assert await replies(app) == ["HELLO", "approval"]
    await eventually(lambda: _ready(len(plugin.sent) == 2))
    assert plugin.sent == ["HELLO", "approval"]


async def _ready(value):
    return value


# Agent lifecycle ------------------------------------------------------------------------


async def test_llm_hooks_edit_requests_and_observe_tools(app):
    write_plugin(
        app,
        "probe",
        "from shiritaibot.api import Plugin, hook\n"
        "class Probe(Plugin):\n"
        "    log = []\n"
        "    @hook.on_agent_begin()\n"
        "    async def begin(self, turn):\n"
        "        self.log.append(('begin', turn.session, turn.text))\n"
        "    @hook.on_llm_request()\n"
        "    async def request(self, turn, request):\n"
        "        request.system_prompt += '\\nPLUGIN-SYSTEM'\n"
        "        request.add_context('PLUGIN-CONTEXT')\n"
        "        request.tools[:] = [t for t in request.tools\n"
        "                            if t['function']['name'] in {'hello_probe'}]\n"
        "    @hook.on_llm_response()\n"
        "    async def response(self, turn, response):\n"
        "        response.text = response.text.replace('raw', 'edited')\n"
        "    @hook.on_tool_call()\n"
        "    async def call(self, turn, name, arguments):\n"
        "        self.log.append(('call', name, arguments))\n"
        "    @hook.on_tool_result()\n"
        "    async def result(self, turn, name, arguments, result):\n"
        "        self.log.append(('result', name, result))\n"
        "    @hook.on_agent_done()\n"
        "    async def done(self, turn, result):\n"
        "        self.log.append(('done', result.status, result.text))\n"
        "    @hook.llm_tool(risk='safe')\n"
        "    async def hello_probe(self, ctx, who: str):\n"
        "        return 'hi ' + who\n",
    )
    plugin = await enable(app, "probe")
    app.config.value.policy.owner_default.preset = "full_access"
    calls = []

    class FakeLLM:
        async def complete(self, route, system, messages, tools, **kwargs):
            calls.append((system, messages, [t["function"]["name"] for t in tools]))
            if len(calls) == 1:
                call = {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "hello_probe", "arguments": '{"who": "bob"}'},
                }
                return Reply(text="", calls=[call])
            return Reply(text="raw answer")

        async def decide(self, *args, **kwargs):
            return None

    app.runtime.llm = FakeLLM()
    await app.runtime.submit(OWNER, "你好")

    async def finished():
        return any(entry[0] == "done" for entry in plugin.log)

    await eventually(finished)
    system, messages, tools = calls[0]
    assert system.endswith("PLUGIN-SYSTEM") and tools == ["hello_probe"]
    assert messages[-1]["content"].endswith("PLUGIN-CONTEXT")
    stored = await app.store.history("web:owner")
    assert "PLUGIN-CONTEXT" not in json.dumps(stored, ensure_ascii=False)
    assert stored[-1]["content"] == "edited answer"
    assert plugin.log == [
        ("begin", "web:owner", "你好"),
        ("call", "hello_probe", {"who": "bob"}),
        ("result", "hello_probe", '"hi bob"'),
        ("done", "completed", "edited answer"),
    ]


async def test_failing_hooks_never_break_the_pipeline(app):
    write_plugin(
        app,
        "probe",
        "import asyncio\n"
        "from shiritaibot.api import Plugin, hook\n"
        "class Probe(Plugin):\n"
        "    @hook.on_decorating_result()\n"
        "    async def boom(self, message):\n"
        "        raise RuntimeError('boom')\n"
        "    @hook.message_type()\n"
        "    async def slow(self, event):\n"
        "        raise ValueError('listener')\n",
    )
    await enable(app, "probe")
    await app.adapters.enqueue("web:owner", "still sent")
    assert await replies(app) == ["still sent"]
    assert not await app.plugins.hooks.dispatch(web_event("hello"))
    failures = [a for a in await app.store.list("activity") if a["kind"] == "plugin.hook_failed"]
    assert {a["detail"] for a in failures} == {"probe.boom: RuntimeError", "probe.slow: ValueError"}


async def test_reloaded_plugin_handlers_are_not_called_and_on_loaded_runs(app):
    source = (
        "from shiritaibot.api import Plugin, hook\n"
        "class Probe(Plugin):\n"
        "    @hook.on_loaded()\n"
        "    async def loaded(self):\n"
        "        await self.put_kv_data('loaded', '{tag}')\n"
        "    @hook.on_decorating_result()\n"
        "    async def tag(self, message):\n"
        "        message.text += '{tag}'\n"
    )
    write_plugin(app, "probe", source.format(tag="-1"))
    first = await enable(app, "probe")
    await eventually(lambda: first.get_kv_data("loaded"))
    write_plugin(app, "probe", source.format(tag="-2"))
    await app.plugins.reload("probe")
    await app.adapters.enqueue("web:owner", "x")
    assert await replies(app) == ["x-2"]

    async def reloaded():
        return await app.plugins.kv_get("probe", "loaded") == "-2"

    await eventually(reloaded)
    stale = next(h for h in app.plugins.hooks.handlers["probe"])
    stale.owner = first
    assert not app.plugins._alive(stale)


# Proactive engine -----------------------------------------------------------------------

PROACTIVE = (
    "from shiritaibot.api import Plugin, hook\n"
    "class Probe(Plugin):\n"
    "    events = []\n"
    "    @hook.on_proactive_event()\n"
    "    async def start(self, event):\n"
    "        self.events.append(('start', event.source, event.protected))\n"
    "        if event.title.startswith('spam'):\n"
    "            event.drop('广告')\n"
    "        event.title = event.title + '（已标注）'\n"
    "    @hook.on_proactive_judged()\n"
    "    async def judged(self, event, triage):\n"
    "        self.events.append(('judged', event.title, triage.importance))\n"
    "        triage.importance = 0 if event.title.startswith('low') else 4\n"
    "    @hook.on_proactive_done()\n"
    "    async def done(self, event, outcome):\n"
    "        self.events.append(('done', outcome.notify, outcome.forced))\n"
    "        outcome.message = '[插件] ' + outcome.message\n"
    "        outcome.notify = False\n"
)


class TriageLLM:
    """Every triage scores 2; every Proactive Loop reports a notification."""

    async def decide(self, *args, **kwargs):
        return None

    async def complete(self, route, system, messages, tools, **kwargs):
        if route == "light":
            return Reply(text='{"importance": 2, "decision": "log", "reason": "普通"}')
        report = {"notify": True, "message": "处理完毕", "reason": "完成"}
        call = {
            "id": "r1",
            "type": "function",
            "function": {"name": "proactive_report", "arguments": json.dumps(report)},
        }
        return Reply(text="", calls=[call])


async def run_events(app, *events):
    for task in app.tasks:
        task.cancel()
    app.config.value.proactive.enabled = True
    app.config.value.proactive.quiet_start = app.config.value.proactive.quiet_end = "00:00"
    app.proactive.llm = app.runtime.llm = TriageLLM()
    keys = [await app.proactive.emit(event) for event in events]

    async def settled():
        await app.proactive.tick()
        rows = [await app.store.get("event", key) for key in keys]
        return rows if all(row["status"] in {"processed", "discarded"} for row in rows) else None

    return await eventually(settled)


async def test_proactive_hooks_drop_adjust_and_rewrite(app):
    write_plugin(app, "probe", PROACTIVE)
    plugin = await enable(app, "probe")
    spam, low, important = await run_events(
        app,
        ExternalEvent(source="mail", kind="mail", dedup_key="1", title="spam offer"),
        ExternalEvent(source="mail", kind="mail", dedup_key="2", title="low priority"),
        ExternalEvent(source="mail", kind="mail", dedup_key="3", title="exam moved"),
    )
    assert spam["status"] == "discarded" and spam["reason"] == "广告"
    assert low["status"] == "discarded" and low["event"]["title"] == "low priority（已标注）"
    assert important["decision"]["importance"] == 4
    # Importance 4 is forced: the plugin rewrote the message but could not cancel it.
    assert important["notification_queued"]
    assert important["result"]["message"] == "[插件] 处理完毕"
    assert ("judged", "exam moved（已标注）", 2) in plugin.events
    assert ("done", True, True) in plugin.events


async def test_proactive_hooks_cannot_drop_or_silence_owner_reminders(app):
    write_plugin(app, "probe", PROACTIVE)
    plugin = await enable(app, "probe")
    (reminder,) = await run_events(
        app,
        ExternalEvent(
            source="routine", kind="routine.reminder", dedup_key="r", title="spam 时间到了"
        ),
    )
    assert ("start", "routine", True) in plugin.events
    assert reminder["status"] == "processed" and reminder["notification_queued"]
    # The judged hook's importance 4 stands; the scheduled floor is 3 either way.
    assert reminder["decision"]["importance"] >= 3


async def test_proactive_done_can_cancel_ordinary_notifications(app):
    source = PROACTIVE.replace("else 4", "else 2")
    write_plugin(app, "probe", source)
    await enable(app, "probe")
    (row,) = await run_events(
        app, ExternalEvent(source="mail", kind="mail", dedup_key="9", title="weekly digest")
    )
    assert row["status"] == "processed" and not row["notification_queued"]
    assert row["result"]["message"] == "[插件] 处理完毕"
