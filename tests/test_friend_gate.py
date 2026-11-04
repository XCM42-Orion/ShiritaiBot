import asyncio
import json

from websockets.asyncio.server import serve

from shiritaibot.config import AdapterConfig
from shiritaibot.core import Reply


async def test_private_friend_gate(app):
    delivered = []

    class FakeLLM:
        async def complete(self, route, system, messages, tools, **kwargs):
            return Reply(text="ok")

    app.runtime.llm = FakeLLM()

    async def peer(ws):
        async for raw in ws:
            request = json.loads(raw)
            action = request["action"]
            if action == "get_login_info":
                data = {"user_id": 100}
            elif action == "get_friend_list":
                data = [{"user_id": 2, "nickname": "f"}]
            else:
                data = {}
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "echo": request["echo"], "data": data}))
            if action == "send_private_msg":
                delivered.append(request["params"]["user_id"])

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        cfg = app.config.value.model_copy(deep=True)
        cfg.adapters = [AdapterConfig(id="a", url=f"ws://127.0.0.1:{port}", owner_id="1", self_id="100",
                                      friends=["4"])]
        await app.reconfigure(cfg)

        hub = app.adapters
        bot = hub.instances["a"]
        for user, note in [(1, "owner"), (2, "friend"), (3, "stranger"), (4, "allowlist")]:
            event = {"post_type": "message", "message_type": "private", "user_id": user,
                     "message_id": 10 + user,
                     "message": [{"type": "text", "data": {"text": note}}]}
            await hub.receive(bot, event)
        async def done():
            return len(delivered) >= 3

        async with asyncio.timeout(5):
            while len(delivered) < 3:
                await asyncio.sleep(0.05)
        assert sorted(delivered) == [1, 2, 4], delivered
        await app.adapters.close()
