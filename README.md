<div align="center">

<img src="https://shiritaibot.dev/shiritai.webp" width="180" alt="ShiritaiBot" />

# ShiritaiBot

**「知りたい」—— 住在消息平台里的下一代 Proactive Agent 框架**

少操一点心，多做喜欢的事。

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-20.19+-339933?logo=node.js&logoColor=white)
![OneBot](https://img.shields.io/badge/OneBot-V11-black)
![Status](https://img.shields.io/badge/status-0.1-orange)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

[官网](https://shiritaibot.dev/) ·
[文档](https://shiritaibot.dev/docs/models) ·
[插件开发](https://shiritaibot.dev/docs/plugins) ·
[问题反馈](https://github.com/XCM42-Orion/ShiritaiBot/issues)

</div>

## 设计理念

### 从事件出发，而不是从提示词出发

大多数 Agent 等你写好提示词才开始工作。ShiritaiBot 按 **Proactive Agent** 的思路设计，工作流从事件开始：新邮件、作业公告、日程到点、子代理任务完成……事件进来后，判断模型先评估它的重要程度，再决定马上处理、汇总后再告诉你，还是先不打扰你。往往你还没想到这件事，它已经处理上了。

### 它会记住，也会越来越懂你

**复合记忆层**把人物、事物、事实、事件关联起来，每条记忆都附带原文证据。聊过的朋友、正在忙的计划、后来改过的安排，它都会记住，需要时连同证据带回对话。记忆会定期总结、反思和精炼，相处越久，它越懂你。

### 住在你最熟悉的聊天软件里

不用守在电脑前。ShiritaiBot 通过 OneBot V11 接入 QQ，打开聊天窗口就能让它查资料、记日程、发提醒，或者调起本机的 Codex / Claude Code 做更长的任务。它负责管理 Coding Agent，过一阵再回来看看进度。

### 理性与感性，各司其职

判断模型会把每条消息分到 **Chat**（陪伴聊天）或 **Work**（干活）模式，两种模式交给不同的模型处理：一个陪你说晚安、提醒你早点睡，一个帮你查 Issue、写插件。

### 安全默认，权限可控

默认只监听本机，不连接外部账号，不调用付费模型，Shell 和主动性引擎都是关闭的。每个会话可以单独设置工具权限（禁止、人工审批、模型自动审批、无需审批），群成员不能自行提权。

## 功能一览

- 🔔 **主动性引擎**：事件驱动的「判断 → 分级 → 行动」循环，带免打扰和通知策略
- 🧠 **复合记忆层**：结构化的长期记忆，支持语义检索、证据回溯、冲突确认和主动遗忘
- 📅 **日程管理**：待办、日程、提醒和定时任务，可以根据对话自动整理
- 💬 **情感陪伴**：多人格，Chat / Work 模式分流，群聊上下文感知
- 🔍 **网页搜索**：可选 Tavily、SerpAPI、DeepSeek Search
- 🤖 **子代理**：调用本机 Codex / Claude Code / 通用 CLI，任务完成后由主动循环接手汇报
- 🧩 **插件系统**：用插件添加工具或事件源，支持 ZIP / GitHub 安装和热重载
- 🌐 **多模型路由**：支持 OpenAI Chat Completions / Responses、Anthropic、Gemini 协议，可设置主备模型和按任务路由，也可接入 Jev 等判断模型
- 🖥️ **WebUI 控制台**：模型、连接、权限、记忆、日志、数据看板，都在浏览器里管理

## 快速部署

### 一键部署（推荐）

macOS / Linux：

```bash
curl -fsSL https://shiritaibot.dev/install.sh | bash
```

Windows（PowerShell）：

```powershell
irm 'https://shiritaibot.dev/install.ps1' | iex
```

脚本会自动准备 Python 和 Node.js 环境，下载源码，构建管理后台并启动服务。所有文件都放在安装目录里，不改动系统包和 Shell 配置。完成后终端会显示管理后台地址和获取登录令牌的命令。

安装目录、端口等选项见 [脚本说明](scripts/README.md)。

### 从源码启动

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 Node.js 20.19+。

```bash
git clone https://github.com/XCM42-Orion/ShiritaiBot.git
cd ShiritaiBot
uv sync
npm --prefix web ci
npm --prefix web run build
uv run shiritaibot run
```

已有源码时也可以直接运行 `bash scripts/install.sh`；Windows 运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1`。

### 登录管理后台

启动后，点击终端里 `Uvicorn running on` 后面的完整链接即可自动登录。如果直接访问 <http://127.0.0.1:8765>，需要先在新终端获取令牌：

```bash
uv run shiritaibot token      # 源码启动
shiritaibot token             # 一键安装（macOS / Linux）
```

> 服务默认只监听本机。远程访问建议使用 SSH 端口转发；如果要暴露到公网，请在启动命令后加上 `--allowed-host <域名>`。

## 开始使用

1. **配置模型**：在「模型与服务」中添加提供商，填入 API Key，获取并添加模型。用对话栏发一句话，能收到回复就说明配置成功。→ [配置模型与路由](https://shiritaibot.dev/docs/models)
2. **连接 QQ**：在「协议连接」中添加 OneBot 正向 WebSocket 实例（例如 NapCat），填写 Bot QQ、主人 QQ 和访问令牌。用 QQ 给 Bot 发 `/help`，看到帮助菜单就说明连接成功。→ [连接 QQ / NapCat](https://shiritaibot.dev/docs/onebot)
3. **继续探索**：打开记忆、主动性、网页搜索、子代理等功能，按需设置工具权限。→ [了解主动性与工具](https://shiritaibot.dev/docs/agent-loop)

在 QQ 或 WebUI 中输入 `/help` 可以查看全部斜杠命令。

## 插件

可以用插件给 Agent 添加新工具，也可以接入新的事件源，让外部变化进入主动性引擎。仓库自带的 [邮件插件](src/shiritaibot/plugin/email) 是一个完整示例：用 IMAP 收件作为事件源，可选 SMTP 作为通知通道。最小可运行示例见 [examples/plugins/hello_world](examples/plugins/hello_world)。

插件可以在管理后台上传 ZIP、从公开 GitHub 仓库安装，或者放进 `<数据目录>/plugins/` 后点击「重载插件」。

→ [编写第一个插件](https://shiritaibot.dev/docs/plugins) · [插件说明](docs/PLUGINS.md)

或者，直接让 ShiritaiBot 给自己写一个？问问它吧。

## 数据与安全

- 运行数据都在 `data/`（一键安装时位于安装目录下）。API Key 加密保存，但主密钥也在本机，备份时请保护好整个目录。
- Shell 工作目录**不是沙箱**，审批通过的命令拥有 Bot 所在系统账号的全部权限。请谨慎放开 Shell 和子代理权限。

## 参与开发

```bash
npm --prefix web run dev   # 前端开发服务器，/api 代理到 8765
uv run pytest -q           # 后端测试
npm --prefix web test      # 前端测试
```

架构设计见 [设计草案](docs/DESIGN.md)，当前进度见 [实现状态](docs/IMPLEMENTATION.md)。欢迎提交 Issue 和 PR。

## 许可证

本项目使用 [GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0-or-later）开源。修改后的版本如果通过网络向他人提供服务，也需要按同一许可证公开源代码。
