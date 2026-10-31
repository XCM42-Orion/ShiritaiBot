#!/usr/bin/env bash
# ShiritaiBot user-local installer. Requires Bash, curl, tar and a SHA-256 utility.
set -Eeuo pipefail

shiritai_install() {
  local target='' port=8765 no_start=0 platform arch temp_dir='' uv_bin node_dir
  local command_dir command_entry=''
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dir) [ "$#" -ge 2 ] || { printf '%s\n' '--dir 需要目录参数。' >&2; return 1; }; target=$2; shift 2 ;;
      --port) [ "$#" -ge 2 ] || { printf '%s\n' '--port 需要端口参数。' >&2; return 1; }; port=$2; shift 2 ;;
      --no-start) no_start=1; shift ;;
      --help|-h)
        printf '%s\n' 'ShiritaiBot 一键部署' '用法: bash install.sh [--dir PATH] [--port 8765] [--no-start]' '默认安装后在当前终端启动；Ctrl+C 停止。'; return 0 ;;
      *) printf '未知参数: %s\n' "$1" >&2; return 1 ;;
    esac
  done
  [[ "$port" =~ ^[0-9]{1,5}$ ]] || { printf '%s\n' '端口应在 1–65535 之间。' >&2; return 1; }
  port=$((10#$port))
  [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || { printf '%s\n' '端口应在 1–65535 之间。' >&2; return 1; }

  case "$(uname -s)" in
    Darwin) platform=darwin; target=${target:-"$HOME/Library/Application Support/ShiritaiBot"} ;;
    Linux) platform=linux; target=${target:-"${XDG_DATA_HOME:-$HOME/.local/share}/shiritaibot"} ;;
    *) printf '%s\n' '此脚本用于 Linux 或 macOS；Windows 请使用 install.ps1。' >&2; return 1 ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) printf '%s\n' '当前一键部署支持 x64 和 ARM64。' >&2; return 1 ;;
  esac
  for tool in curl tar awk mktemp; do
    command -v "$tool" >/dev/null 2>&1 || { printf '请先安装 %s，再运行此命令。\n' "$tool" >&2; return 1; }
  done
  if [ -e "$target" ] && [ ! -d "$target" ]; then
    printf '安装路径不是目录: %s\n' "$target" >&2; return 1
  fi
  if [ -d "$target" ] && [ -n "$(ls -A "$target")" ] &&
     ! [ -f "$target/.shiritaibot-installer" ]; then
    printf '该目录已有其他文件，请用 --dir 选择空目录: %s\n' "$target" >&2; return 1
  fi
  mkdir -p "$target"
  target=$(cd "$target" && pwd -P)
  printf '%s\n' 'shiritaibot-installer-v1' > "$target/.shiritaibot-installer"
  mkdir -p "$target/runtime/bin" "$target/data"
  chmod 700 "$target/data"
  temp_dir=$(mktemp -d "$target/.install-XXXXXX")
  SHIRITAI_STAGING_DIR=$temp_dir
  # Only remove this invocation's staging files; app/ and data/ survive failures.
  trap 'shiritai_result=$?; [ -z "${SHIRITAI_STAGING_DIR:-}" ] || rm -rf -- "$SHIRITAI_STAGING_DIR"; if [ "$shiritai_result" -ne 0 ]; then printf "%s\n" "安装未完成，请查看上面的错误；修复后可重新运行同一命令。" >&2; fi' EXIT
  export UV_PYTHON_INSTALL_DIR="$target/runtime/python"
  export UV_PYTHON_BIN_DIR="$target/runtime/bin"
  export UV_CACHE_DIR="$target/runtime/cache/uv"
  export npm_config_cache="$target/runtime/cache/npm"
  export PATH="$target/runtime/bin:$target/runtime/node/bin:$PATH"

  shiritai_download() {
    curl --fail --show-error --location --retry 2 --connect-timeout 20 --output "$2" "$1"
  }
  shiritai_node_works() {
    command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 &&
      node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>=24?0:1)' >/dev/null 2>&1
  }

  if [ -d "$target/app" ]; then
    [ -f "$target/app/src/shiritaibot/cli.py" ] && [ -f "$target/app/uv.lock" ] &&
      [ -f "$target/app/web/package-lock.json" ] || { printf '%s\n' 'app 目录不是完整的 ShiritaiBot 源码，请先检查该目录。' >&2; return 1; }
    printf '%s\n' '使用已有 ShiritaiBot 源码。'
  elif [ -e "$target/app" ]; then
    printf '%s\n' 'app 路径已被其他文件占用。' >&2; return 1
  else
    printf '%s\n' '[1/4] 下载 ShiritaiBot'
    shiritai_download 'https://github.com/XCM42-Orion/ShiritaiBot/archive/refs/heads/main.tar.gz' "$temp_dir/source.tar.gz"
    mkdir "$temp_dir/app"
    tar -xzf "$temp_dir/source.tar.gz" -C "$temp_dir/app" --strip-components=1
    [ -f "$temp_dir/app/src/shiritaibot/cli.py" ] && [ -f "$temp_dir/app/uv.lock" ] &&
      [ -f "$temp_dir/app/web/package-lock.json" ] || { printf '%s\n' '下载的源码不完整。' >&2; return 1; }
    mv "$temp_dir/app" "$target/app"
  fi

  printf '%s\n' '[2/4] 准备 Python 和 Node.js'
  if ! command -v uv >/dev/null 2>&1; then
    shiritai_download 'https://astral.sh/uv/install.sh' "$temp_dir/uv-install.sh"
    UV_UNMANAGED_INSTALL="$target/runtime/bin" sh "$temp_dir/uv-install.sh"
  fi
  uv_bin=$(type -P uv)
  [ -n "$uv_bin" ] && "$uv_bin" --version || { printf '%s\n' 'uv 无法运行。' >&2; return 1; }

  if ! shiritai_node_works; then
    local archive expected actual
    if [ -e "$target/runtime/node" ]; then
      printf '%s\n' '安装目录中的 Node.js 无法运行；请检查 runtime/node 或使用新的 --dir。' >&2; return 1
    fi
    shiritai_download 'https://nodejs.org/download/release/latest-v24.x/SHASUMS256.txt' "$temp_dir/SHASUMS256.txt"
    archive=$(awk -v suffix="-$platform-$arch.tar.gz" '$2 ~ (suffix "$") && $2 ~ /^node-v24\./ { print $2; exit }' "$temp_dir/SHASUMS256.txt")
    [ -n "$archive" ] || { printf '%s\n' '未找到适合当前系统的 Node.js 24。' >&2; return 1; }
    expected=$(awk -v file="$archive" '$2 == file { print $1; exit }' "$temp_dir/SHASUMS256.txt")
    [[ "$expected" =~ ^[[:xdigit:]]{64}$ ]] || { printf '%s\n' 'Node.js 校验清单无效。' >&2; return 1; }
    shiritai_download "https://nodejs.org/download/release/latest-v24.x/$archive" "$temp_dir/node.tar.gz"
    if command -v sha256sum >/dev/null 2>&1; then
      actual=$(sha256sum "$temp_dir/node.tar.gz" | awk '{print $1}')
    elif command -v shasum >/dev/null 2>&1; then
      actual=$(shasum -a 256 "$temp_dir/node.tar.gz" | awk '{print $1}')
    else
      printf '%s\n' '请安装 sha256sum 或 shasum 后重试。' >&2; return 1
    fi
    [ "$actual" = "$expected" ] || { printf '%s\n' 'Node.js 下载校验失败，请重试。' >&2; return 1; }
    mkdir "$temp_dir/node"
    tar -xzf "$temp_dir/node.tar.gz" -C "$temp_dir/node" --strip-components=1
    "$temp_dir/node/bin/node" --version
    mv "$temp_dir/node" "$target/runtime/node"
    hash -r
  fi
  shiritai_node_works || { printf '%s\n' 'Node.js/npm 无法运行，请检查系统兼容性。' >&2; return 1; }
  node_dir=$(dirname "$(type -P node)")
  export PATH="$node_dir:$PATH"

  printf '%s\n' '[3/4] 安装依赖并构建管理页面'
  cd "$target/app"
  "$uv_bin" sync --frozen --no-dev
  npm --prefix web ci --no-audit --no-fund
  npm --prefix web run build

  # Save executable locations without sourcing editable shell configuration.
  {
    printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail'
    printf 'SHIRITAI_UV=%q\nSHIRITAI_NODE_DIR=%q\nSHIRITAI_PORT=%q\n' "$uv_bin" "$node_dir" "$port"
    cat <<'LAUNCHER'
SHIRITAI_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
export PATH="$SHIRITAI_ROOT/runtime/bin:$SHIRITAI_NODE_DIR:$PATH"
export UV_PYTHON_INSTALL_DIR="$SHIRITAI_ROOT/runtime/python"
export UV_PYTHON_BIN_DIR="$SHIRITAI_ROOT/runtime/bin"
export UV_CACHE_DIR="$SHIRITAI_ROOT/runtime/cache/uv"
cd "$SHIRITAI_ROOT/app"
case "${1:-run}" in
  token) shift; exec "$SHIRITAI_UV" run --frozen --no-sync python -m shiritaibot.cli token --home "$SHIRITAI_ROOT/data" "$@" ;;
  run) [ "$#" -eq 0 ] || shift; exec "$SHIRITAI_UV" run --frozen --no-sync python -m shiritaibot.cli run --home "$SHIRITAI_ROOT/data" --port "$SHIRITAI_PORT" "$@" ;;
  *) printf '%s\n' '用法: start.sh [run|token]'; exit 1 ;;
esac
LAUNCHER
  } > "$temp_dir/start.sh"
  chmod 700 "$temp_dir/start.sh"
  mv "$temp_dir/start.sh" "$target/start.sh"

  command_dir=${SHIRITAI_COMMAND_DIR:-"$HOME/.local/bin"}
  if { [ ! -e "$command_dir/shiritaibot" ] && [ ! -L "$command_dir/shiritaibot" ]; } ||
     { [ -f "$command_dir/shiritaibot" ] && [ ! -L "$command_dir/shiritaibot" ] &&
       [ "$(sed -n '2p' "$command_dir/shiritaibot")" = '# Managed by ShiritaiBot installer' ]; }; then
    mkdir -p "$command_dir"
    {
      printf '%s\n' '#!/usr/bin/env bash' '# Managed by ShiritaiBot installer'
      printf 'exec %q "$@"\n' "$target/start.sh"
    } > "$temp_dir/shiritaibot"
    chmod 700 "$temp_dir/shiritaibot"
    mv "$temp_dir/shiritaibot" "$command_dir/shiritaibot"
    case ":$PATH:" in
      *":$command_dir:"*) command_entry="$command_dir/shiritaibot" ;;
    esac
  fi
  rm -rf -- "$temp_dir"
  temp_dir=''
  SHIRITAI_STAGING_DIR=''
  printf '\n%s\n' '[4/4] 部署完成'
  printf '管理页面: http://127.0.0.1:%s\n' "$port"
  printf '重新启动: '; printf '%q\n' "$target/start.sh"
  printf '获取管理令牌: '; printf '%q token\n' "$target/start.sh"
  if [ -n "$command_entry" ]; then
    printf '%s\n' '快捷启动: shiritaibot' '快捷获取令牌: shiritaibot token'
  fi
  printf '数据目录: %s\n' "$target/data"
  if [ "$no_start" -eq 0 ]; then
    printf '%s\n' '正在启动。保持此终端打开；按 Ctrl+C 停止。'
    exec "$target/start.sh"
  fi
}

shiritai_install "$@"
