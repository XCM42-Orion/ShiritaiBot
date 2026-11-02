# 更新日志

本文件记录 ShiritaiBot 的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，
版本号遵循语义化版本。

## [未发布]

### 新增

- Skill Manager：安装和管理 `.skill` 技能包（Agent Skills 格式，ZIP 内含 `SKILL.md`）。
  - 已启用技能的名称和描述注入 Agent 系统提示，模型按需用 `skill_read` 读取正文和附带文件。
  - 新工具 `skill_list` / `skill_read` / `skill_write` / `skill_update` / `skill_install` /
    `skill_remove` / `skill_pack`；聊天中收到的技能文件可经 `qq_file_download` 后直接安装。
  - 内置示例技能 `skill-creator`，打包示例见 `examples/skills/skill-creator.skill`。
  - WebUI 新增「技能」页面：上传安装、查看内容、逐个停用、删除。详见 `docs/SKILLS.md`。
- MCP Manager：接入 MCP 服务器（stdio 与 Streamable HTTP）。
  - 服务器工具注册为 `mcp_<服务>_<工具>`，安全等级由配置决定，受会话工具权限与审批约束。
  - 请求头与凭据类环境变量自动加密保存；配置变更只重启受影响的服务，进程崩溃后自动重连。
  - WebUI 新增「MCP 服务」页面：配置服务、查看连接状态与工具、手动重连。详见 `docs/MCP.md`。

- 情绪指标系统，默认关闭（`emotion.enabled`）。开启后，每轮对话结束时会异步判断主人的情绪，
  结果影响下一轮的回复语气；情绪足够强烈且主动性足够高时，主动发起一次关心。
  - 三个信号融合成 0–1 的强度：判断模型（`light` 路由，多标签情绪向量）、规则词表、负面用词。
    三者的相对权重可在配置中调整（`judge_weight` / `rules_weight` / `sentiment_weight`），
    只对实际运行过的信号重新归一化。
  - 判定异步执行：本轮开始时取快照，结果在下一轮可见，判定失败不影响回复。
  - 状态按作用域保存：主人、私聊用户、群聊；衰减半衰期与合并系数由 `persistence` 和
    灵敏度 `sensitivity` 控制，阈值随灵敏度变化。
  - 主动关心只在主人方向触发，并有限流，避免连续打扰。
  - WebUI 配置页新增情绪指标表单，可开关并调整各项参数。

### 修复

- 规则信号单独命中（判定模型未运行）时，情绪状态不会被写入。
- 词表中单字条目（烦、累、病）会命中无关词（麻烦、积累、病句）的问题，改为词组匹配。
- 流式子代理输出在 Windows 上残留 `\r`，按行解析前统一去掉。
- 修正日志、统计与子代理模块的类型检查报错；子代理测试不再依赖 POSIX 路径，并按 UTF-8 读写。
