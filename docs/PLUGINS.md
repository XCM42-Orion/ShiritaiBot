# 插件与 OneLearn

## 目录约定

`src/shiritaibot/plugin/core/` 是主仓库的插件宿主；`src/shiritaibot/plugin/` 下除 `core/` 和通用的邮件插件 `email/` 外，其余目录都被 `.gitignore` 忽略，飞书和网络学堂需在部署环境中单独安装实际代码与清单。干净 checkout 不含这两个插件。旧的 `integrations/`、`builtin_plugins/` 已撤销。

`src/shiritaibot/learn/` 属于核心代码：定义学校无关的请求/响应、stdio 接入、查询和下载工具、同步基线与变更事件；不包含清华 URL、SSO、SM2 或学校客户端。`plugin/thu_learn/` 则负责清华具体实现，包括客户端和单请求适配进程。

两处插件搜索路径：先 `src/shiritaibot/plugin/<id>/`，再 `<home>/plugins/<id>/`。同名时前者优先。`core` 不作为插件扫描。`plugin.toml` 中 `api = 1`，入口 `main.py` 暴露 `Entry(Plugin)`。

启动时扫描上述目录，自动为有效插件补齐配置，保留已有启用状态和配置值。新发现的插件默认停用，配置后启用并保存。无效清单或加载失败会显示在 WebUI 中，不阻断其他插件。

插件列表只展示磁盘上实际扫描到的插件。配置中残留的 ID 不会生成插件条目，也不显示“未安装”占位项；对应设置保留供恢复代码后使用。手动重载时会停止目录或清单已消失的运行实例，并撤销工具和通知通道。每个插件的清单必须与实际 `main.py` 一起部署，只创建清单不能恢复缺失的实现。

WebUI 的“安装插件”支持上传 ZIP 或填写公开 GitHub 仓库地址（下载默认分支的 ZIP）。插件安装到 `<home>/plugins/<id>/`；ZIP 可以直接包含 `plugin.toml` 与 `main.py`，或套一层仓库目录。同名安装会被拒绝。“移除”会先弹出确认，确认后停止插件、撤销工具和通知通道、删除代码目录与配置并更新列表；同名的两个搜索路径都会清理，插件数据目录与历史记录保留。“重载插件”会重新扫描全部目录、补齐配置并重载；也可单独重载当前插件。重载使用已保存的配置。

插件在同一进程运行，属于可信本地代码，不是安全沙箱。工具执行由权限层控制，但插件代码自身具有宿主账号权限。只安装可信代码。宿主不会自动安装 requirements。

## 工具安全声明与会话权限

`PluginContext.register_tool` 的 `risk` 为必填参数；直接构造 `Tool` 也必须提供。有效值为 `safe`、`normal`、`sensitive`、`dangerous`，仅 `safe` 属于安全工具。遗漏或无效值会使插件加载失败，失败重载保留旧实例；不得把带副作用的工具标记为 safe。

```python
self.context.register_tool("read_status", "读取状态", schema({}), self.read_status, risk="safe")
self.context.register_tool("send_notice", "对外发送", parameters, self.send_notice, risk="sensitive")
```

Plugin Manager 在替换旧实例前验证所有工具的安全等级和 Schema，插件列表 API 暴露每个已注册工具的 `name/risk/safe`；WebUI 的工具目录展示来源和安全性。每个 Agent 会话可将单个工具设为禁止、人工审批、模型自动审批或无需审批。工具安全等级仅决定预设的默认权限，不能替代执行层授权。插件工具默认仅在 Agent 模式可用；只读、属于基本对话能力的工具可在 `register_tool(..., chat=True)` 中声明，使其也出现在 Chat 会话中，仍受同样的权限控制。

OneLearn 的 `learn_query` 声明 safe，`learn_download` 声明 normal。部署环境的飞书只读查询工具声明 safe；清华登录 / 短信验证工具声明 sensitive，并保留主人私聊的业务校验。邮件插件目前只注册事件轮询和通知通道，没有模型可调用工具，不需要虚构工具声明；这些生命周期任务不受模型工具审批控制。

飞书、清华插件源码仍由部署环境单独维护，本次兼容修改提供在 `examples/plugins/session-permissions.patch`，可在仓库根目录运行 `git apply examples/plugins/session-permissions.patch`（本部署已应用，无需重复）。今后新增工具均需显式声明 risk。

## 插件自行声明配置项

每个插件在自己的 `plugin.toml` 中通过 `settings_schema` 声明配置项；无需修改前端或宿主的插件配置列表。使用 JSON Schema 对象定义，例如：

```toml
api = 1
name = "My Plugin"

[settings_schema]
type = "object"

[settings_schema.properties.poll_seconds]
type = "integer"
title = "检查间隔（秒）"
description = "轮询外部服务的间隔"
minimum = 60
default = 300

[settings_schema.properties.service_key]
type = "string"
title = "服务密钥"
writeOnly = true
default = ""
```

WebUI 按字段类型生成文本、数字、开关、枚举下拉框、字符串列表及复杂对象/数组的 JSON 编辑框。`title`、`description`、`minimum`、`maximum` 用于表单展示，`default` 用于默认配置；也支持 `[settings]` 定义默认值，优先于字段的 `default`。`writeOnly = true` 的字符串用密钥控件填写并加密保存。未声明字段的老插件仍可编辑已有 settings 或完整 JSON。

配置项定义和默认值属于插件目录；用户填写的配置值及启用状态仍保存在主配置 `config.toml`，凭据存放于 Vault。运行时会合并默认值和已有设置，并校验插件声明的 schema；校验失败只报告字段路径。修改插件清单后点击“重载插件”即可更新表单和运行实例。

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

## 邮件（IMAP 收件事件源）

目录：`src/shiritaibot/plugin/email/`（随主仓库提交）。插件 ID：`email`，默认未启用。

主线是 **收件 → 统一事件 API → 预处理 → 重要性 Filter → Proactive Loop → 通知与短期摘要**：

1. 插件按 `poll_seconds` 轮询 IMAP 邮箱（只读 `EXAMINE` + `BODY.PEEK`，不改已读状态），将本次符合时间条件的邮件合并为一个 `email.received` 事件，最多进入一个 Proactive Loop。`payload` 包含 `mailbox`、`count` 和 `messages` 数组，每封保留 UID、发件人、主题、日期、服务器收件时间、正文摘要和附件名；没有符合条件的邮件则不发事件。批次按邮箱、UIDVALIDITY 和邮件标识生成稳定去重键，成功提交后持久化 UID 游标。收件失败产生 `email.sync.failed` 事件（每种错误每天一次）。
2. 事件立即唤醒主动性引擎（见下文“主动性引擎”），由 light 模型分类重要程度（0–4），0 丢弃，其余进入可调用工具的 Proactive Loop；4 级必须通知。
3. 事件流程写入 proactive_trace 审计；每个执行 Turn 由模型压缩到 Shortterm Memory，主人可通过 `proactive_recall` 回顾 Main / Proactive 的摘要。

在“插件”页面选择自动发现的 `email`，直接填写邮箱信息、启用并保存（密码自动加密）。如需恢复默认设置，可点击“填入配置模板”：

```toml
[[plugins]]
id = "email"
enabled = true
[plugins.settings]
imap_host = "imap.example.com"
imap_port = 993
imap_security = "ssl"          # ssl / starttls / none（none 仅用于本机）
username = "you@example.com"
password = "secret:自动生成的凭据标识"   # IMAP 与可选 SMTP 共用
mailbox = "INBOX"
poll_seconds = 300             # 最短 60
max_per_poll = 20
max_body_chars = 2000
time_filter = "startup"         # startup / last_poll / none / custom
filter_before = ""              # custom 模式必填，如 2026-10-28T09:00:00+08:00
# 可选：把主动通知同时发到邮箱（smtp_host 与 notify_to 都填写才启用）
smtp_host = ""
smtp_port = 465
smtp_security = "ssl"
notify_to = []
```

时间过滤按服务器 `INTERNALDATE` 精确比较（含边界），不使用可由发件人填写的 Date 头。`startup` 默认过滤本次插件启动或重载之前的邮件；`last_poll` 过滤上次成功轮询之前的邮件，时间持久化，首次无记录时使用本次启动时间；`none` 不按时间过滤，首次可读取已有邮件；`custom` 过滤带时区的 `filter_before` 之前的邮件。已处理或已过滤的 UID 不会重复扫描；`UIDVALIDITY` 改变时重新按时间条件扫描。`max_per_poll` 限制每次最多读取的候选邮件数，积压分多次轮询处理，每次仍只发一个合并事件；`last_poll` 在积压读完前保持原过滤窗口，失败不推进游标或时间。

邮件内容视为不可信资料：分诊和先期准备的提示中都声明不执行其中的指令，后台准备使用创建时的工具权限快照。分类阶段不加人设；进入 Proactive Loop 后加载配置的默认人设及助手/主人称呼，最终通知遵循人设。配置缺少 `imap_host` 或 `username` 时加载失败，错误只列字段名、不回显输入。IMAP / SMTP 使用标准库客户端，在线程中执行，不阻塞事件循环。

SMTP 通道是尽力而为的副本：在 QQ / WebUI 投递并记为已发送之后再发邮件，失败只记录 `notify.channel_failed`，不影响 IM 通知、不重发。其他插件可调用 `PluginContext.register_notifier(handler)` 接入同一通道，`handler(source, text)` 为异步函数；重载和停用时宿主自动撤销。

## 主动性引擎

事件源统一调用 `PluginContext.emit(event)`、`emit_many(events)`、`get_event(receipt_id)`；宿主提供同名 API，HTTP 分别是认证后的 `POST /api/events`、`POST /api/events/batch`、`GET /api/events/{id}`。事件包含来源事件 `id`、事件名 `kind`、`source`、带时区的 `occurred_at`、标题 `title`、内容 `payload`、稳定的 `dedup_key` 和参考值 `urgency`。`id` 可由入口自动补齐。返回的接收记录 ID 按 `(source, dedup_key)` 稳定去重；提交不代表处理完成，状态查询可查看分类和执行结果。

| 阶段 | 行为 |
|---|---|
| 入口 / 预处理 | 清理标识和标题空白、校验、去重、持久化后唤醒 PE |
| Filter | 使用独立分类提示词，不加人设；light 模型输出 importance=0–4、reason 和可选通知/查证建议；失败修正一次，再失败交给普通级 Loop 核查 |
| 分级 | 0 discard；1–4 按级别降序启动后台 Turn，同级 FIFO；不抢占运行中的任务 |
| Proactive Loop | `background:proactive:<接收记录 ID>`，复用 Agent Runtime，加载配置的默认人设及助手/主人称呼，按创建时的默认工具权限快照调用工具；最终输出 `{notify, message, reason}`，message 遵循人设 |
| 通知与交接 | Loop 完成后才发送最终 message，追加到目标 Main context；4 级强制通知并穿透免打扰、摘要延迟和节流；失败/超时发兜底并注明未完成 |
| 每轮记忆 | Main / Proactive 每个 Turn 成功、失败或取消后，持久化摘要任务，由 light 模型压缩输入、工具结果和最终消息；失败重试，不阻塞回复 |

显式 Routine 提醒/定时任务也经过 Filter，最低 3 级；显式提醒不允许静默。PE 停用时其他事件只留审计。事件源的 urgency 不能直接决定 importance。

审计存放在 `proactive_trace`（`/api/records/proactive_trace`），模型摘要存放在 `short_term`（默认保留 7 天），待重试任务在 `memory_summary`。`proactive_recall(query?, limit?)` 查询主人私聊/后台的模型摘要，不返回其他 Chat/群会话的内容。旧版本阶段日志不会被当作新的模型摘要召回。0 级事件未进入 Loop，只有 discard 审计。

## OneLearn 子集

当前约定定义在 `learn/protocol.py`：一个进程接收 stdin 上的 UTF-8 JSON 请求，处理一个操作，向 stdout 返回一个 JSON 对象后退出。凭据走 stdin，不放在 argv。

请求包括 `version=1`、`operation`、`params`、`owner`、`config` 和 `data_dir`。响应包括 `ok`、`data`，或 `status` / `message`。这是可运行的最小接入子集，**不是最终 OneLearn 标准的一致性实现**；设计中提到的 `docs/LEARN_PROTOCOL.md` 未出现在本次工作区。暂未实现长连接 hello/capabilities、WebSocket、多学校聚合和写操作。

## 编写普通插件

将 `examples/plugins/hello_world` 复制到 `src/shiritaibot/plugin/hello_world` 或 `<home>/plugins/hello_world`，在 WebUI 点击“重载插件”，选择自动发现的插件、启用并保存。也可将该目录打包成 ZIP 后通过 WebUI 安装。

```python
from shiritaibot.plugin_api import Plugin, schema

class Entry(Plugin):
    async def start(self):
        self.context.register_tool("my_query", "工具说明", schema({}), self.query)

    async def query(self, ctx, args):
        return {"answer": "hello"}
```

`PluginContext` 提供独立数据目录、配置、凭据解析、事件发出、工具注册和主动通知通道注册。学校适配插件可继承公共 API 导出的 `OneLearnPlugin`，只补充自己的适配进程入口。插件退出时必须在 `close()` 中取消自建任务。

手动热重载会先验证新入口和工具 schema，再替换旧实例；失败时保留原实例。正在执行的同插件工具通过锁与重载互斥。主程序重构不要求插件导入内部服务。
