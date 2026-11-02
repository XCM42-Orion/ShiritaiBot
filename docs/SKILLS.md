# 技能（Skill Manager）

技能是可安装的「操作手册」：一个包含 `SKILL.md` 的目录，开头用 YAML frontmatter 写明名称和
用途，正文写执行步骤，可附带 `references/`、`scripts/`、`assets/` 等文件。格式与 Agent Skills
约定兼容，`.skill` 文件就是这个目录的 ZIP 包。

技能只是说明文字，宿主不会执行其中的任何代码；技能附带的脚本需要由模型通过 `shell` 工具运行，
仍受工具权限与审批约束。

代码：`src/shiritaibot/skill/`。内置技能：`src/shiritaibot/skill/builtin/`。

## 工作方式（渐进式披露）

1. Agent 模式下，系统提示末尾的 `<skills>` 块列出所有已启用技能的 `name` 和 `description`。
2. 任务与某个技能匹配时，模型调用 `skill_read` 读取其 SKILL.md，再按说明执行。
3. SKILL.md 引用的其他文件按需用 `skill_read(path=...)` 读取。

只有工具列表中包含 `skill_read` 时才注入技能列表；Chat 模式不提供技能工具，也不注入。

## 存放位置

| 来源 | 位置 | 说明 |
| --- | --- | --- |
| 内置 | `src/shiritaibot/skill/builtin/<name>/` | 随程序发布，只读；修改时自动复制到数据目录 |
| 已安装 | `<home>/skills/<name>/` | 与内置同名时覆盖内置版本；删除后内置版本恢复 |

## 工具

| 工具 | 安全等级 | 说明 |
| --- | --- | --- |
| `skill_list` | safe | 列出技能、来源、是否启用 |
| `skill_read` | safe | 读取 SKILL.md 或技能内文件，长文件分段 |
| `skill_write` | normal | 新建技能（SKILL.md + 附带文本文件），同名需 `replace` |
| `skill_update` | normal | 修改单个文件：`content` 整体替换 / `old_text`+`new_text` 精确替换 / `delete` |
| `skill_install` | sensitive | 从本地 .skill/.zip、SKILL.md 或目录安装；相对路径基于会话工作目录 |
| `skill_remove` | dangerous | 删除已安装技能（内置技能不可删除） |
| `skill_pack` | normal | 打包为 `<name>.skill` 放入会话工作目录，可再用 `qq_file_send` 发送 |

`skill_update` 不允许通过修改 frontmatter 的 `name` 改名，也不能删除 SKILL.md。

## 安装

- **聊天中直接发文件**：主人在 QQ 中发送 `.skill` 文件，助手用 `qq_file_download` 下载后调用
  `skill_install`。下载的文件名是哈希值，安装时按内容识别 ZIP 或 Markdown，不依赖扩展名。
- **WebUI**：「技能」页面上传 `.skill` / `.zip` / `SKILL.md`，可选择覆盖同名技能。
- **手动**：把技能目录放到 `<home>/skills/` 下即可，无需重启。

限制：技能包不超过 10 MiB，解压后不超过 50 MiB、500 个文件；不允许绝对路径、`..`、符号链接；
隐藏文件和 `__pycache__` 会被忽略。

## 配置

```toml
[skills]
enabled = true          # 关闭后不注册 skill_* 工具，也不注入技能列表
inject = true           # 在系统提示中列出技能
disabled = []           # 停用的技能名称：不注入、不能被 skill_read 读取
```

## 示例：skill-creator

内置的 `skill-creator` 指导助手创建、改进和打包技能，涵盖格式规范（`references/format.md`）、
编写原则和创建流程。打包好的示例位于 `examples/skills/skill-creator.skill`，可用来测试安装流程。
修改内置技能后可以重新生成该示例：让助手调用 `skill_pack`，或用任意 ZIP 工具把目录打包为
`skill-creator/...` 结构。
