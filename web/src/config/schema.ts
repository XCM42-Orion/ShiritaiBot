import type {
  AdapterConfig,
  PluginConfig,
  ProviderConfig,
  SubagentConfig,
} from "../api/types";
import { uniqueId } from "../lib/utils";

export type FieldKind =
  | "text"
  | "number"
  | "boolean"
  | "select"
  | "secret"
  | "list"
  | "time"
  | "json"
  | "multiselect"
  | "textarea";

export interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  help?: string;
  placeholder?: string;
  options?: string[];
  optionLabels?: Record<string, string>;
  min?: number;
  max?: number;
  full?: boolean;
}

export const PROVIDER_FIELDS: FieldSpec[] = [
  {
    key: "id",
    label: "提供商名称",
    kind: "text",
    help: "作为模型引用和路由里的标识，只能使用字母、数字、点、下划线或短横线。",
  },
  { key: "enabled", label: "启用提供商", kind: "boolean" },
  {
    key: "protocol",
    label: "接口协议",
    kind: "select",
    options: ["chat_completions", "responses", "anthropic", "gemini"],
  },
  {
    key: "base_url",
    label: "API Base URL",
    kind: "text",
    placeholder: "https://api.openai.com/v1",
    help: "填写接口前缀：OpenAI / Anthropic 通常以 /v1 结尾，Gemini 以 /v1beta 结尾。",
  },
  { key: "api_key", label: "API Key", kind: "secret" },
  { key: "timeout", label: "超时（秒）", kind: "number", min: 5, max: 600 },
  {
    key: "max_retries",
    label: "最大重试轮数",
    kind: "number",
    min: 0,
    max: 10,
    help: "超时、限流或临时服务错误后的额外重试次数；0 表示不重试。默认 2 轮，最多发送 3 次。",
  },
  {
    key: "proxy",
    label: "网络请求代理",
    kind: "text",
    placeholder: "http://127.0.0.1:7890",
    help: "支持 HTTP、HTTPS、SOCKS5；用于模型发现、测试及正式调用。留空使用服务进程的默认网络环境。",
    full: true,
  },
];

export const MODEL_FIELDS: FieldSpec[] = [
  { key: "id", label: "模型标识", kind: "text" },
  { key: "model", label: "提供商模型名称", kind: "text" },
  {
    key: "max_output",
    label: "最大输出 tokens",
    kind: "number",
    min: 64,
    max: 128000,
  },
  { key: "enabled", label: "启用", kind: "boolean" },
  {
    key: "max_context",
    label: "最大上下文 tokens",
    kind: "number",
    min: 0,
    max: 10000000,
    help: "包括输入与输出，须大于最大输出。0 表示未指定；按字符估算 token，超出时尝试下一候选模型。",
  },
  {
    key: "modalities",
    label: "模态支持（输入）",
    kind: "multiselect",
    options: ["text", "image", "audio", "video"],
    optionLabels: { text: "文本", image: "图像", audio: "音频", video: "视频" },
    help: "至少选择一项。用于记录模型能力；当前消息链路仍为文本，勾选不会开启媒体上传或转换。",
    full: true,
  },
  {
    key: "extra_body",
    label: "自定义请求体参数（JSON）",
    kind: "json",
    full: true,
    placeholder: '{"temperature": 0.7}',
    help: '按接口原生格式填写 JSON 对象，嵌套对象递归合并；Gemini 示例：{"generationConfig":{"temperature":0.7}}。参数覆盖默认值，但不能替换模型、对话或工具定义，也不能开启流式响应。',
  },
];

export const ADAPTER_FIELDS: FieldSpec[] = [
  { key: "id", label: "实例标识", kind: "text" },
  { key: "enabled", label: "启用连接", kind: "boolean" },
  {
    key: "url",
    label: "WebSocket 地址",
    kind: "text",
    placeholder: "ws://127.0.0.1:3001",
    help: "NapCat 的 Universal WebSocket 地址，只支持 ws:// 或 wss://。",
  },
  { key: "token", label: "访问令牌", kind: "secret" },
  { key: "self_id", label: "Bot QQ 号", kind: "text" },
  {
    key: "owner_id",
    label: "主人 QQ 号",
    kind: "text",
    help: "主人的私聊拥有完整 Agent 权限；其他人和群聊只能使用 Chat 模式。",
  },
  {
    key: "groups",
    label: "启用的群号",
    kind: "list",
    help: "每行一个群号，只有白名单内的群会响应。",
  },
  {
    key: "friends",
    label: "启用的好友",
    kind: "list",
    help: "每行一个 QQ 号，非主人好友加入后才能使用 Chat 模式。",
  },
  {
    key: "ignore_users",
    label: "忽略的账号",
    kind: "list",
    help: "每行一个 QQ 号，优先级高于白名单。",
  },
];

export const SUBAGENT_FIELDS: FieldSpec[] = [
  { key: "id", label: "实例标识", kind: "text" },
  { key: "enabled", label: "启用实例", kind: "boolean" },
  {
    key: "kind",
    label: "类型",
    kind: "select",
    options: ["codex", "claude_code", "generic"],
  },
  {
    key: "command",
    label: "启动命令",
    kind: "list",
    help: "每行一个参数，例如第一行 codex，第二行 exec。",
  },
  {
    key: "permission",
    label: "权限范围",
    kind: "select",
    options: ["read_only", "edit"],
  },
  { key: "timeout", label: "超时（秒）", kind: "number", min: 5, max: 7200 },
  {
    key: "model",
    label: "模型",
    kind: "text",
    help: "留空则使用 CLI 的默认模型。",
  },
];

export const POLICY_FIELDS: FieldSpec[] = [
  {
    key: "shell_enabled",
    label: "启用 Shell 工具",
    kind: "boolean",
    help: "默认关闭。启用后每次调用仍需审批；它不是系统沙箱。",
  },
  {
    key: "shell_command",
    label: "Shell 启动参数",
    kind: "list",
    help: "每行一个参数，默认 bash --noprofile --norc -c。",
  },
  {
    key: "approval_timeout",
    label: "审批有效期（秒）",
    kind: "number",
    min: 1,
    max: 3600,
  },
  { key: "max_steps", label: "每轮步数上限", kind: "number", min: 1, max: 100 },
  {
    key: "max_turn_seconds",
    label: "单轮时限（秒）",
    kind: "number",
    min: 10,
    max: 7200,
  },
  {
    key: "max_context_chars",
    label: "上下文字符上限",
    kind: "number",
    min: 4000,
    max: 1000000,
  },
  {
    key: "denied_tools",
    label: "禁用工具",
    kind: "list",
    help: "每行一个工具名，支持通配符，例如 shell 或 web_*。",
  },
];

export const PROACTIVE_FIELDS: FieldSpec[] = [
  { key: "enabled", label: "启用主动性引擎", kind: "boolean" },
  {
    key: "timezone",
    label: "时区",
    kind: "text",
    placeholder: "Asia/Shanghai",
  },
  { key: "quiet_start", label: "免打扰开始", kind: "time" },
  { key: "quiet_end", label: "免打扰结束", kind: "time" },
  {
    key: "throttle_seconds",
    label: "同来源通知间隔（秒）",
    kind: "number",
    min: 0,
    max: 86400,
  },
];

export const BASIC_SETTINGS_FIELDS: FieldSpec[] = [
  {
    key: "persona",
    label: "助手人设",
    kind: "textarea",
    full: true,
    help: "作为系统提示词使用。工具结果会被标记为资料而不是指令。",
  },
  {
    key: "tavily_key",
    label: "Tavily API Key",
    kind: "secret",
    help: "配置后才注册 web_search 工具。",
  },
  {
    key: "concurrency",
    label: "LLM 并发数",
    kind: "number",
    min: 1,
    max: 32,
    help: "修改后需要重启进程生效。",
  },
];

export const PLUGIN_FIELDS: FieldSpec[] = [
  { key: "id", label: "插件标识", kind: "text" },
  { key: "enabled", label: "启用插件", kind: "boolean" },
];

export const PLUGIN_SETTINGS_FIELDS: Record<string, FieldSpec[]> = {
  email: [
    { key: "imap_host", label: "IMAP 服务器", kind: "text" },
    {
      key: "imap_port",
      label: "IMAP 端口",
      kind: "number",
      min: 1,
      max: 65535,
    },
    {
      key: "imap_security",
      label: "IMAP 加密方式",
      kind: "select",
      options: ["ssl", "starttls", "none"],
    },
    { key: "username", label: "邮箱账号", kind: "text" },
    { key: "password", label: "邮箱密码或授权码", kind: "secret" },
    { key: "mailbox", label: "收件文件夹", kind: "text" },
    { key: "poll", label: "自动检查新邮件", kind: "boolean" },
    {
      key: "poll_seconds",
      label: "轮询间隔（秒）",
      kind: "number",
      min: 60,
      max: 86400,
    },
    {
      key: "max_per_poll",
      label: "每次最多读取邮件数",
      kind: "number",
      min: 1,
      max: 200,
    },
    {
      key: "max_body_chars",
      label: "正文摘要字符上限",
      kind: "number",
      min: 200,
      max: 20000,
    },
    { key: "smtp_host", label: "SMTP 服务器（可选）", kind: "text" },
    {
      key: "smtp_port",
      label: "SMTP 端口",
      kind: "number",
      min: 1,
      max: 65535,
    },
    {
      key: "smtp_security",
      label: "SMTP 加密方式",
      kind: "select",
      options: ["ssl", "starttls", "none"],
    },
    {
      key: "sender",
      label: "发件地址",
      kind: "text",
      help: "留空则使用邮箱账号。",
    },
    {
      key: "notify_to",
      label: "通知收件人",
      kind: "list",
      help: "每行一个邮箱；同时填写 SMTP 服务器后启用邮件通知。",
    },
    { key: "subject_prefix", label: "邮件主题前缀", kind: "text" },
    {
      key: "timeout",
      label: "连接超时（秒）",
      kind: "number",
      min: 5,
      max: 120,
    },
  ],
  feishu: [
    {
      key: "command",
      label: "启动命令",
      kind: "list",
      help: "每行一个参数，默认 lark-cli。",
    },
    { key: "config_dir", label: "独立飞书配置目录", kind: "text" },
  ],
  thu_learn: [
    { key: "command", label: "启动命令", kind: "list" },
    { key: "username", label: "学号", kind: "text" },
    { key: "password", label: "密码", kind: "secret" },
    { key: "poll", label: "自动检查作业变化", kind: "boolean" },
    { key: "poll_seconds", label: "轮询间隔（秒）", kind: "number", min: 60 },
  ],
};

export function pluginTemplate(id: string): Record<string, unknown> | null {
  if (id === "email") {
    return {
      imap_host: "",
      imap_port: 993,
      imap_security: "ssl",
      username: "",
      password: "",
      mailbox: "INBOX",
      poll: true,
      poll_seconds: 300,
      max_per_poll: 20,
      max_body_chars: 2000,
      smtp_host: "",
      smtp_port: 465,
      smtp_security: "ssl",
      sender: "",
      notify_to: [],
      subject_prefix: "[ShiritaiBot]",
      timeout: 30,
    };
  }
  if (id === "feishu") return { command: ["lark-cli"], config_dir: "" };
  if (id === "thu_learn") {
    return {
      command: [],
      username: "",
      password: "",
      poll: false,
      poll_seconds: 900,
    };
  }
  return null;
}

/** Fallback for plugin settings the frontend does not know about yet. */
export function inferFieldSpec(key: string, value: unknown): FieldSpec {
  if (typeof value === "boolean") return { key, label: key, kind: "boolean" };
  if (typeof value === "number") return { key, label: key, kind: "number" };
  if (Array.isArray(value)) return { key, label: key, kind: "list" };
  return { key, label: key, kind: "text" };
}

export function createProvider(rows: ProviderConfig[]): ProviderConfig {
  return {
    id: uniqueId("provider-new", rows),
    enabled: true,
    protocol: "chat_completions",
    base_url: "https://api.openai.com/v1",
    api_key: "",
    timeout: 120,
    max_retries: 2,
    proxy: "",
  };
}

export function createAdapter(rows: AdapterConfig[]): AdapterConfig {
  return {
    id: uniqueId("qq-main", rows),
    enabled: false,
    url: "ws://127.0.0.1:3001",
    token: "",
    self_id: "",
    owner_id: "",
    groups: [],
    friends: [],
    ignore_users: [],
  };
}

export function createSubagent(rows: SubagentConfig[]): SubagentConfig {
  return {
    id: uniqueId("codex-main", rows),
    enabled: false,
    kind: "codex",
    command: ["codex"],
    permission: "read_only",
    timeout: 900,
    model: "",
  };
}

export function createPlugin(rows: PluginConfig[]): PluginConfig {
  return {
    id: uniqueId("plugin-new", rows),
    enabled: false,
    settings: {},
  };
}
