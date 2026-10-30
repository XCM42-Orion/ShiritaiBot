# 0.1 实现范围与验证

本文件描述当前代码实际行为；`DESIGN.md` 是目标架构，未将全部条目标记为已完成。2026-10-27 的目录调整优先于设计草案：OneLearn 核心在 `learn/`，具体学校和飞书实现是 `plugin/` 下的本地插件，主仓库只保留 `plugin/core/`。

| 模块 | 已实现的基础路径 | 尚未实现的设计项 |
|---|---|---|
| AgentLoop | 会话串行/跨会话并发、同一发送者消息去抖、插话、取消、工具参数校验、步数/墙钟/重复调用限制、消息持久化、中断历史修复 | 完整 recall、epoch 摘要压缩、优先级信号量、只读工具并行、按日费用预算 |
| 权限 | Main 会话 Chat / Agent、四档逐工具权限和四种预设；会话及私聊/群聊默认；Proactive 创建时权限快照；人工/独立模型审批与审计；插件安全等级必填；兼容全局白名单 | Admin 角色、完整七层配置来源展示、操作系统沙箱 |
| 工具 | Tavily、bash、时间、QQ 文件下载、记忆、日程、子代理、大输出 artifact 分页 | 网页正文、文档解析、OCR、完整文件管理 |
| OneBot | 多实例正向 Universal WS、Bearer、echo、登录身份校验、ping/pong、重连、CQ 数组/字符串、白名单、持久化 Outbox、分段、文件引用 | 反向 WS、HTTP、NapCat heartbeat 事件超时、全面平台扩展、撤回处理、图像渲染 |
| LLM | 多 Provider / 模型 / 路由；四种 wire protocol；模型目录拉取及分页；不透明推理状态同模型回传；提供商可配置超时重试轮数及 HTTP(S)/SOCKS5 代理；模型自定义 JSON 请求参数、上下文估算上限及输入模态声明；HTTP 限流重试和候选 fallback；usage 落库及跨协议统计 | SSE、原生联网搜索、完整多模态、精确 tokenizer、细粒度计价、embedding |
| Subagent | 多 Codex / Claude Code / Generic CLI 实例；stdin prompt、工作目录隔离、三并发、超时、取消、完成事件、CLI 版本检测 | 原生会话续接、实时事件解析、app-server/ACP、MCP 审批桥接、登录状态检测 |
| 插件 | manifest 发现、启停、手动热重载、冲突检查、加载失败保留旧实例、公共 API、独立数据目录、主动通知通道注册；随仓库提供邮件插件（IMAP 收件事件源，可选 SMTP 通知） | 自动文件监视、依赖安装、MCP、完整 hook/command/source 装饰器 |
| 飞书 | 本地只读 CLI 插件，固定操作/参数白名单、独立 profile、结构化脱敏 | 写操作、真实账号联调 |
| Learn | 通用 stdio 子集、查询/下载工具、持久化同步基线和变化事件；本地清华登录/短信验证工具、查询/附件适配 | 正式协议完整实现、多个学校实例聚合、作业提交、课表转换、真实账号联调 |
| Memory | Main/Proactive 每轮模型短期摘要（7 天）、持久化失败重试和会话隔离、显式记录、private/public 可见性、中文 jieba + FTS5/BM25、归档、私人上下文召回 | 自动反思、画像、向量/图谱融合、群备忘、衰减和合并 |
| Routine | SQLite 待办/日程/提醒/cron；heapq 调度；UTC 持久化、时区 cron、重启后迟到提醒 | ICS 导入/订阅/导出、RRULE、free slots、snooze、过期提醒汇总策略 |
| Proactive | 统一单条/批量事件入口与状态 API；预处理、持久化去重、light 分类 Filter（0–4）；0 丢弃、其余按重要度启动按默认权限快照调用工具的 Proactive Loop；4 强制通知；Loop 最终消息经通知策略后回写 Main context；失败/超时兜底；审计与每轮模型摘要分开 | Markdown Playbook、细粒度动作模板、反馈学习、关注群、课程日程感知 |
| WebUI | 登录、12 个管理视图、哈希路由（可深链和刷新）、类型化 API 客户端、按页面拆分的组件、配置草稿与底部保存条、内联密钥填写、模型发现和加号添加、错误弹窗、会话模式和逐工具权限编辑、四种预设、自动审批记录、24h/3d/7d 逐小时看板、斜杠菜单、工具审批、日程/记忆操作、子代理状态、当前表单模型测试、插件重载、WS 指数退避重连与缓存失效 | 手机端实机验收、密码/QQ 验证码登录、生产级 Cookie session、完整 schema 自动表单、日历视图 |

## 关键运行语义

- 四种 LLM 适配目前使用 **非流式 HTTP JSON 响应**。所有网络与持久化 I/O 都不阻塞主 asyncio 事件循环。
- Chat 仅对话，所有工具均被执行层拒绝。每个 Main 会话可由主人或 WebUI 配置为 Agent，其他成员不能自行提权。
- Shell 按当前会话逐工具权限执行，支持禁止、人工审批、模型自动审批和无需审批；它不是系统沙箱。Codex 默认 `read-only`，可配置 `workspace-write`；Claude 默认只暴露 Read/Glob/Grep，edit 模式增加 Edit/Write，不开放 Bash。Generic CLI 的行为由管理员提供的程序决定。
- 子代理启动按当前工具策略审批；这不是子代理内部逐个工具的审批桥接。子代理拿不到额外的 ShiritaiBot 工具权限。
- 插件是可信进程内代码，工具策略不能防止恶意插件绕过框架直接访问系统。
- API 只绑定 loopback，使用本地随机 token；浏览器令牌放在 sessionStorage。敏感凭据使用 Fernet 加密保存在 SQLite，配置只保存引用。密钥和数据库在同一台机器，不能抵御该账号已被控制的情况。
- OneBot 外发在超时/断线且结果未知时标记 `uncertain`，不自动重发以避免重复。跨数据库和外部消息平台没有 exactly-once 保证。后台事件、提醒和通知均持久化；进程在外部发送成功、写状态之前崩溃仍存在重复窗口。
- 热更新会重连 Adapter；模型参数在下轮生效。并发信号量修改需要重启。插件重载失败保留上一个运行实例，并记录活动日志；配置保存成功不代表每个插件加载成功，需看概览和日志。
- QQ / WebUI 共用斜杠命令，支持会话重置、模型切换、状态/工具/权限查看与修改、私人记忆/子代理查询和一次性审批。命令不交给 LLM；重置仅推进会话历史游标，消息统计和旧记录保留。群聊无法借助命令读取私人记忆或执行审批。
- 配置 API 接受直接填写的凭据，应用配置前自动写入加密存储，再保存不透明引用；GET 不返回密钥明文。验证错误不回显提交的输入。配置文件中的旧引用继续有效。
- 数据看板从消息和 usage 历史聚合，不受普通记录列表 500 条上限影响；所有桶补零，首尾小时按实际滚动区间截断。Anthropic 的缓存读写计入输入，Gemini 的思考 token 计入输出。没有上游 usage 的失败请求不估算 tokens。
- 记忆和业务状态落 SQLite，历史和工具输出可能包含私人内容。日志只存操作标识/安全错误类型，不保存 Provider 请求体或密钥。请自行保护整个数据目录。
- 默认 WebUI 显示最多 500 条记录；后台事件/Outbox 按 pending 状态批量处理，不被历史完成记录挤出查询窗口。调度器一次扫描上限 10,000 条 scheduled 项，适用于个人实例。

## 验证方式

群聊上下文感知：214 项 pytest、26 项 Vitest、Ruff、核心 Pyright、TypeScript / Vite 构建及 Prettier 检查通过。覆盖开关控制的群消息记录、触发消息去重、条数/字符预算、注入不入档、查询工具的群隔离与分页、Chat 模式多步调用基础工具及 WebUI 配置。


会话权限更新：203 项 pytest、26 项 Vitest、Ruff、核心 Pyright、TypeScript / Vite 构建及 Prettier 检查通过。覆盖预设矩阵、逐工具覆盖、Chat 仅基础对话工具、群聊 Agent 授权、权限持久化、Proactive 默认快照、模型审批严格格式/失败拒绝/取消、等待审批时撤权、斜杠修改命令、API 校验及 WebUI 配置交互。

本次 WebUI 合并验证：12 项 Vitest 前端交互测试、TypeScript / Vite 构建和 Prettier 格式检查通过；后端 pytest 为 77 项通过、2 项跳过。保留邮件插件的配置模板，补齐类型化表单，并验证已有凭据保留、模板填入和多个通知收件人的逐行输入。

此前实现验证：63 项 pytest 后端测试、6 项 Vitest 前端交互测试通过；Ruff、核心 Pyright 和前端 TypeScript / Vite 构建通过。新增测试覆盖未保存模型的直接测试、已有/新填密钥、测试不落配置，以及学校登录/验证工具和权限限制。具体本地插件不在主仓库的 Pyright 扫描范围内；清华客户端已单独安装可选依赖并通过导入检查，飞书参数白名单和学校协议层分别有测试。

测试通过本地假 WebSocket 服务、HTTP MockTransport、临时 SQLite 和 Python 测试子进程验证；**没有使用或迁移用户服务器上的真实账号、Cookie、密钥**，也没有更改服务器插件。

覆盖工具闭环、审批允许/拒绝/取消/重放、群聊越权阻断、中文记忆、重启恢复、提醒去重、免打扰、四种协议的工具和推理状态转换、Provider fallback、多实例配置、OneBot echo、CLI 输出限额/超时、Generic 子代理、插件热重载/失败回退、OneLearn stdin 和首次同步基线、Web API 鉴权/验证/WS。

真实 Tavily、四类 LLM 服务、NapCat QQ 收发/文件下载、Codex/Claude 的模型任务、飞书 CLI 和学校 SSO/下载尚未在 ShiritaiBot 内联调。参考 AstrBot 插件的历史验证不替代本项目联调。

前端已通过本地隔离实例的无头 Edge 浏览器验收：看板区间切换、模拟模型列表获取/添加、真实配置 API 保存、无效标识错误弹窗、斜杠菜单与 `/help`。已检查桌面和 390px 手机视口截图，无横向溢出。浏览器测试使用独立临时数据目录和模拟模型列表，没有使用用户账号或调用真实模型；手机实机尚未验收。

## 接口参考

- [OneBot V11 WebSocket](https://github.com/botuniverse/onebot-11/blob/master/communication/ws.md)
- [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)；同时核对过本机 `codex exec --help`。
- [Claude Code CLI](https://code.claude.com/docs/en/cli-reference)
- [OpenAI 模型列表](https://developers.openai.com/api/reference/resources/models/methods/list)、[Anthropic 模型列表](https://platform.claude.com/docs/en/api/models/list)、[Gemini 模型列表](https://ai.google.dev/api/models)
- 命令交互参考 [Codex 命令](https://learn.chatgpt.com/docs/developer-commands) 和 [Claude Code 命令速查](https://support.claude.com/en/articles/14553413-claude-code-cheatsheet)；实际支持范围以 README 为准。

## 2026-10-28 双 Loop 状态机对齐

- Main Agent 保持常规模型/工具循环；Proactive 事件统一经过预处理、Filter 和独立后台 Turn。分类批次按 importance 降序启动，同级 FIFO，不是全局 LLM 抢占调度。
- `short_term` 只保存模型摘要；`proactive_trace` 保存事件阶段审计。摘要任务在 `memory_summary` 持久化，模型不可用时保留并重试，不以日志代替摘要。每轮摘要和尚未实现的 epoch 上下文压缩是不同机制。
- 后台最终消息进入通知队列后，以 assistant 消息追加至目标 Main 会话；活动 Main Turn 结束后再交接，避免拆散工具调用/结果。审批提示仍即时发送。
- 4 级必须尝试通知并进入持久化 Outbox；这不保证离线 QQ 平台已经实际送达，仍须检查 Outbox 状态。

## 消息去抖

- 私聊和群聊空闲时，同一发送者的连续消息合并为一轮：默认等待 1.5 秒，最长 6 秒；以问号、句号或感叹号结尾时立即开始。WebUI 会话和 Proactive 后台任务不参与。
- `policy.debounce_enabled` 控制开关，`policy.debounce_suffixes` 配置立即开始的结尾后缀（空列表表示总是等满时间）。
- 不同发送者的消息不合并；`/stop` 丢弃本人尚未开始的缓冲消息。
- 验证：新增 5 项行为测试和 1 项配置校验，全部通过。
