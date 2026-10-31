import base64
import json

import httpx
import pytest
from pydantic import ValidationError

from shiritaibot.config import ModelConfig, ProviderConfig, Settings
from shiritaibot.llm import IMAGE_TOKENS, estimated_tokens, request_payload

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 100
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
CDN = "https://multimedia.nt.qq.com.cn/download?id="


def image_message(text="看看"):
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
        ],
    }


@pytest.mark.parametrize(
    "protocol,expected",
    [
        ("chat_completions", {"type": "image_url", "image_url": {"url": DATA_URL}}),
        ("responses", {"type": "input_image", "image_url": DATA_URL}),
        (
            "anthropic",
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": DATA_URL.split(",", 1)[1],
                },
            },
        ),
        (
            "gemini",
            {"inlineData": {"mimeType": "image/png", "data": DATA_URL.split(",", 1)[1]}},
        ),
    ],
)
def test_image_parts_use_each_protocol_format(protocol, expected):
    _, body = request_payload(protocol, "m", "sys", [image_message()], [], 100)
    first = {
        "chat_completions": lambda: body["messages"][1]["content"],
        "responses": lambda: body["input"][0]["content"],
        "anthropic": lambda: body["messages"][0]["content"],
        "gemini": lambda: body["contents"][0]["parts"],
    }[protocol]()
    assert first[1] == expected
    assert "看看" in json.dumps(first[0], ensure_ascii=False)


def test_images_are_estimated_by_count_not_base64_length():
    big = "data:image/png;base64," + "A" * 400000
    message = {"role": "user", "content": [{"type": "image_url", "image_url": {"url": big}}]}
    for protocol in ("chat_completions", "responses", "anthropic", "gemini"):
        _, body = request_payload(protocol, "m", "s", [message], [], 100)
        assert IMAGE_TOKENS <= estimated_tokens(body) < IMAGE_TOKENS + 200


def test_vision_route_requires_image_models():
    providers = [ProviderConfig(id="p", api_key="")]
    with pytest.raises(ValidationError, match="vision route"):
        Settings(
            providers=providers,
            models=[ModelConfig(id="t", provider="p", model="t")],
            routes={"vision": ["t"]},
        )
    Settings(
        providers=providers,
        models=[ModelConfig(id="v", provider="p", model="v", modalities=["text", "image"])],
        routes={"vision": ["v"]},
    )


async def setup(app, *, main_image: bool, caption: bool = True, vision: bool = True):
    app.config.value.providers = [
        ProviderConfig(id="p", base_url="https://fake.test/v1", api_key="")
    ]
    app.config.value.models = [
        ModelConfig(
            id="main",
            provider="p",
            model="main",
            modalities=["text", "image"] if main_image else ["text"],
        ),
        ModelConfig(id="eyes", provider="p", model="eyes", modalities=["text", "image"]),
    ]
    app.config.value.routes = {"agent": ["main"], "vision": ["eyes"] if vision else []}
    app.config.value.vision.caption = caption
    calls = {"download": 0, "eyes": 0, "main": []}

    async def handle(req):
        if req.url.host == "multimedia.nt.qq.com.cn":
            calls["download"] += 1
            return httpx.Response(200, content=PNG)
        body = json.loads(req.content)
        if body["model"] == "eyes":
            calls["eyes"] += 1
            assert body["messages"][1]["content"][0]["image_url"]["url"] == DATA_URL
            return httpx.Response(200, json={"choices": [{"message": {"content": "一只橘猫"}}]})
        calls["main"].append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await app.llm.client.aclose()
    app.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return calls


async def ref(app, session, n=1):
    key = f"{n:032x}"
    await app.store.put(
        "qq_file", key, {"adapter": "qq", "group_id": "", "data": {"url": CDN + str(n)}}, session
    )
    return key


async def ask(app, text, scope="private:qq:1"):
    return await app.llm.complete(
        "agent", "sys", [{"role": "user", "content": text}], [], scope=scope
    )


async def test_image_models_receive_the_image_and_it_is_cached(app):
    calls = await setup(app, main_image=True)
    key = await ref(app, "private:qq:1")
    await ask(app, f"这是什么\n[QQ 图片引用 {key}: a.jpg]")
    await ask(app, f"这是什么\n[QQ 图片引用 {key}: a.jpg]")
    content = calls["main"][0]["messages"][1]["content"]
    assert content == [
        {"type": "text", "text": f"这是什么\n[图片 ref={key}]"},
        {"type": "image_url", "image_url": {"url": DATA_URL}},
    ]
    assert calls["download"] == 1 and calls["eyes"] == 0
    assert (app.store.home / "media" / key).read_bytes() == PNG


async def test_only_recent_images_are_sent_natively(app):
    calls = await setup(app, main_image=True, caption=False)
    app.config.value.vision.max_images = 1
    old, new = await ref(app, "private:qq:1", 1), await ref(app, "private:qq:1", 2)
    await ask(app, f"[QQ 图片引用 {old}: a.jpg] [QQ 图片引用 {new}: b.jpg]")
    content = calls["main"][0]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": f"[图片 ref={old}] [图片 ref={new}]"}
    assert len(content) == 2 and calls["download"] == 1


async def test_text_models_get_a_cached_caption(app):
    calls = await setup(app, main_image=False)
    key = await ref(app, "private:qq:1")
    await ask(app, f"[QQ 图片引用 {key}: a.jpg]")
    await ask(app, f"还是这张 [QQ 图片引用 {key}: a.jpg]")
    assert calls["main"][0]["messages"][1]["content"] == f"[图片 ref={key}：一只橘猫]"
    assert calls["main"][1]["messages"][1]["content"] == f"还是这张 [图片 ref={key}：一只橘猫]"
    assert calls["eyes"] == 1 and calls["download"] == 1


@pytest.mark.parametrize("caption,vision", [(False, True), (True, False)])
async def test_text_models_get_a_placeholder_without_captioning(app, caption, vision):
    calls = await setup(app, main_image=False, caption=caption, vision=vision)
    key = await ref(app, "private:qq:1")
    await ask(app, f"[QQ 图片引用 {key}: a.jpg]")
    assert calls["main"][0]["messages"][1]["content"] == f"[图片 ref={key}]"
    assert calls["eyes"] == 0 and calls["download"] == 0


async def test_refs_from_another_session_are_never_resolved(app):
    calls = await setup(app, main_image=True)
    key = await ref(app, "private:qq:2")
    await ask(app, f"[QQ 图片引用 {key}: a.jpg]")
    assert calls["main"][0]["messages"][1]["content"] == f"[图片 ref={key}]"
    assert calls["download"] == 0


async def test_calls_without_scope_leave_markers_alone(app):
    calls = await setup(app, main_image=True)
    key = await ref(app, "private:qq:1")
    text = f"[QQ 图片引用 {key}: a.jpg]"
    await ask(app, text, scope=None)
    assert calls["main"][0]["messages"][1]["content"] == text
