# MCP 服务（MCP Manager）

ShiritaiBot 可以作为 MCP（Model Context Protocol）客户端接入 MCP 服务器，把服务器提供的工具
交给 Agent 调用。

代码：`src/shiritaibot/mcp/`。`client.py` 是不依赖额外库的最小客户端，`__init__.py` 负责连接
管理与工具注册。

## 支持范围

- 传输：**stdio**（本地进程，按行分隔的 JSON-RPC）与 **Streamable HTTP**（JSON 或 SSE 响应，
  `Mcp-Session-Id` 会话）。旧版 HTTP+SSE 双端点传输暂不支持。
- 能力：`initialize`、`tools/list`（含分页）、`tools/call`、响应服务器的 `ping`，
  收到 `notifications/tools/list_changed` 时自动刷新工具。
- 服务器发起的 sampling / elicitation / roots 请求会收到 “method not found”，服务器可自行降级。
- resources、prompts 暂不接入。

## 配置

在 WebUI「MCP 服务」页面添加，或直接写入 `config.toml`：

```toml
[[mcp_servers]]
id = "files"
enabled = true
transport = "stdio"
command = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/home/me/docs"]
env = { }
timeout = 60
risk = "sensitive"                       # 默认安全等级
tool_risks = { read_text_file = "safe", list_directory = "safe" }
disabled_tools = ["move_file"]

[[mcp_servers]]
id = "remote"
enabled = true
transport = "http"
url = "https://example.com/mcp"
headers = { Authorization = "Bearer xxx" }   # 保存时自动加密为 secret:...
```

- `command` 是 argv，不经过 shell。`cwd` 留空时使用当前目录，填写时必须是绝对路径。
- `env` 与 `headers` 的值可以写 `env:变量名` 或 `secret:...`。保存时：`headers` 的所有值、以及
  `env` 中名称含 KEY / TOKEN / SECRET / PASSWORD / CREDENTIAL / AUTH 的值，会自动存入 Vault。
- HTTP 地址必须是 http(s)，不能在地址里写账号密码。

## 工具与权限

- 每个工具注册为 `mcp_<服务名>_<工具名>`（非法字符替换为 `_`，超过 64 字符时截断并加哈希），
  描述前缀为 `[MCP 服务名]`，来源显示为 `mcp:<服务名>`。
- 安全等级完全由配置决定：`tool_risks` 按原始工具名覆盖，否则使用服务的 `risk`（默认
  `sensitive`）。服务器自报的 `readOnlyHint` 只在 WebUI 中展示，不会自动降级为 safe。
- MCP 工具与插件工具一样只在 Agent 模式可用，并受逐会话的工具权限、审批和禁止列表控制。
- 工具返回的文本内容合并后交给模型；`structuredContent` 原样附带；图片、音频和二进制资源
  只返回占位说明。`isError` 结果作为工具错误返回给模型。

## 生命周期

- 启动时并行连接所有已启用的服务；连接失败只记录错误，不影响其他服务和程序启动。
- 保存配置时只重启配置有变化的服务，未变化的连接保持不动。
- stdio 服务进程意外退出后，下次调用工具时会自动重连一次。
- WebUI 展示每个服务的连接状态、服务器信息和工具列表，可手动「重新连接」。
- MCP 服务进程与 ShiritaiBot 同账号运行，属于可信本地代码；只接入信任的服务器。
