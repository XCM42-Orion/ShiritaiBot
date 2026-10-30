import base64
import json

import pytest

from shiritaibot.adapters.onebot11 import OneBot
from shiritaibot.config import AdapterConfig
from shiritaibot.core import Context
from shiritaibot.tools.qq import session_dir

OWNER = Context("private:qq:1", "1", "agent", True)


class FakeBot(OneBot):
    def __init__(self, app, adapter="qq", replies=None):
        super().__init__(
            AdapterConfig(id=adapter, owner_id="1", self_id="100"), app.vault, app.store, None
        )
        self.self_id, self.status = "100", "connected"
        self.calls, self.replies = [], replies or {}

    async def call(self, action, params):
        self.calls.append((action, params))
        return self.replies.get(action, {"message_id": 42})


@pytest.fixture
def bot(app):
    app.config.value.policy.owner_default.preset = "full_access"
    bot = FakeBot(app)
    app.adapters.instances["qq"] = bot
    return bot


async def run(app, tool, args, ctx=OWNER):
    return json.loads(await app.registry.execute(ctx, tool, args))


@pytest.mark.parametrize(
    "ctx",
    [
        Context("group:qq:9", "1", "chat", True, True),
        Context("private:qq:2", "2", "chat"),
    ],
)
async def test_qq_tools_are_owner_agent_only(app, bot, ctx):
    for tool in ("qq_message_send", "qq_group_kick", "qq_request_handle", "qq_friend_list"):
        assert app.permissions.decision(ctx, app.registry.tools[tool]) == "deny"
        assert "error" in await run(app, tool, {}, ctx)
    assert bot.calls == []


async def test_send_message_builds_segments_and_records_group_context(app, bot):
    app.config.value.group_context.enabled = True
    result = await run(
        app,
        "qq_message_send",
        {"target": "group", "id": 9, "text": "开会了", "at": [3, "all"], "reply_to": -5},
    )
    assert result == {"message_id": 42}
    action, params = bot.calls[0]
    assert action == "send_group_msg" and params["group_id"] == 9
    assert [p["type"] for p in params["message"]] == ["reply", "at", "at", "text"]
    rows = await app.store.group_messages("group:qq:9", 10)
    assert rows[0]["sender"] == "100" and "开会了" in rows[0]["text"]


async def test_adapter_must_be_named_when_ambiguous(app, bot):
    app.adapters.instances["other"] = FakeBot(app, "other")
    bot.replies["get_friend_list"] = []
    web = Context("web:owner", "owner", "agent", True)
    assert "adapter" in (await run(app, "qq_friend_list", {}, web))["error"]
    assert (await run(app, "qq_friend_list", {"adapter": "qq"}, web)) == []
    # A QQ session defaults to its own instance.
    await run(app, "qq_message_recall", {"message_id": 7})
    assert bot.calls[-1] == ("delete_msg", {"message_id": 7})


async def test_local_files_are_confined_to_session_folders(app, bot, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    result = await run(app, "qq_file_send", {"target": "private", "id": 2, "path": str(outside)})
    assert "error" in result
    workspace = session_dir(app.home, "workspaces", OWNER.session)
    workspace.mkdir(parents=True)
    (workspace / "a.txt").write_text("hello")
    assert "error" in await run(
        app, "qq_file_send", {"target": "private", "id": 2, "path": "../" + outside.name}
    )
    result = await run(app, "qq_file_send", {"target": "group", "id": 9, "path": "a.txt"})
    assert result["sent"] and result["bytes"] == 5
    action, params = bot.calls[-1]
    assert action == "upload_group_file" and params["name"] == "a.txt"
    assert params["file"] == "base64://" + base64.b64encode(b"hello").decode()


async def test_message_get_registers_session_bound_file_refs(app, bot):
    bot.replies["get_msg"] = {
        "message_id": 5,
        "message_type": "group",
        "group_id": 9,
        "time": 1700000000,
        "sender": {"user_id": 3, "card": "小明"},
        "message": [
            {"type": "text", "data": {"text": "看这个"}},
            {"type": "file", "data": {"name": "a.pdf", "file_id": "f1"}},
            {"type": "forward", "data": {"id": "fw1"}},
        ],
    }
    result = await run(app, "qq_message_get", {"message_id": 5})
    assert result["text"] == "看这个[文件 a.pdf][合并转发 fw1]"
    assert result["sender"] == {"user_id": 3, "name": "小明"}
    ref = result["files"][0]["ref"]
    row = await app.store.get("qq_file", ref)
    assert row["scope"] == OWNER.session and row["group_id"] == "9"
    other = Context("private:qq:2", "2", "agent", True)
    app.config.value.policy.owner_default.tools["qq_file_download"] = "allow"
    assert "error" in await run(app, "qq_file_download", {"ref": ref}, other)


async def test_member_list_is_filtered_sorted_and_paged(app, bot):
    bot.replies["get_group_member_list"] = [
        {"user_id": i, "nickname": f"n{i}", "card": "", "role": "member", "join_time": i}
        for i in range(1, 6)
    ] + [{"user_id": 99, "nickname": "boss", "role": "owner", "join_time": 50}]
    page = await run(app, "qq_group_members", {"group_id": 9, "limit": 2})
    assert [m["user_id"] for m in page["members"]] == [99, 1]
    assert page["total"] == 6 and page["next_offset"] == 2
    last = await run(app, "qq_group_members", {"group_id": 9, "offset": 4, "limit": 2})
    assert [m["user_id"] for m in last["members"]] == [4, 5] and last["next_offset"] is None
    assert (await run(app, "qq_group_members", {"group_id": 9, "query": "BOSS"}))["total"] == 1


async def test_requests_are_recorded_once_and_handled(app, bot):
    emitted = []

    async def emit(event):
        emitted.append(event)
        return "k"

    app.adapters.emit = emit
    event = {
        "post_type": "request",
        "request_type": "friend",
        "user_id": 3,
        "comment": "我是小明",
        "flag": "flag-1",
    }
    await app.adapters.receive(bot, event)
    await app.adapters.receive(bot, event)
    await app.adapters.receive(
        bot,
        {
            "post_type": "request",
            "request_type": "group",
            "sub_type": "add",
            "user_id": 4,
            "group_id": 9,
            "flag": "flag-2",
        },
    )
    assert [e.kind for e in emitted] == ["qq.request.friend", "qq.request.group"]
    pending = await run(app, "qq_request_list", {})
    assert len(pending) == 2 and all("flag" not in row for row in pending)
    friend = next(r for r in pending if r["kind"] == "friend")
    group = next(r for r in pending if r["kind"] == "group")

    result = await run(app, "qq_request_handle", {"request": friend["id"], "action": "approve"})
    assert result == {"status": "approved"}
    assert bot.calls[-1] == (
        "set_friend_add_request",
        {"flag": "flag-1", "approve": True, "remark": ""},
    )
    assert "error" in await run(
        app, "qq_request_handle", {"request": friend["id"], "action": "reject"}
    )

    calls = len(bot.calls)
    assert (await run(app, "qq_request_handle", {"request": group["id"], "action": "ignore"})) == {
        "status": "ignored"
    }
    assert len(bot.calls) == calls
    assert await run(app, "qq_request_list", {}) == []


async def test_group_invite_sends_share_card(app, bot):
    bot.replies["ArkShareGroup"] = '{"app":"com.tencent.contact.lua"}'
    await run(app, "qq_group_invite", {"group_id": 9, "user_id": 3})
    assert bot.calls[-1] == (
        "send_private_msg",
        {
            "user_id": 3,
            "message": [{"type": "json", "data": {"data": '{"app":"com.tencent.contact.lua"}'}}],
        },
    )
