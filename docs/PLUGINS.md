# 插件与 OneLearn

## 目录约定

`src/shiritaibot/plugin/core/` 是主仓库的插件宿主；`src/shiritaibot/plugin/` 下除 `core/`、通用的邮件插件 `email/`、GitHub 插件 `github/` 和开发公告插件 `srtb_plugin_devs_notice/` 外，其余目录都被 `.gitignore` 忽略，飞书和网络学堂需在部署环境中单独安装实际代码与清单。干净 checkout 不含这两个插件。旧的 `integrations/`、`builtin_plugins/` 已撤销。

`src/shiritaibot/learn/` 属于核心代码：定义学校无关的请求/响应、stdio 接入、查询和下载工具、同步基线与变更事件；不包含清华 URL、SSO、SM2 或学校客户端。`plugin/thu_learn/` 则负责清华具体实现，包括客户端和单请求适配进程。

两处插件搜索路径：先 `src/shiritaibot/plugin/<id>/`，再 `<home>/plugins/<id>/`。同名时前者优先。`core` 不作为插件扫描。目录名就是插件 ID（`[a-z0-9_]{1,64}`）。

插件 API 有两个版本，按目录里的清单文件区分：

| | API v2（推荐） | API v1（旧版，兼容保留） |
|---|---|---|
| 清单 | `metadata.yaml` | `plugin.toml`（`api = 1`） |
| 配置定义 | `_conf_schema.json`（AstrBot 格式） | `plugin.toml` 的 `[settings_schema]`（JSON Schema） |
| 入口 | `main.py` 中唯一的 `shiritaibot.api.Plugin` 子类 | `main.py` 的 `Entry(shiritaibot.plugin_api.Plugin)` |
| 生命周期 | `initialize()` / `terminate()` | `start()` / `close()` |
| 能力 | 指令、消息监听、LLM 工具装饰器、Agent / 发送 / 主动性引擎 hook、KV 存储，以及 v1 的全部能力 | 工具、事件、通知渠道、子进程环境变量等（见下文“旧版 API”） |

两个文件都存在时按 v2 处理。内置的 email、github 插件已迁移到 v2；部署环境的飞书、网络学堂仍是 v1，无需修改即可继续使用。

启动时扫描上述目录，自动为有效插件补齐配置，保留已有启用状态和配置值。新发现的插件默认停用，配置后启用并保存。无效清单或加载失败会显示在 WebUI 中，不阻断其他插件。

插件列表只展示磁盘上实际扫描到的插件。配置中残留的 ID 不会生成插件条目，也不显示“未安装”占位项；对应设置保留供恢复代码后使用。手动重载时会停止目录或清单已消失的运行实例，并撤销工具和通知通道。每个插件的清单必须与实际 `main.py` 一起部署，只创建清单不能恢复缺失的实现。

WebUI 的“安装插件”支持上传 ZIP 或填写公开 GitHub 仓库地址（下载默认分支的 ZIP）。插件安装到 `<home>/plugins/<id>/`；ZIP 可以直接包含 `plugin.toml` 与 `main.py`，或套一层仓库目录。同名安装会被拒绝。“移除”会先弹出确认，确认后停止插件、撤销工具和通知通道、删除代码目录与配置并更新列表；同名的两个搜索路径都会清理，插件数据目录与历史记录保留。“重载插件”会重新扫描全部目录、补齐配置并重载；也可单独重载当前插件。重载使用已保存的配置。

插件在同一进程运行，属于可信本地代码，不是安全沙箱。工具执行由权限层控制，但插件代码自身具有宿主账号权限。只安装可信代码。宿主不会自动安装 requirements。

## 工具安全声明与会话权限

`@hook.llm_tool(..., risk=...)` 和 `Context.register_tool` 的 `risk` 为必填参数；直接构造 `Tool` 也必须提供。有效值为 `safe`、`normal`、`sensitive`、`dangerous`，仅 `safe` 属于安全工具。遗漏或无效值会使插件加载失败，失败重载保留旧实例；不得把带副作用的工具标记为 safe。

```python
self.context.register_tool("read_status", "读取状态", schema({}), self.read_status, risk="safe")
self.context.register_tool("send_notice", "对外发送", parameters, self.send_notice, risk="sensitive")
```

Plugin Manager 在替换旧实例前验证所有工具的安全等级和 Schema，插件列表 API 暴露每个已注册工具的 `name/risk/safe`；WebUI 的工具目录展示来源和安全性。每个 Agent 会话可将单个工具设为禁止、人工审批、模型自动审批或无需审批。工具安全等级仅决定预设的默认权限，不能替代执行层授权。插件工具默认仅在 Agent 模式可用；只读、属于基本对话能力的工具可在 `register_tool(..., chat=True)` 中声明，使其也出现在 Chat 会话中，仍受同样的权限控制。

OneLearn 的 `learn_query` 声明 safe，`learn_download` 声明 normal。部署环境的飞书只读查询工具声明 safe；清华登录 / 短信验证工具声明 sensitive，并保留主人私聊的业务校验。邮件插件目前只注册事件轮询和通知通道，没有模型可调用工具，不需要虚构工具声明；这些生命周期任务不受模型工具审批控制。

飞书、清华插件源码仍由部署环境单独维护，本次兼容修改提供在 `examples/plugins/session-permissions.patch`，可在仓库根目录运行 `git apply examples/plugins/session-permissions.patch`（本部署已应用，无需重复）。今后新增工具均需显式声明 risk。

## 插件自行声明配置项

v2 插件在目录内的 `_conf_schema.json` 中声明配置项，格式与 AstrBot 相同，无需修改前端或宿主：

```json
{
  "token": { "type": "string", "description": "访问令牌", "hint": "加密保存", "secret": true },
  "mode": {
    "type": "string",
    "description": "模式",
    "options": ["fast", "safe"],
    "labels": ["快速", "安全"],
    "default": "safe"
  },
  "interval": { "type": "int", "description": "间隔（秒）", "slider": { "min": 60, "max": 3600, "step": 60 }, "default": 300 },
  "advanced": {
    "type": "object",
    "description": "高级设置",
    "items": { "retries": { "type": "int", "default": 3 } }
  }
}
```

| 字段 | 说明 |
|---|---|
| `type` | 必填：`string`、`text`（多行）、`int`、`float`、`bool`、`object`、`list`、`dict`、`template_list` |
| `description` / `hint` | 表单标签 / 帮助文字；`obvious_hint: true` 时醒目显示 |
| `default` | 默认值；缺省时按类型取 `""`、`0`、`0.0`、`false`、`{}`、`[]` |
| `items` | `object` 的子项，可嵌套 |
| `options` / `labels` / `subtitles` | 下拉选项、显示名、选项说明（按下标对应）；`list` 带 `options` 时为多选 |
| `slider` | `int` / `float` 滑块：`{"min", "max", "step"}` |
| `secret` | `string` 或字符串 `list`：密钥控件，**加密存入 Vault**（AstrBot 只做界面遮挡） |
| `invisible` | 不在表单中显示，值仍然保留 |
| `editor_mode` / `editor_language` | 等宽代码编辑框 |
| `template_schema` | `dict` 的可选参数模板 |
| `templates` | `template_list` 的条目模板，保存为带 `__template_key` 的对象数组；`display_item` 指定列表摘要字段 |
| `minimum` / `maximum` / `pattern` / `format` | ShiritaiBot 扩展：按 JSON Schema 校验 |

暂不支持 `file` 类型；`_special` 选择器会被忽略，按基础类型显示。宿主把这份定义转换为 JSON Schema（附带 `x-` 扩展字段，见 `plugin/core/manifest.py`），加载前校验配置，校验失败只报告字段路径。

配置值保存在主配置 `config.toml`，凭据存放于 Vault。与 AstrBot 一样，插件更新配置定义后，重载时自动为新字段补默认值、移除已删除的字段。插件代码通过 `self.config`（`PluginSettings`，`dict` 子类）读取；修改后 `await self.config.save_config()` 写回主配置，密钥字段会自动加密。

v1 插件继续在 `plugin.toml` 的 `[settings_schema]` 中使用 JSON Schema（`title`、`description`、`minimum`、`maximum`、`default`、`writeOnly`），也可用 `[settings]` 定义默认值。修改清单后点击“重载插件”即可更新表单和运行实例。

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

目录：`src/shiritaibot/plugin/email/`（随主仓库提交，API v2）。插件 ID：`email`，默认未启用。

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

## GitHub

目录：`src/shiritaibot/plugin/github/`（随主仓库提交，API v2）。插件 ID：`github`，默认未启用。

用于让 Agent 以一个**专用 bot 账号**维护仓库。建议在 bot 账号下创建 Fine-grained PAT：Repository access 选 *All repositories*（之后新建的仓库也能覆盖），按需授予 Contents、Issues、Pull requests、Workflows、Actions、Administration 的读写权限。不要使用主账号的 token。

```toml
[[plugins]]
id = "github"
enabled = true
[plugins.settings]
token = "secret:自动生成的凭据标识"   # 在 WebUI 填写，自动加密
commit_name = ""                      # 留空 = token 账号的登录名
commit_email = ""                     # 留空 = <id>+<login>@users.noreply.github.com
co_author = false                     # 开启后提交自动追加 Co-authored-by: ShiritaiBot
allowed_owners = ["shiritai-bot"]     # 留空 = 只允许 token 账号本身
proxy = ""                            # 如 socks5h://127.0.0.1:1080；留空 = API 沿用环境变量代理，git 直连
timeout = 30
```

| 工具 | risk | 内容 |
|---|---|---|
| `github_read` | safe | 账号、仓库、分支、文件、提交、Issue / PR（含评论、diff）、Actions、Release |
| `github_write` | normal | 创建、修改 Issue 和 PR，评论，Review，创建 Label |
| `github_publish` | sensitive | 合并 PR、发 Release、重跑或触发 workflow、删除非默认分支 |
| `github_admin` | dangerous | 创建仓库、修改仓库设置和 topics、设置分支保护；**不提供删除仓库** |
| `github_push` | sensitive | 把工作区本地分支推送到 GitHub；`force` 不能用于默认分支 |

所有工具仅限主人在私聊 / WebUI 的 Agent 模式调用，群聊不可用；仓库 owner 必须在 `allowed_owners` 内。Issue、PR 和文件内容视为不可信资料。

**token 不进入 shell 和子代理。** 插件只通过 `PluginContext.set_process_env` 向 shell 工具和子代理注入 `GIT_AUTHOR_*` / `GIT_COMMITTER_*`，模型在 shell 里 `git commit` 即使用 bot 身份；推送必须调用 `github_push`。该工具不在模型控制的仓库里运行 git（其配置和 hooks 可能读取凭据），而是把分支 fetch 到插件私有的裸镜像 `<home>/plugin_data/github/mirrors/`，再从镜像带 token 推送；token 经环境变量传给 git，不出现在命令行参数和工具输出中。推送子代理的改动时传 `subagent_run`。

开启 `co_author` 后，提交信息带上 `Co-authored-by: ShiritaiBot <shiritaibot@shiritaibot.dev>`，不改动 git 配置、hooks 和环境变量：

- **主 Agent shell**：插件通过 `PluginContext.add_shell_filter` 在命令获批后、执行前改写命令，给每个 `git [全局选项] commit` 加上 `-c trailer.co-authored-by.ifexists=addIfDifferent` 和 `--trailer '...'`（提交信息里已有同一行时不重复）。改写按 bash 语法识别命令位置，引号、注释和 heredoc 里的文字不会被改；需要 git ≥ 2.32。
- **工具产生的提交**：`pr_merge` 的 merge / squash 会在提交正文末尾追加该行。未指定 `commit_message` 时，按仓库的 merge / squash 提交信息设置重建 GitHub 默认正文后再追加；rebase 不产生新提交信息，返回 `co_author: false`。
- **子代理**：通过 `PluginContext.add_subagent_note` 在新启动的子代理（generic 类型除外）提示词末尾说明：如需提交就带上这一行，任务明确不提交时忽略。续接的运行不再重复注入。

局限：shell 工具与宿主以同一系统账号运行，能读到 `<home>/master.key` 和数据库的模型理论上仍可解密 Vault。以上设计防止的是 token 意外进入上下文、日志和聊天记录；如需更强的隔离，应让 shell 在沙箱或独立账号中运行，并保持 token 的最小权限。用 `env:` 引用的 token 会被所有子进程继承，请改在 WebUI 中填写。

## ShiritaiBot Devs 公告

本地目录：`src/shiritaibot/plugin/srtb_plugin_devs_notice/`。插件 ID：`srtb_plugin_devs_notice`。随主仓库发布，默认停用。

插件通过 HTTPS 轮询 `https://notice.shiritaibot.dev/api/notices?after=<cursor>`，接收 ShiritaiBot Devs 发布的开发公告。公告服务给每次发布、修改和删除分配全局递增的 `seq`，插件把读到的最大 `seq` 存在 KV 中作为游标；首次轮询只记录当前 `seq` 作为基线，不回放历史公告。

```toml
[[plugins]]
id = "srtb_plugin_devs_notice"
enabled = true
[plugins.settings]
url = "https://notice.shiritaibot.dev/api"   # 必须 https；仅本机调试允许 http://127.0.0.1 / localhost
poll = true
poll_seconds = 1200                           # 最短 600
categories = ["release", "security", "breaking", "maintenance", "plugin", "general"]
notify_updates = true                         # 公告被修改时也通知
min_importance = 2                            # 1–4
timeout = 30
```

| 类型 | 含义 |
|---|---|
| `release` | 版本发布 |
| `security` | 安全公告 |
| `breaking` | 不兼容变更 / 迁移指引 |
| `maintenance` | 服务维护 |
| `plugin` | 插件动态 |
| `general` | 一般公告 |

每条选中类型的新公告（或修改后的公告）提交一个事件：`source = "devs_notice"`，`kind` 为 `devs_notice.published` / `devs_notice.updated`，`dedup_key = notice:<id>:r<revision>`；公告级别 `info` / `important` / `critical` 对应 urgency `normal` / `high` / `critical`。删除的公告和未选中的类型只推进游标。插件的 `on_proactive_judged` 把分诊重要性提升到不低于 `min_importance`，`critical` 公告固定为 4 级，因此公告不会在分诊时被丢弃，一定会进入 Proactive Loop。公告正文作为不可信资料放在 payload 中，并注明不是对助手的指令。

插件不注册模型工具，也不需要凭据。发布公告使用 ShiritaiBot Devs 内部的独立服务和插件，不随主仓库分发。

## 主动性引擎

事件源统一调用 `PluginContext.emit(event)`、`emit_many(events)`、`get_event(receipt_id)`；宿主提供同名 API，HTTP 分别是认证后的 `POST /api/events`、`POST /api/events/batch`、`GET /api/events/{id}`。事件包含来源事件 `id`、事件名 `kind`、`source`、带时区的 `occurred_at`、标题 `title`、内容 `payload`、稳定的 `dedup_key`、参考值 `urgency`，以及可选的 `actors`（事件涉及的人：`identity` 如 `email:someone@example.edu` / `qq:12345`，`name`，`role` 如 `sender`）。`actors` 只是原始身份数据：插件不调用任何记忆接口，事件处理完后由核心统一决定是否写入长期记忆（见 [MEMORY.md](MEMORY.md)）。`id` 可由入口自动补齐。返回的接收记录 ID 按 `(source, dedup_key)` 稳定去重；提交不代表处理完成，状态查询可查看分类和执行结果。

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

## 编写插件（API v2）

v2 的结构参照 [AstrBot 插件](https://docs.astrbot.app/dev/star/plugin-new.html)：`metadata.yaml` + `_conf_schema.json` + `main.py`，插件类继承 `Plugin`，用 `hook` 装饰器注册指令、监听器、工具和各类 hook。完整示例见 `examples/plugins/hello_world`：把它复制到 `<home>/plugins/hello_world`（或打包成 ZIP 在 WebUI 安装），点击“重载插件”，启用并保存即可。

所有导入都来自 `shiritaibot.api`，这是 v2 唯一受支持的导入边界；主程序其他模块随时可能重构。

### 清单 metadata.yaml

```yaml
name: hello_world            # 展示名的后备；插件 ID 始终是目录名
display_name: Hello World    # 插件列表中的名称
desc: 插件说明
short_desc: 一句话简介        # 侧栏显示
version: 1.0.0
author: you
repo: https://github.com/you/repo
support_platforms: [qq, web] # 仅展示
shiritaibot_version: ">=0.2,<1"   # 可选；不满足时插件显示为加载失败
```

宿主不会自动安装 `requirements.txt`。

### 插件类

```python
from shiritaibot.api import Context, MessageEvent, Plugin, PluginSettings, hook


class MyPlugin(Plugin):
    def __init__(self, context: Context, config: PluginSettings):
        super().__init__(context, config)   # 不需要配置时可以只接收 context

    async def initialize(self):             # 加载时调用，适合启动后台任务
        ...

    async def terminate(self):              # 停用、重载、移除、退出时调用，必须取消自建任务
        ...

    @hook.command("hello")
    async def hello(self, event: MessageEvent, name: str = ""):
        """打个招呼"""                       # 第一行是 /help 中的说明
        yield event.plain_result(f"你好 {name or event.get_sender_name()}")
```

`main.py` 中必须恰好定义一个 `Plugin` 子类（可以有中间基类，取最末端的那个）。实例上可用：`self.config`（配置）、`self.context`（宿主服务）、`self.name`（插件 ID）、`self.data_dir`（`<home>/plugin_data/<id>/`，大文件放这里）、`self.logger`，以及每个插件独立的 KV 存储 `await self.get_kv_data(key, default)` / `put_kv_data(key, value)` / `delete_kv_data(key)`（值需可 JSON 序列化）。

### 指令

| 装饰器 | 作用 |
|---|---|
| `@hook.command(name, aliases={...}, priority=0)` | `/name 参数…`；参数按处理函数签名绑定，`int` / `float` / `bool` 自动转换，最后一个 `str` 参数吃掉剩余文本，`*args` 接收全部剩余参数；缺参数或类型不对时回复用法 |
| `grp = hook.command_group(name)` + `@grp.command(sub)` / `grp.group(sub)` | 指令组 `/name sub 参数…`，可多层嵌套；只输入组名时回复子指令列表 |
| `@hook.permission(Permission.OWNER)` | 仅主人可用；其他人调用会收到提示 |
| `@hook.message_type(MessageType.PRIVATE)` / `@hook.platform(Platform.QQ)` | 与指令叠加时只在对应场景匹配 |

处理函数的前两个参数是 `self, event`。回复方式与 AstrBot 相同：`yield event.plain_result(...)`（可多次）、`return` 结果或字符串、或 `await event.send(...)`。指令在后台执行（上限 10 分钟），不阻塞后续消息；执行记录写入会话历史，但不进入模型上下文。

插件指令不能与内置指令（`/help`、`/status`、`/stop` 等）或其他插件的顶层指令重名，冲突时插件加载失败。内置指令、“停 / 取消”和审批回复始终由宿主处理，插件看不到也拦不住。目前只支持文本回复，不支持图片、文转图等富消息。

### 消息监听

| 装饰器 | 作用 |
|---|---|
| `@hook.message_type(MessageType.ALL)` | 每条消息都调用；`GROUP` / `PRIVATE` 只看群聊或私聊 |
| `@hook.regex(pattern)` | 文本匹配 `re.search` 时调用 |

监听器按 `priority` 从高到低依次执行，单个上限 30 秒。调用 `event.stop_event()` 后，后续监听器不再执行，消息也不会交给模型。群里没有 @ 机器人、也不含唤醒词的消息同样会送达监听器（`event.is_wake` 为 False），可用于统计或关键词触发；这类消息原本就不会交给模型。

`MessageEvent` 字段：`session`（统一会话 ID，可传给 `Context.send_message`）、`text`（用户输入的纯文本）、`sender_id`、`sender_name` / `get_sender_name()`、`platform`（`qq` / `web`）、`adapter`、`group_id`、`is_group`、`is_private`、`is_owner`、`is_wake`、`message_id`、`segments`（OneBot 消息段）、`raw`（原始事件）。修改这些字段不会改变模型看到的内容，要改模型输入请用 `on_llm_request`。

### LLM 工具

```python
@hook.llm_tool("weather", risk="safe", chat=True, timeout=30)
async def weather(self, ctx: ToolContext, city: str, days: int = 1):
    """查询天气。

    Args:
        city(string): 城市名
        days(integer): 天数
    """
    return {"city": city, "forecast": "晴"}
```

参数 schema 的来源依次为：显式的 `parameters=`、docstring 的 `Args:`（类型可写 `string`、`number`、`integer`、`boolean`、`object`、`array`、`array[string]`），以及类型注解（与 AstrBot 不同，这里会读取注解）。没有默认值的参数是必填参数。`ctx` 是工具调用上下文，包含 `session`、`actor`、`owner`、`group`、`mode` 等字段。`risk` 规则见上文。也可以用 `self.context.register_tool(...)` 或 `self.context.add_tools(Tool(...))` 动态注册。

### Agent 生命周期 hook

| 装饰器 | 处理函数参数 | 可做什么 |
|---|---|---|
| `on_agent_begin` | `(turn)` | 观察 |
| `on_llm_request` | `(turn, request)` | 每次请求模型前调用：修改 `request.system_prompt`、`request.messages`、`request.tools`，或用 `request.add_context(text)` 给最新一条用户消息追加内容；改动只影响这一次请求，不写入历史 |
| `on_llm_response` | `(turn, response)` | 改写 `response.text`（会写入历史并发送） |
| `on_tool_call` | `(turn, name, arguments)` | 观察；在权限检查之前调用 |
| `on_tool_result` | `(turn, name, arguments, result)` | 观察 |
| `on_agent_done` | `(turn, result)` | 观察 `result.status`（`completed` / `failed` / `cancelled`）、`result.steps`、`result.text` |

`Turn` 包含 `id`、`session`、`actor`、`owner`、`group`、`background`、`mode`、`route`、`text`；`turn.proactive` 和 `turn.event_id` 用于识别 Proactive Loop。这些 hook 作用于主循环和 Proactive Loop，不作用于子代理、分诊和记忆抽取等内部模型调用。

### 发送 hook

| 装饰器 | 处理函数参数 | 可做什么 |
|---|---|---|
| `on_decorating_result` | `(message)` | 消息进入发送队列前调用：改写 `message.text`，或 `message.cancel()` 取消发送 |
| `after_message_sent` | `(message)` | 消息进入发送队列后，在后台调用 |

覆盖所有发往 QQ 和 WebUI 的文本，包括模型回复、指令输出和主动通知。权限审批提示除外，插件既不能改写也不能取消。

### 主动性引擎 hook

| 装饰器 | 时机 | 可做什么 |
|---|---|---|
| `on_proactive_event(event)` | 事件开始处理、分诊之前 | 修改 `event.title` / `payload` / `urgency`；`event.drop(reason)` 直接丢弃 |
| `on_proactive_judged(event, triage)` | 分诊完成后 | 修改 `triage.importance`（0 为丢弃）、`decision`、`message`、`reason`、`prepare` |
| `on_proactive_done(event, outcome)` | Proactive Loop 结束、通知入队前 | 改写 `outcome.message` / `reason`；把 `outcome.notify` 设为 False 取消通知 |
| `on_notification_sent(source, text)` | 通知已发给主人后 | 观察，例如转发到其他渠道（v1 的 `register_notifier` 同样可用） |

宿主保留兜底规则。主人设定的提醒和定时任务、主人委派的子代理结果属于 `event.protected`：它们不能被丢弃，分诊仍有最低级别，委派结果始终为 4 级。重要性 4 和主人设定的提醒标记为 `outcome.forced`，此时只能改写内容，不能取消。修改结果无效时（例如 importance 超出 0–4、message 为空或超过 4000 字），宿主忽略该插件的修改并记录警告。只读字段 `source`、`kind` 可以用于判断事件类型。

### 宿主服务 Context

`self.context` 是 `Context`，包含 v1 `PluginContext` 的全部能力（见下文“旧版 API”），另外提供：

| 方法 | 说明 |
|---|---|
| `await send_message(session, text)` | 主动向会话推送文本（会经过发送 hook） |
| `await notify_owner(text)` | 直接给主人发消息，不经过分诊、免打扰和节流 |
| `await emit(ExternalEvent(...))` / `emit_many` / `get_event` | 向主动性引擎提交事件（见下文“主动性引擎”） |
| `await llm_generate(prompt, messages=None, system="", route="light")` | 一次不带工具的模型调用，返回文本 |
| `await decide(state, questions, route="light")` | 向决策模型提出结构化问题；未配置决策模型时返回 None |
| `get_plugins()` | 已加载插件的元数据 |
| `get_platform(adapter_id)` | OneBot 协议端实例；`await platform.call(action, params)` 可直接调用协议端 API |

### 运行约定

- 所有处理函数和 hook 都不经过插件的重载锁，慢工具不会阻塞消息处理。插件被重载或停用后，旧实例的处理函数不再被调用。
- hook 抛出异常或超时（流水线 hook 10 秒，主动性引擎 hook 30 秒）只会记录警告和 `plugin.hook_failed` 活动，不影响主流程。
- 多个插件的同类 hook 按 `priority`（默认 0）从高到低执行；优先级相同时按加载顺序执行，后一个插件看到的是前一个修改后的对象。
- 插件列表 API 和 WebUI 会展示每个插件的版本、作者、指令、工具（含 risk）和 hook。

## 旧版 API（v1）

`plugin.toml`（`api = 1`）+ `main.py` 中的 `Entry(Plugin)`，从 `shiritaibot.plugin_api` 导入。这一版本继续受支持，但新插件请使用 v2。

```python
from shiritaibot.plugin_api import Plugin, schema

class Entry(Plugin):
    async def start(self):
        self.context.register_tool("my_query", "工具说明", schema({}), self.query, risk="safe")

    async def query(self, ctx, args):
        return {"answer": "hello"}

    async def close(self):
        ...
```

v1 的 `PluginContext` 提供以下能力（v2 的 `Context` 同样具备）：

- 独立数据目录、配置（`settings`）、凭据解析（`resolve_secret`）、日志。
- 事件发出、工具注册、主动通知渠道注册（`register_notifier`）。
- `set_process_env(env)`：向 shell 工具和子代理进程添加**非敏感**环境变量。不同插件不能设置同名变量。
- `add_subagent_note(text)`：在新启动的非 generic 子代理提示词末尾追加说明。
- `add_shell_filter(rewrite)`：注册一个同步、无副作用的 `rewrite(command) -> command`，在 shell 工具命令获批后、执行前改写命令。过滤器出错时被忽略，原命令照常执行。
- `session_workspace(session)`：返回会话的 shell 工作目录。

以上环境变量、子代理说明和 shell 过滤器都会在插件重载或停用时撤销。v1 工具调用与插件重载通过锁互斥。

学校适配插件可继承 `shiritaibot.plugin_api` 导出的 `OneLearnPlugin`，只需补充自己的适配进程入口；插件退出时必须在 `close()` 中取消自建任务。

## 日志

`self.logger`（v1：`self.context.logger`）是宿主 `LogManager` 的 `Logger`，来源固定为 `插件:<id>`。它同时输出到彩色终端、滚动日志文件 `data/logs/shiritaibot.log` 和 WebUI「日志 → 运行日志」：

```python
log = self.logger
log.debug("仅在 --log-level debug 时显示")
log.log("收到 3 封新邮件")             # [LOG][时间][插件:email]: 收到 3 封新邮件
log.warning("收件失败（TimeoutError）", "INBOX")  # 额外参数是附加标签：[插件:email][INBOX]
log.error("同步出错", exc=exc)          # exc 会把堆栈写入日志文件
```

需要更多来源时可用 `get_logger("名称")` 或 `logger.bind("标签")`；`brief(value, limit)` 把字符串、字典或列表压成一行可读文本。请写人话摘要，不要直接记录原始 JSON、凭据或完整正文。

手动热重载会先验证新入口、工具 schema 和指令冲突，再替换旧实例；失败时保留原实例。
