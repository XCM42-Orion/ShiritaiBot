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
4. **协议连接**：添加 OneBot 正向 WS 实例，填写 NapCat Universal WebSocket URL、Bot QQ、主人 QQ 和访问令牌。群聊、非主人好友只有加入对应白名单后才回复，群聊通过 `@Bot` 或已配置的唤起关键词触发。
5. **设置**中直接填写 Tavily API Key。Key 可用时才注册 `web_search`。插件密码和访问令牌也在插件配置中直接填写。
6. **子代理**中分别添加 Codex / Claude Code / Generic CLI 实例。命令数组每行一个参数。通过对话委派，审批后启动；结果在子代理页查看。只实现 CLI 一次性任务，不支持原生会话续接或逐步审批桥接。

危险工具在 WebUI 审批，也可在主人私聊回复 `同意 <审批码>` / `拒绝 <审批码>`。`/stop` 取消当前任务。群聊始终为 Chat 模式，即使说话者是主人，也不能使用私人记忆、日程或系统工具。

### 提供商与模型参数

- 提供商的 `max_retries` 是首次请求失败后的额外重试轮数，默认 2，范围 0–10；0 表示不重试。请求超时和 HTTP 429 / 500 / 502 / 503 / 504 会退避重试，其他错误交给候选模型回退。`timeout` 是每次网络请求的超时；Agent 整轮仍受全局时间上限约束。
- `proxy` 支持 `http://`、`https://`、`socks5://`、`socks5h://`，同时用于模型列表、当前配置测试和正式调用。留空沿用进程默认网络环境（包括环境变量代理）；填写后使用该提供商的独立代理。
- 模型的 `extra_body` 支持 JSON 对象，例如 `{"temperature":0.7,"top_p":0.9}`。使用协议原生字段，Gemini 示例为 `{"generationConfig":{"temperature":0.7}}`。嵌套对象递归合并，其他值覆盖默认值；不能替换模型、对话、工具定义或开启流式响应。Chat Completions 指定 `max_tokens` 时会移除默认的 `max_completion_tokens`，避免字段冲突。界面保留未完成的 JSON 并提示错误，非法 JSON 不能测试或保存。为保留 `null`，写入 TOML 时该字段编码为 JSON 字符串，读取时恢复成对象。
- `max_context` 表示输入与预留输出合计的 token 上限；默认 0（未指定），非零时须大于最大输出。当前采用 ASCII 约 4 字符/token、其他字符约 1 字符/token 的估算，包含系统提示、工具和不透明推理状态，不等同于各模型的精确 tokenizer。估算超限会跳过该模型并尝试路由中的下一项，不截断工具调用历史。
- `modalities` 记录输入模态：`text`、`image`、`audio`、`video`，至少一项，默认仅文本。这是能力配置；当前消息传输仍以文本为主，不会因勾选而自动开启媒体处理。

以上参数均可在 **模型与服务** 中编辑并保存。**测试当前配置**使用未保存的参数，测试过程中显示转圈并禁用按钮，成功或失败后均恢复。旧配置缺省值自动兼容，无需迁移。

## 斜杠命令与数据看板

QQ 和 WebUI 共用命令入口。WebUI 输入 `/` 可查看命令菜单。命令直接由程序执行，不调用模型。

| 命令 | 行为 |
|---|---|
| `/help` | 命令帮助 |
| `/status` | 当前会话、模式、模型、执行状态和排队数 |
| `/models`、`/model` | 可用模型列表、当前模型 |
| `/model GLM-5.3-Flash` | 主人为当前会话选择模型，下一轮生效；`/model default` 恢复全局路由 |
| `/personas`、`/persona` | 可用人格的名称和标识、当前人格；不会显示提示词 |
| `/persona 标识` | 主人为当前会话选择人格，下一轮生效；`/persona default` 恢复全局默认人格 |
| `/new`、`/clear` | 主人停止当前会话并开启新上下文；旧消息留库，长期记忆保留 |
| `/stop`、`/cancel` | 停止当前任务并清理本人排队消息；主人可清理当前会话全部排队消息 |
| `/tools`、`/permissions` | 当前会话可用工具及审批要求，不修改权限 |
| `/tasks` | 最近子代理任务，仅主人私聊 / WebUI |
| `/memory [关键词]` | 查看或搜索长期记忆，仅主人私聊 / WebUI |
| `/approve 编号`、`/deny 编号` | 同意 / 拒绝一次审批，仅主人私聊 / WebUI |

**概览 → 数据看板**显示过去 24 小时、3 天、7 天的逐小时输入/输出 tokens、用户消息和助手回复，以及总 tokens、消息数、模型调用数、失败轮次。使用北京时间，首尾小时只统计所选滚动区间；每 30 秒刷新。统计来自已有 SQLite 数据，无需重新开始积累。

Token 使用提供商返回值，包含工具轮次与连接测试；未返回用量的请求不估算。消息统计已入库的用户消息和最终回复，含斜杠命令，不含工具结果、中间工具调用回复与内部交接。清理上下文不会清除统计。费用估算尚未实现。

**权限与审批 → 作用域工具白名单**中的作用域就是会话匹配规则。例如 `group:qq-main:*` 匹配该 OneBot 实例所有群，`private:qq-main:123456` 匹配与该账号的私聊，`web:*` 匹配所有 WebUI 会话。每行填一个允许的工具名；空列表禁用全部工具，多条匹配规则取交集，不能绕过群聊权限或全局禁用规则。

## 人格、唤起和上下文

在 **设置** 中配置助手称呼、对主人的称呼、多个命名人格及默认人格。完整人格提示词仅在受管理令牌保护的 WebUI 中编辑；QQ 和 WebUI 的人格命令只返回名称与标识。旧配置的 `persona` 继续作为基础人设使用。人格与模型的会话选择分别保存，删除或停用已选人格后自动使用全局默认人格。

唤起关键词每行一个，消息包含任意关键词即可触发；群聊也可通过 @ 触发，白名单和忽略规则仍有效。主人私聊默认不需要关键词，可开启“主人私聊 Agent 需要关键词”，开启时须至少配置一个关键词。斜杠命令、停止和审批指令始终可用，WebUI 不受关键词限制。

上下文支持 `discard`（超限丢弃旧轮次，默认）和 `compress`（压缩旧轮次为摘要）。`context.max_turns` 默认 50，包含即将开始的一轮；一轮内的工具调用、工具结果和插话整体保留或处理。字符上限沿用 `policy.max_context_chars`。压缩时保留 `context.keep_recent_turns` 个最近完整轮次（默认 10，须小于最大轮数）；字符空间不足时会压缩更多旧轮次。`context.compression_model` 可指定已启用的模型标识，留空使用 `light` 路由。摘要与裁剪位置持久化，后续压缩会合并已有摘要。

**压缩和丢弃只影响模型上下文，原始消息独立保留在数据库中。** `/new` 会重置上下文和摘要，但保留原始历史。Agent 的 `history_query` 工具可查询当前会话的全部原始对话，包含已压缩、丢弃和 `/new` 之前的消息：支持关键词搜索、`before_seq` 分页，以及通过 `message_seq` + `offset` 分段读取长消息。工具不跨会话读取，群聊和好友 Chat 模式不能调用，并遵守现有工具权限策略。摘要会使用配置的助手称呼，原始记录保留当时的说话者称呼。

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
web/                     React 控制台（Vite）
  src/api/               类型化 API 客户端与后端数据模型
  src/pages/             每个管理视图一个页面组件
  src/components/        共享界面组件
  src/hooks/             路由、配置草稿、通知与实时连接
  src/styles/            设计变量与分层样式
tests/                   核心和模拟集成测试
examples/plugins/        最小插件示例
```

具体插件按项目约定 **保留在本地、默认不纳入主仓库 Git**，通用的 email 插件随主仓库提交。启动时自动扫描插件子目录并注册配置项，旧配置也会补齐 email。插件的字段定义与默认值放在各自的 `plugin.toml` 中，由 WebUI 动态生成表单。WebUI 支持上传 ZIP、从公开 GitHub 仓库安装、确认后删除插件，以及手动扫描和热重载。新插件默认停用，配置后启用并保存；也可以直接放到 `<home>/plugins/` 后点击“重载插件”。飞书和网络学堂接入方式见 [插件说明](docs/PLUGINS.md)。

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
npm --prefix web run format:check
uv run shiritaibot doctor
```

如果 Windows 的系统临时目录不可写，创建 `.local`，使用全新的测试临时目录，例如 `uv run pytest -q --basetemp=.local/pytest-check-01`。pytest 会清理所指定目录，因此不要指向工作文件或已有数据。
