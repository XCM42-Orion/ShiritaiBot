# 更新日志

本文件记录 ShiritaiBot 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，
版本号遵循语义化版本。

## 0.2.2 - 2026-11-4

### 新增

- **群聊水群**：Bot 可按概率、冷却时间和每群每小时上限，选择性地参与没有 @ 自己的群聊。观察轮不调用工具、
  不写入会话历史，觉得合适就自然接一句，否则输出 `<pass>` 保持沉默；自动避开免打扰时段和正在处理任务的会话。
  默认关闭，在「设置 → 群聊」开启。
- **协议端自动拉起**：协议端进程消失、连接持续失败超过设定时间后，按配置命令重新启动，五分钟内最多尝试一次，
  结果写入活动日志。默认关闭，在「设置 → 协议连接」填写启动命令。
- **首次使用清单**：概览页按「提供商 → 模型 → 路由 → 搜索密钥 → NapCat」列出待完成项并可直接跳转；
  五项全部完成后自动隐藏。
- **配置体检**：保存配置后检查密钥、模型路由、搜索密钥和调用失败情况，在概览页集中提示。

### 变更

- **自动审批提速**：一分钟内相同会话、工具、参数的批准结论直接复用；复核只携带最近 12 条、每条 1200 字符的上下文。
- **出站格式清洗**：回复发送前去掉 Markdown 加粗、标题、表格分隔线、行内代码和链接语法。
- 日志中的回复内容上限从 200 字符放宽到 1000 字符，审批记录显示模型审批耗时。

### 修复

- **私聊误拦好友**：私聊门控在白名单之外同时查询协议端好友列表，通过好友申请后立即可聊，
  无需手动维护 `friends` 配置；同意好友申请或删除好友时刷新好友缓存。
- **群聊不知道谁在发言**：群聊中触发回复的最新用户发言默认注入发言人身份
  （`[发言人 昵称(账号)]`），与其他群成员的历史记录区分开；`[group_context].identify_sender`
  可关闭。主人的发言使用主人称呼。
- 达到步数上限、记忆保存失败时给出更具体的说明；工具返回 `ok=false` 时不再记为成功。
- 配置校验错误改为中文并指出具体实例和字段；更新流程不再触碰运行中的程序文件，避免 WinError 32。
- 重复启动时明确提示已有实例在运行；保存配置时若未改动协议端设置，不再重连 NapCat。

## 0.2.1 - 2026-11-3

### 新增

- **插件 API v2**（`shiritaibot.api`）：结构参照 AstrBot 插件，详见 `docs/PLUGINS.md`和[官网开发文档](https://shiritaibot.dev)。同步更新了WebUI。
- **ShiritaiBot Devs 公告插件**（`srtb_plugin_devs_notice`）：采用 HTTPS 轮询 `notice.shiritaibot.dev/api`（默认 1200 秒，最短 600 秒），
  可按类型（版本发布、安全公告、不兼容变更、服务维护、插件动态、一般公告）选择接收。新公告进入 Proactive Loop，
  首次读取只建立基线。详见 `docs/PLUGINS.md`。
- **课程日程**：新增 `course`（课程）分类，支持查询和修改时间，默认不提醒、不触发 Proactive Loop。WebUI 月历默认隐藏课程，点击日期可在旁边带阴影的浮窗查看当天全部安排（含课程）。
- **模型调用花费按人民币/美元显示**：概览页的「预估花费」可在美元和人民币之间切换，汇率（USD/CNY）取自
  open.er-api.com，按数据源的更新时间缓存（最长 24 小时）。

### 变更

- 内置 email、github 插件迁移到 API v2，`hello_world` 示例改写为 v2 全功能示例。
- 旧版 API（`plugin.toml` + `Entry`，`shiritaibot.plugin_api`）继续可用。**我们计划在未来的几个版本中停止对v1 API的支持。**

### 修复

- GitHub 插件的 API 请求跟随重定向，仓库改名或转移后仍能访问（#26）。

## 0.2.0 - 2026-11-2

### 新增

- **检查更新**：WebUI 顶栏右上角新增「检查更新」按钮，打开居中浮窗，从
  [ShiritaiBot-Devs/ShiritaiBot](https://github.com/ShiritaiBot-Devs/ShiritaiBot) 读取发布版本。
  - 上半部分显示所选版本的更新日志，下半部分分页列出版本；默认只列正式版，可勾选显示测试版，
    也可显示尚未发布的 `main` 最新提交（更新日志为自最新版本以来的提交列表）。
  - 选中版本并确认后自动下载源码包、构建 WebUI、替换程序文件、`uv sync --inexact` 同步依赖，然后重启服务。
    `.venv` 和发布包中没有的插件目录会保留；任一步失败时恢复原文件且不重启。上一版本保存在
    安装目录的 `.update/previous`，可手动回退。
  - 有新的正式版时按钮显示提示点。GitHub 查询结果缓存 5 分钟，可在浮窗中手动刷新。
  - 仅支持一键部署脚本安装、且通过 `shiritaibot run` 启动的实例；源码开发环境请使用 git 更新。
- **知识库**（`[knowledge]`）：存放不会变化的资料，结构扁平，检索不调用文本模型。详见 `docs/KNOWLEDGE.md`。
  - 支持 txt、md、rst、log、json、yaml、xml、csv、tsv、pdf、doc、docx、xls、xlsx、ppt、pptx。
  - 可在 WebUI「知识库」页上传，也可在 QQ 私聊把文件发给 Bot，由 `kb_import` 存入；内容相同的文件只存一份。
  - `kb_search` 用 RRF 融合 BM25（jieba 分词加中文二元组）和 Embedding 余弦相似度；
    没配 Embedding 或调用失败时只用关键词。新工具还有 `kb_read` / `kb_list` / `kb_delete`。
  - 检索默认只对主人的 Agent 会话开放，可用 `knowledge.chat` 开放到 Chat 模式；写入和删除始终只限主人私聊。
  - 新增依赖：pypdf、python-docx、python-pptx、openpyxl、xlrd、olefile。
- **Skill Manager**：安装和管理 `.skill` 技能包（Agent Skills 格式，ZIP 内含 `SKILL.md`）。
  - 已启用技能的名称和描述注入 Agent 系统提示，模型按需用 `skill_read` 读取正文和附带文件。
  - 新工具 `skill_list` / `skill_read` / `skill_write` / `skill_update` / `skill_install` /
    `skill_remove` / `skill_pack`；聊天中收到的技能文件可经 `qq_file_download` 后直接安装。
  - 内置示例技能 `skill-creator`，打包示例见 `examples/skills/skill-creator.skill`。
  - WebUI 新增「技能」页面：上传安装、查看内容、逐个停用、删除。详见 `docs/SKILLS.md`。
- **MCP Manager**：接入 MCP 服务器（stdio 与 Streamable HTTP）。
  - 服务器工具注册为 `mcp_<服务>_<工具>`，安全等级由配置决定，受会话工具权限与审批约束。
  - 请求头与凭据类环境变量自动加密保存；配置变更只重启受影响的服务，进程崩溃后自动重连。
  - WebUI 新增「MCP 服务」页面：配置服务、查看连接状态与工具、手动重连。详见 `docs/MCP.md`。
- **情绪指标系统**，默认关闭（`emotion.enabled`）。开启后，每轮对话结束时异步判断主人的情绪，
  结果影响下一轮的回复语气；情绪足够强烈且主动性足够高时，主动发起一次关心。
  - 三个信号融合成 0–1 的强度：判断模型（`light` 路由，多标签情绪向量）、规则词表、负面用词。
    三者的相对权重可在配置中调整（`judge_weight` / `rules_weight` / `sentiment_weight`），
    只对实际运行过的信号重新归一化。
  - 判定异步执行：本轮开始时取快照，结果在下一轮可见，判定失败不影响回复。
  - 状态按作用域保存：主人、私聊用户、群聊；衰减半衰期与合并系数由 `persistence` 和
    灵敏度 `sensitivity` 控制，阈值随灵敏度变化。
  - 主动关心只在主人方向触发，并有限流，避免连续打扰。
  - WebUI 配置页新增情绪指标表单，可开关并调整各项参数。
- **表情管理器**（`[meme]`，默认关闭）：让 Bot 在 QQ 会话里发表情包。详见 `docs/MEMES.md`。
  - 白名单群里他人发的表情包按 `collect_rate × (1 − 已存数量 / capacity)` 的概率收录；
    多模态模型转述描述，决策模型判断是否符合人设（`fit_threshold`）并归入分类。
  - 默认 10 个分类，可在配置中编辑；表情描述有独立向量表，未配 Embedding 时退回关键词检索。
  - 模型回复中的 `<meme>分类</meme>` 或 `<meme>id</meme>` 拆成独立消息按序发送，
    每条回复上限 `max_per_reply`；提示词只由配置决定，不影响缓存命中。
  - 新工具 `meme_search` / `meme_view` / `meme_save`；新增 `routes.meme` 路由。
  - WebUI 新增「表情管理」页：参数、分类编辑、浏览搜索、上传、修改、重新转述、删除。
  - 新增依赖 `pillow`（动图取首帧转述）。
- **GitHub 插件**（`plugin/github/`，随主仓库提交，默认未启用）：以专用 bot 账号读写仓库。
  - 工具 `github_read` / `github_write` / `github_publish` / `github_admin` / `github_push`，
    按风险分级；仅主人在私聊 / WebUI 的 Agent 模式可用，仓库 owner 限定在 `allowed_owners` 内。
  - token 加密保存，不进入模型上下文、shell 和子代理；shell 与子代理只注入 git 提交者身份，
    推送经插件私有裸镜像完成。
  - 可配置代理（http / https / socks5 / socks5h），同时用于 API 请求和 `github_push`。
  - 可选 `co_author`：shell 中的 `git commit`、`pr_merge` 自动追加
    `Co-authored-by: ShiritaiBot` 尾注，新启动的子代理在提示词中被要求加上。
  - 插件 API 新增 `PluginContext.set_process_env` / `session_workspace` /
    `add_subagent_note` / `add_shell_filter`，重载或停用时自动撤销；`check_proxy` 从
    `plugin_api` 导出。
- **子代理**：
  - 支持 DeepSeek Harness（`deepseek_harness`，`dsh` headless CLI），可续接原生会话，
    权限映射到 `--permission-mode` / `--approval-policy` / `--add-dir`。
  - 子代理以 `NEEDS_INPUT:` 标记或因权限被拒停下时进入 `needs_input` 状态，由 Proactive Agent
    代为回答或转告主人；新工具 `subagent_send` 在原生会话中续接运行。
- **WebUI 概览**：按模型统计 token 用量、缓存命中率和估算费用；模型可配置货币与每百万
  输入 / 缓存 / 输出价格，支持从 OpenRouter 风格的模型列表拉取价格；token 图表按模型着色。
- 唤醒关键词匹配模式 `wake_keyword_mode`：`contains`（任意位置，默认）或 `prefix`（仅句首）。
- 网络学堂 `learn_query` 接受 `homework_detail` 操作（适配实现位于单独管理的 `thu_learn` 插件）。
- README 新增贡献者列表。

### 变更

- 子代理权限按 CLI 分别配置，取代原来的 `permission = read_only | edit`（旧配置自动迁移）：
  - Codex：`sandbox`、`network_access`、`add_dirs`（经 `-c` 覆盖传入，新会话与续接一致）。
  - Claude Code：`permission_mode`、`tools`、`allowed_tools`、`disallowed_tools`、`add_dirs`。
  - `subagent_instances` 返回 `permissions` 字段，WebUI 子代理页同步调整。
- WebUI 决策模型标签改名为「决策模型（Systemone）」。

### 修复

- `qq_file_download`：协议端的 `get_file` 不返回 QQ HTTPS 地址时，改试 `get_private_file_url` 和消息段自带的链接；
  文件名兼容 NapCat 的 `file` 字段。
- 子代理超长输出被截断时丢失最终结果并误判为 completed（#23）：
  - codex 与 deepseek_harness 改为边读边解析 NDJSON，最终消息、失败、`NEEDS_INPUT`、会话 ID
    和权限拒绝不再受保留上限影响；单行上限 4 MiB，内存有界。
  - claude_code 输出上限提到 16 MiB，仍被截断时显式判失败。
  - 截断时结果末尾附加提示，取不到最终结果判 failed；存储结果保留首尾。
- 唤醒关键词不再从传给模型的文本中删掉，只在后面紧跟命令（如 `小知，/stop`）时剥离。
- QQ `local_file` 允许发送 `<home>/plugin_data` 下的文件（如插件下载的附件）；新增敏感文件
  保护，拒绝 `config.toml`、`master.key`、`web.token`、数据库与日志等内部文件（#16）。
- 安装脚本在 `uv sync` 后向 venv 安装 pip，修复 shell 中 `python3 -m pip` 报
  `No module named pip`（#15）。
- 邮件插件兼容单位数日期的 `INTERNALDATE`（如 QQ 邮箱的 `1-Nov-2026`）；收件失败日志带上
  服务器返回的原因。
- WebUI 侧边栏在视口过矮时可滚动，不再遮住末尾的导航项和状态栏。
- 情绪指标：
  - 规则信号单独命中（判定模型未运行）时，情绪状态不会被写入。
  - 词表中单字条目（烦、累、病）会命中无关词（麻烦、积累、病句），改为词组匹配。
- 流式子代理输出在 Windows 上残留 `\r`，按行解析前统一去掉。
- 修正日志、统计与子代理模块的类型检查报错；子代理测试不再依赖 POSIX 路径，并按 UTF-8 读写。
