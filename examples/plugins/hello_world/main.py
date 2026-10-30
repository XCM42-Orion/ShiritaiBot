from shiritaibot.plugin_api import Plugin, schema


class Entry(Plugin):
    async def start(self):
        self.context.register_tool("hello_world", "返回欢迎消息", schema({}), self.hello, risk="safe")

    async def hello(self, ctx, args):
        self.context.logger.log(f"向 {ctx.session} 打招呼")
        return {"message": self.context.settings["greeting"], "session": ctx.session}
