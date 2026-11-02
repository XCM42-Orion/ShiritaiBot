# 技能格式规范

## SKILL.md

文件必须以 YAML frontmatter 开头，`---` 包围：

```markdown
---
name: weekly-report
description: 根据本周的日程、邮件和对话整理周报。当主人要求写周报、总结本周工作或回顾本周进展时使用。
---

# Weekly Report

正文……
```

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| `name` | 是 | 小写字母、数字、短横线，1–64 字符，不以短横线开头或结尾；必须与目录名一致 |
| `description` | 是 | 非空，不超过 1024 字符；写清做什么、何时使用 |
| `license` | 否 | 许可证名称或许可证文件名 |
| 其他字段 | 否 | 会被保留但不影响 ShiritaiBot 的行为（例如 `allowed-tools`、`metadata`） |

`description` 是唯一常驻在系统提示中的内容，是否触发技能全靠它。推荐写法：
「<做什么>。当<场景1>、<场景2>或<触发语>时使用。」

## 目录与文件

- 路径都是技能目录内的相对路径，不能包含 `..`、绝对路径或以 `.` 开头的隐藏文件。
- 单个文本文件不超过 200,000 字符；整个技能不超过 500 个文件、解压后 50 MiB。
- 不支持符号链接。

## .skill 文件

`.skill` 就是一个 ZIP 压缩包，内含一个技能目录：

```
weekly-report.skill
└── weekly-report/
    ├── SKILL.md
    └── references/…
```

也接受 SKILL.md 直接位于压缩包根目录的形式；安装时以 frontmatter 的 `name` 作为技能目录名。
压缩包不超过 10 MiB。安装来源还可以是单个 SKILL.md 文件，或包含 SKILL.md 的本地目录。

## 存放位置与优先级

- 内置技能：随 ShiritaiBot 发布，只读；用 `skill_update` 修改时会自动复制一份到数据目录。
- 已安装技能：`<数据目录>/skills/<name>/`。与内置技能同名时覆盖内置版本，删除后内置版本恢复。
- 停用的技能（`skills.disabled`）不会出现在系统提示中，也不能被 `skill_read` 读取。
