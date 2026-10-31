# 一键部署

官网首页与快速开始文档提供可复制的系统命令。脚本准备运行环境、获取源码、
安装依赖并构建管理后台，完成后在当前终端启动服务。

已有源码时可以直接执行：

```sh
bash scripts/install.sh
```

Windows PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1
```

## 选项

| 用途 | macOS / Linux | Windows |
|---|---|---|
| 安装目录 | `--dir PATH` | `-InstallDir PATH` |
| 端口（默认 8765） | `--port 8765` | `-Port 8765` |
| 安装完成后暂不启动 | `--no-start` | `-NoStart` |

默认目录是 macOS 的 `~/Library/Application Support/ShiritaiBot`、Linux 的
`${XDG_DATA_HOME:-~/.local/share}/shiritaibot`、Windows 的
`%LOCALAPPDATA%\ShiritaiBot`。目录中 `app/` 保存源码、`data/` 保存聊天和配置、
`runtime/` 保存运行环境与缓存。再次执行会复用已有源码和数据。

安装输出会给出管理页面、重启命令和获取令牌的命令。Bash 使用安装目录里的
`start.sh` 启动、`start.sh token` 获取令牌；Windows 使用输出的 PowerShell
命令启动，附加 `-Token` 获取令牌。在管理页面添加模型并连接 QQ；按 Ctrl+C
停止前台服务。

macOS / Linux 还会在 `~/.local/bin` 添加 `shiritaibot` 快捷入口。
该目录已在 PATH 中时，可从任意目录运行 `shiritaibot token`，获取的始终是
安装实例的令牌；`shiritaibot` 可重新启动同一实例。已有的其他同名命令不会被覆盖。
可用 `SHIRITAI_COMMAND_DIR` 指定快捷入口目录。Windows 继续使用 `start.ps1 -Token`。

脚本从当前仓库 `main` 下载源码，通过 uv 遵循项目 `.python-version` 准备 Python，
复用兼容的 Node.js 或下载校验过 SHA256 的官方 Node.js 24。新增环境放在安装目录，
无须修改系统包、Shell 配置或全局 PowerShell 执行策略。
检测会实际运行 Node.js 与配套 npm，并逐个尝试 PATH 中的候选项。Windows 使用
原生 `node.exe` 直接运行 `npm-cli.js`，兼容系统自带的 PowerShell 5.1。
已有可用环境会直接复用；损坏的安装器私有 Node 会保留到 `runtime/.node-backup-*`
后再替换。

实现参考了 [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)、
[uv Python 管理](https://docs.astral.sh/uv/concepts/python-versions/) 和
[Node.js 官方分发](https://nodejs.org/download/release/latest-v24.x/)。没有复制第三方安装器源码。

## 验证

```sh
bash -n scripts/install.sh
python3 -m unittest discover -s scripts/tests -v
```

测试在临时目录中模拟下载、运行环境和构建，覆盖带空格与特殊字符的目录、
重复运行、下载或构建失败、Node 校验、旧版 Node 切换与启动/令牌入口。
本轮另在 macOS 临时目录完成真实下载、安装、构建、启动与鉴权接口检查。
Windows 回归测试在 PowerShell 5.1 和 PowerShell 7 下运行真实的 Node/npm，
覆盖环境复用、缺少 Node、损坏的私有运行时、路径中的空格与非 ASCII 字符。
下载与 uv 使用隔离测试夹具；不连接模型、不读写现有机器人数据。

```powershell
./scripts/tests/test_node.ps1
./scripts/tests/test_windows_install.ps1
```
