# 更新日志

本文件记录 ShiritaiBot 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，
版本号遵循语义化版本。

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
