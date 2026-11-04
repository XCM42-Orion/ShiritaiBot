import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from shiritaibot.config import ModelConfig, ProviderConfig, Settings
from shiritaibot.core import Context
from shiritaibot.llm.media import NO_VISION, model_image, view_marker
from shiritaibot.meme.core import MemeRejected, meme_id
from shiritaibot.meme.hooker import sticker_url
from shiritaibot.meme.inject import meme_prompt
from shiritaibot.web import create_app

CDN = "https://gchat.qpic.cn/sticker?id="


def png(color=(255, 0, 0)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def gif() -> bytes:
    frames = [Image.new("P", (8, 8), i) for i in (1, 2)]
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:])
    return out.getvalue()


def setup(app, *, decision=True, fit=0.9, category="happy", main_image=True):
    """Fake providers: a vision text model and optionally a Jev model in the meme route."""
    cfg = app.config.value
    cfg.meme.enabled = True
    cfg.providers = [
        ProviderConfig(id="p", base_url="https://fake.test/v1", api_key=""),
        ProviderConfig(id="jev", protocol="systemone", base_url="https://jev.test/v1", api_key=""),
    ]
    cfg.models = [
        ModelConfig(id="eyes", provider="p", model="eyes", modalities=["text", "image"]),
        ModelConfig(id="judge", provider="jev", model="jev-latest"),
        ModelConfig(
            id="main",
            provider="p",
            model="main",
            modalities=["text", "image"] if main_image else ["text"],
        ),
    ]
    cfg.routes = {"meme": (["judge"] if decision else []) + ["eyes"], "agent": ["main"]}
    calls = {"caption": 0, "judge": [], "text_judge": 0, "main": [], "download": 0}

    async def handle(request):
        if request.url.host == "gchat.qpic.cn":
            calls["download"] += 1
            return httpx.Response(200, content=png((0, 0, 255)))
        body = json.loads(request.content)
        if request.url.host == "jev.test":
            calls["judge"].append(body)
            answers = {}
            if "fit" in body["questions"]:
                answers["fit"] = {"noul": fit}
            if "category" in body["questions"]:
                answers["category"] = {"choice": category, "confidence": 0.9}
            return httpx.Response(200, json={"answers": answers})
        if body["model"] == "main":
            calls["main"].append(body)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        content = body["messages"][1]["content"]
        if isinstance(content, list):
            calls["caption"] += 1
            assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
            reply = "一只举着牌子大笑的猫，牌子上写着“哈哈哈”"
        else:
            calls["text_judge"] += 1
            reply = json.dumps({"fit": fit, "category": category})
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    app.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return calls


def test_meme_route_needs_image_text_models_but_accepts_decision_models():
    providers = [
        ProviderConfig(id="p", api_key=""),
        ProviderConfig(id="jev", protocol="systemone", api_key=""),
    ]
    with pytest.raises(ValidationError, match="meme 路由"):
        Settings(
            providers=providers,
            models=[ModelConfig(id="t", provider="p", model="t")],
            routes={"meme": ["t"]},
        )
    Settings(
        providers=providers,
        models=[
            ModelConfig(id="j", provider="jev", model="jev-latest"),
            ModelConfig(id="v", provider="p", model="v", modalities=["text", "image"]),
        ],
        routes={"meme": ["j", "v"]},
    )


def test_gif_stickers_reach_models_as_a_png_first_frame():
    url = model_image(gif())
    assert url and url.startswith("data:image/png;base64,")
    data = base64.b64decode(url.split(",", 1)[1])
    assert Image.open(io.BytesIO(data)).size == (8, 8)
    # Other formats pass through untouched.
    assert model_image(png()) == "data:image/png;base64," + base64.b64encode(png()).decode()


def test_sticker_segments_are_recognized():
    sticker = {"type": "image", "data": {"url": CDN + "1", "sub_type": 1}}
    photo = {"type": "image", "data": {"url": CDN + "2", "sub_type": 0}}
    summary = {"type": "image", "data": {"url": CDN + "3", "summary": "[动画表情]"}}
    market = {"type": "mface", "data": {"url": CDN + "4"}}
    foreign = {"type": "image", "data": {"url": "https://evil.test/a.gif", "sub_type": 1}}
    assert sticker_url(sticker, True) == CDN + "1"
    assert sticker_url(photo, True) is None and sticker_url(photo, False) == CDN + "2"
    assert sticker_url(summary, True) and sticker_url(market, True)
    assert sticker_url(foreign, True) is None


async def test_collection_rate_falls_as_the_library_fills(app):
    setup(app)
    cfg = app.config.value
    cfg.meme.capacity, cfg.meme.collect_rate = 4, 0.4
    hooker = app.memes.hooker
    assert hooker.probability(cfg) == pytest.approx(0.4)
    for index in range(2):
        await app.memes.core.add(png((index, 0, 0)), "happy", f"测试 {index}", "manual")
    assert hooker.probability(cfg) == pytest.approx(0.2)
    for index in range(2, 4):
        await app.memes.core.add(png((index, 0, 0)), "happy", f"测试 {index}", "manual")
    assert hooker.probability(cfg) == 0


async def test_hooker_describes_judges_and_stores_a_group_sticker(app, monkeypatch):
    calls = setup(app)
    monkeypatch.setattr("shiritaibot.meme.hooker.random.random", lambda: 0.0)
    app.memes.hooker.observe(
        "group:qq:100", [{"type": "image", "data": {"url": CDN + "1", "sub_type": 1}}]
    )
    row = await app.memes.hooker.task
    assert row["category"] == "happy" and row["source"] == "group"
    assert "大笑的猫" in row["description"]
    assert calls["caption"] == 1 and calls["text_judge"] == 0
    # The decision model sees the description and the persona, never the image.
    state = calls["judge"][0]["state"]
    assert "大笑的猫" in state["sticker"] and "ShiritaiBot" in state["persona"]
    assert set(calls["judge"][0]["questions"]) == {"fit", "category"}
    assert (app.home / "memes" / row["id"]).read_bytes() == png((0, 0, 255))
    # Private chats and disabled collection are ignored.
    app.memes.hooker.observe(
        "private:qq:1", [{"type": "image", "data": {"url": CDN + "2", "sub_type": 1}}]
    )
    assert app.memes.hooker.task.done()


async def test_stickers_that_do_not_suit_the_persona_are_rejected(app):
    setup(app, fit=0.1)
    with pytest.raises(MemeRejected):
        await app.memes.core.ingest(png(), source="group", persona_check=True)
    assert app.memes.core.count == 0


async def test_text_models_judge_when_no_decision_model_is_configured(app):
    calls = setup(app, decision=False, category="think")
    row = await app.memes.core.ingest(png(), source="manual", persona_check=True)
    assert row["category"] == "think" and calls["text_judge"] == 1 and not calls["judge"]


async def test_markers_split_replies_into_text_and_sticker_messages(app):
    setup(app)
    happy = await app.memes.core.add(png(), "happy", "大笑的猫", "manual")
    sad = await app.memes.core.add(png((0, 9, 0)), "sad", "哭泣的狗", "manual")
    await app.adapters.enqueue(
        "group:qq:100",
        f"好耶<meme>happy</meme>不过<meme>{sad['id'][:8]}</meme><meme>angry</meme>结束",
    )
    rows = await app.store.with_status("outbox", ("pending",))
    assert [(row["text"], row.get("meme")) for row in rows] == [
        ("好耶", None),
        ("[表情：大笑的猫]", happy["id"]),
        ("不过", None),
        ("[表情：哭泣的狗]", sad["id"]),
        # angry has no memes: its marker is dropped and the text around it joins up.
        ("结束", None),
    ]
    segments = await app.adapters.outgoing(rows[1])
    assert segments[0]["type"] == "image" and segments[0]["data"]["sub_type"] == 1
    assert base64.b64decode(segments[0]["data"]["file"].removeprefix("base64://")) == png()


async def test_marker_tolerates_repeated_me_tag_from_chat_model(app):
    setup(app)
    happy = await app.memes.core.add(png(), "happy", "大笑的猫", "manual")
    pieces = app.memes.injector.split("好耶<mememe>happy</mememe>")
    assert [isinstance(p, dict) for p in pieces] == [False, True]
    assert pieces[0] == "好耶" and pieces[1]["id"] == happy["id"]


async def test_marker_tolerates_mismatched_repeated_tags(app):
    setup(app)
    happy = await app.memes.core.add(png(), "happy", "大笑的猫", "manual")
    assert app.memes.injector.split("<meme>happy</mememe>")[0]["id"] == happy["id"]
    assert app.memes.injector.split("<mememe>happy</meme>")[0]["id"] == happy["id"]


async def test_stray_marker_tags_never_reach_the_chat(app):
    setup(app)
    await app.memes.core.add(png(), "happy", "大笑的猫", "manual")
    assert app.memes.injector.split("好<mememe>happy") == ["好happy"]


async def test_marker_limits_and_disabled_feature(app):
    setup(app)
    await app.memes.core.add(png(), "happy", "大笑的猫", "manual")
    app.config.value.meme.max_per_reply = 1
    pieces = app.memes.injector.split("<meme>happy</meme>a<meme>happy</meme>")
    assert [isinstance(p, dict) for p in pieces] == [True, False] and pieces[1] == "a"
    app.config.value.meme.enabled = False
    assert app.memes.injector.split("x<meme>happy</meme>") == ["x<meme>happy</meme>"]


async def test_web_replies_show_readable_labels(app):
    setup(app)
    await app.memes.core.add(png(), "happy", "大笑的猫", "manual")
    await app.adapters.enqueue("web:owner", "好<meme>happy</meme>")
    rows = await app.store.list("reply", scope="web:owner")
    assert rows[0]["text"] == "好\n[表情：大笑的猫]"


def test_prompt_lists_categories_and_is_stable(app):
    cfg = app.config.value
    assert meme_prompt(cfg) == ""
    cfg.meme.enabled = True
    prompt = meme_prompt(cfg)
    assert "<meme>分类</meme>" in prompt and "- happy：" in prompt
    assert prompt == meme_prompt(cfg.model_copy(deep=True))


async def test_qq_turns_get_the_meme_prompt(app):
    calls = setup(app)
    app.config.value.routes["chat"] = ["main"]
    await app.runtime._turn(
        type("Actor", (), {"steering": []})(),
        Context("group:qq:100", "42", "chat", group=True),
        "你好",
    )
    assert "<memes>" in calls["main"][0]["messages"][0]["content"]


async def tool_turn(app, tool_output: str):
    messages = [
        {"role": "user", "content": "看看这个表情"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "meme_view", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": tool_output},
    ]
    return await app.llm.complete("agent", "sys", messages, [], scope="private:qq:1")


async def test_meme_view_attaches_the_image_for_image_models(app):
    calls = setup(app, main_image=True)
    row = await app.memes.core.add(gif(), "happy", "动图猫", "manual")
    output = await app.registry.execute(
        Context("private:qq:1", "1", "agent", True), "meme_view", {"id": row["id"]}
    )
    assert view_marker(row["id"]) in output
    await tool_turn(app, output)
    sent = calls["main"][0]["messages"]
    assert sent[3]["role"] == "tool" and "图片附在随后的消息中" in sent[3]["content"]
    assert sent[4]["role"] == "user"
    assert sent[4]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_meme_view_errors_for_text_only_models(app):
    calls = setup(app, main_image=False)
    row = await app.memes.core.add(png(), "happy", "猫", "manual")
    await tool_turn(app, json.dumps({"image": view_marker(row["id"])}, ensure_ascii=False))
    sent = calls["main"][0]["messages"]
    assert sent[3]["content"] == NO_VISION and len(sent) == 4


async def test_search_and_save_tools(app):
    calls = setup(app)
    ctx = Context("private:qq:1", "1", "agent", True)
    await app.memes.core.add(png(), "happy", "举牌大笑的猫", "manual")
    found = json.loads(await app.registry.execute(ctx, "meme_search", {"query": "大笑"}))
    assert found["memes"][0]["description"] == "举牌大笑的猫"
    ref = "a" * 32
    await app.store.put("qq_file", ref, {"adapter": "qq", "data": {"url": CDN + "9"}}, ctx.session)
    app.config.value.policy.owner_default.tools["meme_save"] = "allow"
    saved = json.loads(
        await app.registry.execute(ctx, "meme_save", {"ref": ref, "category": "love"})
    )
    assert saved["category"] == "love" and calls["download"] == 1
    other = Context("private:qq:2", "2", "agent", True)
    denied = json.loads(await app.registry.execute(other, "meme_save", {"ref": ref}))
    assert "error" in denied


def test_webui_meme_crud(tmp_path):
    with TestClient(create_app(tmp_path, token="admin")) as client:
        client.headers["Authorization"] = "Bearer admin"
        upload = client.post("/api/memes?category=happy&description=开心的猫", content=png())
        assert upload.status_code == 200, upload.text
        key = upload.json()["id"]
        assert key == meme_id(png())
        listing = client.get("/api/memes").json()
        assert listing["count"] == 1 and listing["counts"]["happy"] == 1
        image = client.get(f"/api/memes/{key}/image")
        assert image.content == png() and image.headers["content-type"] == "image/png"
        edited = client.put(f"/api/memes/{key}", json={"category": "love", "description": "猫猫"})
        assert edited.json()["category"] == "love"
        assert client.put(f"/api/memes/{key}", json={"category": "nope"}).status_code == 400
        assert client.post("/api/memes?category=happy", content=b"not an image").status_code == 400
        assert client.delete(f"/api/memes/{key}").status_code == 200
        assert client.get("/api/memes").json()["count"] == 0
        assert not (tmp_path / "memes" / key).exists()
