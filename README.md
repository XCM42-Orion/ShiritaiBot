# ShiritaiBot

面向个人的事件驱动 Agent。Python / asyncio 后端，SQLite 持久化，TypeScript + React + Vite 管理界面。

这是根据 [设计草案](docs/DESIGN.md) 落地的 **0.1 基础实现**。各模块已有运行路径，完整设计中的高级功能尚未全部实现，范围见 [实现状态](docs/IMPLEMENTATION.md)。

## 启动

需要 Python 3.12+、uv、Node.js 20.19+（本机验证使用 Python 3.13、Node 24）。在项目根目录：

```powershell
uv sync
npm --prefix web ci
npm --prefix web run build
uv run shiritaibot run
```

访问 `http://127.0.0.1:8765`。另开终端执行下面的命令取得管理令牌，在登录页面粘贴；不要把令牌发到聊天或提交到 Git。

```powershell
uv run shiritaibot token
```

默认不连接外部账号、不调用付费模型，Shell 和主动性引擎关闭。可以用 `uv run shiritaibot run --home <目录> --port 8765` 选择数据目录。仅监听本机；远程使用可通过 SSH 端口转发。

开发前端时运行 `npm --prefix web run dev`，Vite 会将 `/api` 代理到 8765。

## 首次配置

1. **模型与服务**：左侧新增提供商，右侧直接填写 API Key。保存时自动加密，已有密钥不会回显；留着“已配置”不动可保留原值，输入新值替换，也可点击清除。旧版配置中的 `secret:` / `env:` 引用仍兼容，无需手工迁移。
2. 服务地址包含协议前缀：Chat Completions / Responses / Anthropic 通常以 `/v1` 结尾；Gemini 以 `/v1beta` 结尾。点击 **获取提供商模型**，再点模型右侧的 **＋** 添加；也可手动填写。模型标识支持 `GLM-5.3-Flash`。远端名称中的斜杠会转换为本地标识的短横线，实际请求仍保留原始模型名。
3. 首个模型自动填入空的 `agent`、`chat`、`light` 路由。左侧 **模型路由** 可调整主模型和备用顺序。**测试当前配置**直接使用表单中的提供商、密钥和模型，无需先保存，也不会替你保存；测试会发出一次真实 LLM 请求。
4. **协议连接**：添加 OneBot 正向 WS 实例，填写 NapCat Universal WebSocket URL、Bot QQ、主人 QQ 和访问令牌。群聊、非主人好友只有加入对应白名单后才回复，群聊需要 `@Bot`。
5. **设置**中直接填写 Tavily API Key。Key 可用时才注册 `web_search`。插件密码和访问令牌也在插件配置中直接填写。
6. **子代理**中分别添加 Codex / Claude Code / Generic CLI 实例。命令数组每行一个参数。通过对话委派，审批后启动；结果在子代理页查看。只实现 CLI 一次性任务，不支持原生会话续接或逐步审批桥接。

危险工具在 WebUI 审批，也可在主人私聊回复 `同意 <审批码>` / `拒绝 <审批码>`。`/stop` 取消当前任务。群聊始终为 Chat 模式，即使说话者是主人，也不能使用私人记忆、日程或系统工具。

## 斜杠命令与数据看板

QQ 和 WebUI 共用命令入口。WebUI 输入 `/` 可查看命令菜单。命令直接由程序执行，不调用模型。

| 命令 | 行为 |
|---|---|
| `/help` | 命令帮助 |
| `/status` | 当前会话、模式、模型、执行状态和排队数 |
| `/models`、`/model` | 可用模型列表、当前模型 |
| `/model GLM-5.3-Flash` | 主人为当前会话选择模型，下一轮生效；`/model default` 恢复全局路由 |
| `/new`、`/clear` | 主人停止当前会话并开启新上下文；旧消息留库，长期记忆保留 |
| `/stop`、`/cancel` | 停止当前任务并清理本人排队消息；主人可清理当前会话全部排队消息 |
| `/tools`、`/permissions` | 当前会话可用工具及审批要求，不修改权限 |
| `/tasks` | 最近子代理任务，仅主人私聊 / WebUI |
| `/memory [关键词]` | 查看或搜索长期记忆，仅主人私聊 / WebUI |
| `/approve 编号`、`/deny 编号` | 同意 / 拒绝一次审批，仅主人私聊 / WebUI |

**概览 → 数据看板**显示过去 24 小时、3 天、7 天的逐小时输入/输出 tokens、用户消息和助手回复，以及总 tokens、消息数、模型调用数、失败轮次。使用北京时间，首尾小时只统计所选滚动区间；每 30 秒刷新。统计来自已有 SQLite 数据，无需重新开始积累。

Token 使用提供商返回值，包含工具轮次与连接测试；未返回用量的请求不估算。消息统计已入库的用户消息和最终回复，含斜杠命令，不含工具结果、中间工具调用回复与内部交接。清理上下文不会清除统计。费用估算尚未实现。

**权限与审批 → 作用域工具白名单**中的作用域就是会话匹配规则。例如 `group:qq-main:*` 匹配该 OneBot 实例所有群，`private:qq-main:123456` 匹配与该账号的私聊，`web:*` 匹配所有 WebUI 会话。每行填一个允许的工具名；空列表禁用全部工具，多条匹配规则取交集，不能绕过群聊权限或全局禁用规则。

Shell 在会话工作目录运行，默认 `bash --noprofile --norc -c`。**工作目录不是系统沙箱**，经批准的命令拥有 Bot 系统账号的权限。Windows 可配置 Git Bash 的原生 `bash.exe`；外部 Agent 请使用原生可执行文件，或 `node` 加 JS 入口，不接收 `.cmd/.bat/.ps1` 包装入口。

## 目录

```text
src/shiritaibot/
  agent/                 AgentLoop、会话队列、插话和取消
  adapters/              多实例 OneBot V11
  llm/                   Provider / 模型 / 路由和协议转换
  auth/                  权限、一次性审批、加密凭据
  tools/                 工具执行器和内置工具
  memory/                私人原子记忆、中文全文搜索
  routine/               待办、日程、提醒、cron 调度
  proactive/             事件驱动循环：判断 → 分级 → 行动，免打扰、通知、短期记忆
  subagent/              外部 Agent CLI 生命周期
  learn/                 OneLearn 通用协议、接入、同步与工具
  plugin/
    core/                插件发现、加载和热重载（进入 Git）
    email/               邮件插件：IMAP 收件作为事件源，可选 SMTP 通知（进入 Git，默认未启用）
    feishu/              本地飞书 CLI 插件（Git 忽略）
    thu_learn/           本地清华网络学堂插件（Git 忽略）
  plugin_api/            插件公共 API
  web/                   FastAPI 控制面
web/                     React 配置界面
tests/                   核心和模拟集成测试
examples/plugins/        最小插件示例
```

具体插件按项目约定 **保留在本地、默认不纳入主仓库 Git**。干净 checkout 只有插件核心和示例，需要单独放入插件目录；代码不会偷偷下载或安装插件。也支持在 `<home>/plugins/` 放置插件。飞书和网络学堂接入方式见 [插件说明](docs/PLUGINS.md)。

网络学堂启用后，在主人私聊中说“登录网络学堂”，模型调用 `thu_learn_login`；需要短信验证时，模型询问验证码并调用 `thu_learn_verify_sms`，成功后继续查询。登录和验证工具不向群聊、好友或后台任务开放。WebUI 只保留学号、密码等插件配置，不再提供专用登录面板。

运行数据位于 `data/`：`config.toml`、`state.sqlite3`、`master.key`、`web.token`、`workspaces/`、`files/`、`plugin_data/`。数据库中的 Provider Key 加密保存，但主密钥也在本机；备份应保护整个目录。聊天、记忆和产物本身未加密。

## 检查

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright
uv run pytest -q
npm --prefix web run build
npm --prefix web test
uv run shiritaibot doctor
```

如果 Windows 的系统临时目录不可写，创建 `.local`，使用全新的测试临时目录，例如 `uv run pytest -q --basetemp=.local/pytest-check-01`。pytest 会清理所指定目录，因此不要指向工作文件或已有数据。
