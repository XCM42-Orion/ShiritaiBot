<div align="center">

<img src="https://shiritaibot.dev/shiritai.webp" width="180" alt="ShiritaiBot" />

# ShiritaiBot

**「知りたい」—— 住在消息平台里的下一代 Proactive Agent 框架**


![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-20.19+-339933?logo=node.js&logoColor=white)
![OneBot](https://img.shields.io/badge/OneBot-V11-black)
![Status](https://img.shields.io/badge/status-0.1.0-orange)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

[官网](https://shiritaibot.dev/) ·
[文档](https://shiritaibot.dev/docs/models) ·
[插件开发](https://shiritaibot.dev/docs/plugins) ·
[问题反馈](https://github.com/XCM42-Orion/ShiritaiBot/issues)

</div>

## 设计理念

### Everywhere Agent（随身助手）

现在的 Agent 工作流是：人 → 打开电脑 → 输入 prompt → Agent 干活 → 人看结果。但尽管Agent省下了大量写代码的时间，人依旧被绑在了电脑前面，依旧没有跳开人坐在电脑前面工作这个场景。

而ShiritaiBot希望更进一步，能够把人从电脑前解放出来，随时随地用自己最熟悉的社交平台最大化利用生产力。

### Proactive Loop（主动型能力）

ShiritaiBot践行Proactive Agent的设计理念，除了与用户对话的Main Loop之外，还设计了基于事件的Proactive Loop。

事件源在进入新消息后会激活Proactive Engine，并由判断模型判断重要性。不同重要性的事件会遵循不同的处理方式：低重要性事件直接丢弃，中重要性事件整理归档，高重要性事件处理后直接通知用户。

我们希望，ShiritaiBot能够做到“行动在指令之前”：在你输入一段prompt之前，ShiritaiBot已经做好了一切先期工作。

### Tools for Everything（一切皆工具）

ShiritaiBot践行“一切皆工具(和Skill，未来方向)”的理念，希望能够尽可能减少对话中命令和WebUI的使用，用自然语言让ShiritaiBot处理尽可能多的工作，实现自我维护与更新。

### Self-improvement（自我进化）
我们希望ShiritaiBot不止是帮你工作的Agent，更是随时随地陪伴你的虚拟伴侣。随着ShiritaiBot与你的相处，它能够记住你的一点一滴，不断让自己更贴合你的工作与生活习惯。

## 功能一览

- 🔔 **主动性引擎**：事件驱动的「判断 → 分级 → 行动」循环，带免打扰和通知策略
- 🧠 **复合记忆层**：结构化的长期记忆，支持语义检索、证据回溯、冲突确认和主动遗忘
- 📅 **日程管理**：待办、日程、提醒和定时任务，可以根据对话自动整理
- 💬 **情感陪伴**：高度自定义的人格配置，Chat / Work 模式分流，群聊上下文感知
- 🔍 **网页搜索**：支持 Tavily、SerpAPI、DeepSeek Search 等多种网页搜索提供商
- 🤖 **子代理**：调用本机 Codex / Claude Code / 通用 CLI（施工中），任务完成后由主动循环接手汇报
- 🧩 **插件系统**：用插件添加工具或事件源，支持 ZIP / GitHub 安装和热重载
- 🌐 **多模型路由**：支持 OpenAI Chat Completions / Responses、Anthropic、Gemini 协议，可设置主备模型和按任务路由，也可接入 Jev 等决策模型
- 🖥️ **WebUI 控制台**：模型、连接、权限、记忆、日志、数据看板，都在浏览器里可视化管理

## 快速部署

可以参见 `/scripts` 当中的部署脚本，也可以采用下方的手动部署方式。

手动部署需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 Node.js 20.19+。

```bash
git clone https://github.com/XCM42-Orion/ShiritaiBot.git
cd ShiritaiBot
uv sync
npm --prefix web ci
npm --prefix web run build
uv run shiritaibot run
```

启动后，点击终端里 `Uvicorn running on` 后面的完整链接即可自动登录。如果直接访问 <http://127.0.0.1:8765>，需要先在新终端获取令牌：

```bash
uv run shiritaibot token      # 源码启动
shiritaibot token             # 一键安装（macOS / Linux）
```

> 服务默认只监听本机。远程访问建议使用 SSH 端口转发，如果要暴露到公网，请在启动命令后加上 `--allowed-host <域名>`。


## 许可证

本项目使用 [GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0-or-later）开源。

## 参与贡献

欢迎参与ShiritaiBot的开发！

加入ShiritaiBot Issue反馈群，参与ShiritaiBot的后续工作：1134225815
