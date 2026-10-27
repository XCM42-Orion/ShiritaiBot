# 插件与 OneLearn

## 目录约定

按本次确认的结构，`src/shiritaibot/plugin/core/` 是主仓库的插件宿主；`src/shiritaibot/plugin/` 下其余目录都被 `.gitignore` 忽略。飞书和网络学堂实现已留在本地对应目录。旧的 `integrations/`、`builtin_plugins/` 已撤销。

`src/shiritaibot/learn/` 属于核心代码：定义学校无关的请求/响应、stdio 接入、查询和下载工具、同步基线与变更事件；不包含清华 URL、SSO、SM2 或学校客户端。`plugin/thu_learn/` 则负责清华具体实现，包括客户端和单请求适配进程。

两处插件搜索路径：先 `src/shiritaibot/plugin/<id>/`，再 `<home>/plugins/<id>/`。同名时前者优先。`core` 不作为插件扫描。`plugin.toml` 中 `api = 1`，入口 `main.py` 暴露 `Entry(Plugin)`。

插件在同一进程运行，属于可信本地代码，不是安全沙箱。工具执行由权限层控制，但插件代码自身具有宿主账号权限。只安装可信代码。宿主不会自动安装 requirements，也不会自动 clone 仓库。

## 飞书 CLI

本地目录：`src/shiritaibot/plugin/feishu/`。插件 ID：`feishu`。

在“插件”页面展开条目，点击“填入配置模板”，设置：

```toml
[[plugins]]
id = "feishu"
enabled = true
[plugins.settings]
command = ["lark-cli"]
config_dir = "/home/your-user/.lark-cli/orgs/shiritaibot"
```

先在该独立目录完成 CLI 登录和所需授权。代码每次显式设置 `LARKSUITE_CLI_CONFIG_DIR`；不会读取或迁移 AstrBot 的认证文件。

`feishu_query` 使用固定只读操作：会话列表/搜索、消息历史/搜索、线程、文档搜索/读取、任务查询。参数也使用白名单，不能注入任意 CLI flags，不能发送消息或修改文档。权限范围由该 CLI profile 的授权决定。

白名单来源是本机 AstrBot 飞书插件的 `build_args` / `redact`，已读取当前源代码。历史服务器验证并不代表 ShiritaiBot 已完成真实飞书联调；CLI 升级后须重新核对命令帮助和所需 scopes。

## 清华网络学堂

本地目录：`src/shiritaibot/plugin/thu_learn/`。插件 ID：`thu_learn`。

```powershell
uv sync --extra learn
uv run --extra learn shiritaibot run
```

在 WebUI 的插件配置中直接填写学号和密码并保存，密码会自动加密。保存后的配置形式如下（`secret:...` 由程序生成，无需手工创建）：

```toml
[[plugins]]
id = "thu_learn"
enabled = true
[plugins.settings]
username = "你的学号"
password = "secret:自动生成的凭据标识"
poll = false
poll_seconds = 900
```

`command` 留空时，该插件自动使用当前 Python 解释器启动自己目录内的 `bridge.py`。需要其他部署位置时，可以填显式 argv，例如 Python 可执行文件加 `bridge.py` 的绝对路径。

登录由模型调用插件工具完成，不再有 WebUI 专用登录面板或 `/api/learn/auth` 接口：

1. 主人在私聊中提出登录或查询需求，模型调用 `thu_learn_login()`，已有会话可直接复用。
2. 返回 `verification_required` 等状态时，模型向用户索取短信验证码，不反复调用登录。
3. 用户回复验证码后，模型调用 `thu_learn_verify_sms(code="用户提供的6位验证码")`，成功后继续原查询。仅用户明确要求重发时使用 `thu_learn_login(resend_sms=true)`。

这两个工具仅限主人私聊 / WebUI 的普通对话入口，遵守禁用工具和作用域策略，群聊、好友与后台任务均不可调用。密码由插件从加密配置读取并通过 stdin 传给适配端，不作为模型参数；验证码按对话工具流程进入当前模型上下文与会话记录，活动日志不记录验证码或密码。Cookie / 设备指纹仍保存到 `<home>/plugin_data/thu_learn/session.json`；不复用服务器已有账号或 Cookie。

`learn_query` 支持课程、公告、未交作业、课件和作业附件列表；`learn_download` 支持下载课件/附件到插件私有目录。没有作业提交功能。

轮询默认关闭。启用后首次成功同步只建立基线；后续按作业 ID 和内容指纹生成 created / updated 事件，交给 Proactive Engine。同步失败生成诊断事件。当前只轮询作业，不轮询公告和课件。

`client.py` 由用户服务器的 AstrBot 网络学堂插件移植，保留学校相关登录、课程分页、附件归属检查和下载限额。该兼容客户端仍使用同步 requests，但运行在单独子进程中；主程序通过 asyncio 调用、设置时限并取消子进程，不会阻塞主事件循环。

## OneLearn 子集

当前约定定义在 `learn/protocol.py`：一个进程接收 stdin 上的 UTF-8 JSON 请求，处理一个操作，向 stdout 返回一个 JSON 对象后退出。凭据走 stdin，不放在 argv。

请求包括 `version=1`、`operation`、`params`、`owner`、`config` 和 `data_dir`。响应包括 `ok`、`data`，或 `status` / `message`。这是可运行的最小接入子集，**不是最终 OneLearn 标准的一致性实现**；设计中提到的 `docs/LEARN_PROTOCOL.md` 未出现在本次工作区。暂未实现长连接 hello/capabilities、WebSocket、多学校聚合和写操作。

## 编写普通插件

将 `examples/plugins/hello_world` 复制到 `src/shiritaibot/plugin/hello_world` 或 `<home>/plugins/hello_world`。在 WebUI 添加 ID 为 `hello_world` 的插件并启用。

```python
from shiritaibot.plugin_api import Plugin, schema

class Entry(Plugin):
    async def start(self):
        self.context.register_tool("my_query", "工具说明", schema({}), self.query)

    async def query(self, ctx, args):
        return {"answer": "hello"}
```

`PluginContext` 提供独立数据目录、配置、凭据解析、事件发出和工具注册。学校适配插件可继承公共 API 导出的 `OneLearnPlugin`，只补充自己的适配进程入口。插件退出时必须在 `close()` 中取消自建任务。

手动热重载会先验证新入口和工具 schema，再替换旧实例；失败时保留原实例。正在执行的同插件工具通过锁与重载互斥。主程序重构不要求插件导入内部服务。
