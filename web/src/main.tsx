import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Activity,
  ArrowUpRight,
  Bot,
  CalendarDays,
  Check,
  ChevronRight,
  Circle,
  Database,
  LayoutDashboard,
  MessageSquare,
  Network,
  Plus,
  Puzzle,
  Save,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  Workflow,
  X,
} from "lucide-react";
import { ProviderWorkspace, SecretField, uniqueId } from "./ConfigWorkspace";
import { Dashboard } from "./Dashboard";
import { ErrorDialog } from "./ErrorDialog";
import "./style.css";

type Json = any;
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});
async function api(path: string, method = "GET", body?: Json) {
  const response = await fetch("/api" + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + sessionStorage.getItem("token"),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.ok) return response.json();
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `请求失败（HTTP ${response.status}），请检查后端服务。`,
    );
  return data;
}
const pages = [
  ["overview", "概览", LayoutDashboard],
  ["chat", "对话", MessageSquare],
  ["events", "主动动态", Activity],
  ["routine", "日程与任务", CalendarDays],
  ["memory", "长期记忆", Database],
  ["subagents", "子代理", Workflow],
  ["plugins", "插件", Puzzle],
  ["models", "模型与服务", Bot],
  ["adapters", "协议连接", Network],
  ["approvals", "权限与审批", ShieldCheck],
  ["logs", "活动日志", Terminal],
  ["settings", "设置", Settings],
] as const;
const labels: Record<string, string> = {
  id: "实例标识",
  enabled: "启用",
  protocol: "接口协议",
  base_url: "服务地址",
  api_key: "API Key",
  provider: "Provider 标识",
  model: "模型名称",
  max_output: "最大输出 tokens",
  url: "WebSocket 地址",
  token: "访问令牌",
  self_id: "Bot QQ 号",
  owner_id: "主人 QQ 号",
  groups: "启用的群号",
  friends: "启用的好友",
  ignore_users: "忽略的账号",
  command: "启动命令（每行一个参数）",
  permission: "权限范围",
  kind: "类型",
  timeout: "超时（秒）",
  persona: "助手人设",
  tavily_key: "Tavily API Key",
  concurrency: "LLM 并发数（重启生效）",
  shell_enabled: "启用 Shell 工具",
  shell_command: "Shell 启动参数",
  denied_tools: "禁用工具",
  scope_tools: "作用域工具白名单",
  approval_timeout: "审批有效期（秒）",
  max_steps: "每轮步数上限",
  max_turn_seconds: "单轮时限（秒）",
  max_context_chars: "上下文字符上限",
  timezone: "时区",
  quiet_start: "免打扰开始",
  quiet_end: "免打扰结束",
  throttle_seconds: "同来源通知间隔（秒）",
  config_dir: "独立飞书配置目录",
  username: "学号",
  password: "密码",
  poll: "自动检查作业变化",
  poll_seconds: "轮询间隔（秒）",
  agent: "Agent 主路由",
  chat: "Chat 路由",
  light: "分诊路由",
};
const choices: Record<string, string[]> = {
  protocol: ["chat_completions", "responses", "anthropic", "gemini"],
  permission: ["read_only", "edit"],
};
const defaults: Record<string, Json> = {
  providers: {
    id: "provider-new",
    enabled: true,
    protocol: "chat_completions",
    base_url: "https://api.openai.com/v1",
    api_key: "",
    timeout: 120,
  },
  models: {
    id: "model-new",
    enabled: true,
    provider: "provider-new",
    model: "",
    max_output: 4096,
  },
  adapters: {
    id: "qq-main",
    enabled: false,
    url: "ws://127.0.0.1:3001",
    token: "",
    self_id: "",
    owner_id: "",
    groups: [],
    friends: [],
    ignore_users: [],
  },
  subagents: {
    id: "codex-main",
    enabled: false,
    kind: "codex",
    command: ["codex"],
    permission: "read_only",
    timeout: 900,
    model: "",
  },
  plugins: { id: "hello_world", enabled: false, settings: {} },
};
function Empty({ text = "这里还没有内容" }: { text?: string }) {
  return (
    <div className="empty">
      <Circle size={26} />
      <p>{text}</p>
    </div>
  );
}
function Details({ value }: { value: Json }) {
  return (
    <details>
      <summary>
        查看详情 <ChevronRight size={12} />
      </summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
function TextList({
  value,
  onChange,
}: {
  value: string[];
  onChange: (value: string[]) => void;
}) {
  const [text, setText] = useState(value.join("\n"));
  useEffect(() => {
    if (
      JSON.stringify(value) !==
      JSON.stringify(
        text
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean),
      )
    )
      setText(value.join("\n"));
  }, [value]);
  return (
    <textarea
      rows={Math.max(2, Math.min(value.length, 5))}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(
          e.target.value
            .split("\n")
            .map((x) => x.trim())
            .filter(Boolean),
        );
      }}
      placeholder="每行一个值"
    />
  );
}
function Fields({
  value,
  onChange,
  prefix = "",
}: {
  value: Json;
  onChange: (value: Json) => void;
  prefix?: string;
}) {
  const [mapKey, setMapKey] = useState("");
  return (
    <div className="fields">
      {Object.entries(value).map(([key, v]) => {
        const update = (next: Json) => onChange({ ...value, [key]: next });
        const title = labels[key] || key;
        if (typeof v === "boolean")
          return (
            <label className="toggle" key={key}>
              <span>{title}</span>
              <input
                type="checkbox"
                checked={v}
                onChange={(e) => update(e.target.checked)}
              />
            </label>
          );
        if (v && typeof v === "object" && !Array.isArray(v))
          return (
            <fieldset key={key}>
              <legend>{title}</legend>
              {key === "scope_tools" && (
                <div className="scope-help">
                  <p>
                    作用域表示这条工具限制应用到哪些会话。格式为「会话类型:连接实例:账号或群号」，星号
                    * 匹配任意内容。
                  </p>
                  <ul>
                    <li>
                      <code>group:qq-main:123456</code>：qq-main 上的 123456 群
                    </li>
                    <li>
                      <code>group:qq-main:*</code>：qq-main 的所有群聊
                    </li>
                    <li>
                      <code>private:qq-main:123456</code>：与账号 123456 的私聊
                    </li>
                    <li>
                      <code>web:*</code>：所有 WebUI 会话
                    </li>
                  </ul>
                  <p>
                    例如在 <code>group:qq-main:*</code> 下填写{" "}
                    <code>web_search</code> 和 <code>current_time</code>
                    （每行一个），这些群聊就只能使用这两个工具。空白名单禁用全部工具；多个匹配规则取交集。白名单不能开放原本被群聊权限或全局策略禁止的工具。
                  </p>
                </div>
              )}

              <Fields value={v} prefix={key} onChange={update} />
            </fieldset>
          );
        return (
          <label key={key}>
            <span>{title}</span>
            {[
              "api_key",
              "token",
              "access_token",
              "password",
              "tavily_key",
            ].includes(key) ? (
              <SecretField value={String(v || "")} onChange={update} />
            ) : Array.isArray(v) ? (
              <TextList value={v} onChange={update} />
            ) : choices[key] || (key === "kind" && prefix === "subagents") ? (
              <select
                value={String(v)}
                onChange={(e) => update(e.target.value)}
              >
                {(choices[key] || ["codex", "claude_code", "generic"]).map(
                  (c) => (
                    <option key={c}>{c}</option>
                  ),
                )}
              </select>
            ) : (
              <input
                type={
                  typeof v === "number"
                    ? "number"
                    : key.startsWith("quiet_")
                      ? "time"
                      : "text"
                }
                value={String(v ?? "")}
                onChange={(e) =>
                  update(
                    typeof v === "number"
                      ? Number(e.target.value)
                      : e.target.value,
                  )
                }
              />
            )}
            {prefix === "scope_tools" && (
              <button
                onClick={() => {
                  const next = { ...value };
                  delete next[key];
                  onChange(next);
                }}
                type="button"
              >
                移除限制
              </button>
            )}
          </label>
        );
      })}
      {prefix === "scope_tools" && (
        <div className="inline">
          <input
            value={mapKey}
            onChange={(e) => setMapKey(e.target.value)}
            placeholder="例如 group:qq-main:*"
          />
          <button
            onClick={() => {
              if (mapKey) {
                onChange({ ...value, [mapKey]: [] });
                setMapKey("");
              }
            }}
          >
            添加作用域
          </button>
        </div>
      )}
    </div>
  );
}
function Collection({
  section,
  config,
  setConfig,
  act,
}: {
  section: string;
  config: Json;
  setConfig: (c: Json) => void;
  act: (f: () => Promise<unknown>) => void;
}) {
  const rows = config[section] as Json[];
  const [selected, setSelected] = useState(0);
  const selectedIndex = Math.min(selected, Math.max(0, rows.length - 1));
  function update(index: number, value: Json) {
    setConfig({
      ...config,
      [section]: rows.map((x, i) => (i === index ? value : x)),
    });
  }
  return (
    <section className="panel collection-panel">
      <div className="panel-head">
        <h2>
          {
            (
              {
                providers: "Providers",
                models: "模型",
                adapters: "OneBot V11 实例",
                subagents: "外部 Agent 实例",
                plugins: "插件配置",
              } as Record<string, string>
            )[section]
          }
        </h2>
        <button
          onClick={() => {
            setSelected(rows.length);
            setConfig({
              ...config,
              [section]: [
                ...rows,
                {
                  ...structuredClone(defaults[section]),
                  id: uniqueId(defaults[section].id, rows),
                },
              ],
            });
          }}
        >
          <Plus size={14} />
          添加
        </button>
      </div>
      {rows.length === 0 ? (
        <Empty text="添加第一个实例，开始连接" />
      ) : (
        <div className="config-workspace">
          <section className="config-sidebar">
            {rows.map((row, index) => (
              <button
                className={`config-item ${index === selectedIndex ? "selected" : ""}`}
                key={index}
                onClick={() => setSelected(index)}
              >
                <span className={"dot " + (row.enabled ? "on" : "")} />
                <span>
                  <strong>{row.id || "新实例"}</strong>
                  <small>{row.protocol || row.kind || section}</small>
                </span>
              </button>
            ))}
          </section>
          <section className="config-editor">
            {rows.map(
              (row, index) =>
                index === selectedIndex && (
                  <div className="instance" key={index}>
                    <div className="panel-head">
                      <span className={"dot " + (row.enabled ? "on" : "")} />
                      <strong>{row.id || "新实例"}</strong>
                      <span className="muted">
                        {row.protocol || row.kind || ""}
                      </span>
                    </div>
                    <Fields
                      value={row}
                      prefix={section}
                      onChange={(v) => update(index, v)}
                    />
                    <div className="actions">
                      {section === "subagents" && (
                        <button
                          onClick={() =>
                            act(() =>
                              api(`/subagents/${row.id}/detect`, "POST"),
                            )
                          }
                        >
                          检测 CLI
                        </button>
                      )}
                      {section === "plugins" && (
                        <>
                          <button
                            onClick={() =>
                              act(() =>
                                api(`/plugins/${row.id}/reload`, "POST"),
                              )
                            }
                          >
                            重载已保存的配置
                          </button>
                          <button
                            onClick={() =>
                              update(index, {
                                ...row,
                                settings:
                                  row.id === "feishu"
                                    ? { command: ["lark-cli"], config_dir: "" }
                                    : row.id === "thu_learn"
                                      ? {
                                          command: [],
                                          username: "",
                                          password: "",
                                          poll: false,
                                          poll_seconds: 900,
                                        }
                                      : row.id === "email"
                                        ? {
                                            imap_host: "",
                                            imap_port: 993,
                                            imap_security: "ssl",
                                            username: "",
                                            password: "",
                                            mailbox: "INBOX",
                                            poll_seconds: 300,
                                            smtp_host: "",
                                            smtp_port: 465,
                                            notify_to: [],
                                          }
                                        : row.settings,
                              })
                            }
                          >
                            填入配置模板
                          </button>
                        </>
                      )}
                      <button
                        className="danger"
                        onClick={() =>
                          setConfig({
                            ...config,
                            [section]: rows.filter((_, i) => i !== index),
                          })
                        }
                      >
                        <Trash2 size={14} />
                        移除
                      </button>
                    </div>
                  </div>
                ),
            )}
          </section>
        </div>
      )}
    </section>
  );
}
function App() {
  const [token, setToken] = useState(sessionStorage.getItem("token") || "");
  const [logged, setLogged] = useState(!!token);
  const [page, setPage] = useState("overview");
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const [draft, setDraft] = useState<Json>(null);
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api("/health"),
    enabled: logged,
    refetchInterval: 10000,
  });
  const configuration = useQuery({
    queryKey: ["config"],
    queryFn: () => api("/config"),
    enabled: logged,
  });
  const config = draft || configuration.data;
  const queryKind = (
    {
      overview: "activity",
      events: "event",
      routine: "routine",
      subagents: "subagent_run",
      approvals: "approval",
      logs: "activity",
      chat: "reply",
    } as Record<string, string>
  )[page];
  const records = useQuery({
    queryKey: ["records", queryKind],
    queryFn: () => api("/records/" + queryKind),
    enabled: logged && !!queryKind,
    refetchInterval: 4000,
  });
  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api("/records/approval"),
    enabled: logged,
    refetchInterval: 4000,
  });
  const memories = useQuery({
    queryKey: ["memory"],
    queryFn: () => api("/memory"),
    enabled: logged && page === "memory",
  });
  const [chat, setChat] = useState(""),
    [memory, setMemory] = useState(""),
    [title, setTitle] = useState(""),
    [due, setDue] = useState(""),
    [routineKind, setRoutineKind] = useState("todo"),
    [cron, setCron] = useState("30 7 * * *");
  const [session, setSession] = useState("web:owner");
  const history = useQuery({
    queryKey: ["history", session],
    queryFn: () => api("/history?session=" + encodeURIComponent(session)),
    enabled: logged && page === "chat",
    refetchInterval: 2500,
  });
  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api("/records/session"),
    enabled: logged && page === "chat",
    refetchInterval: 10000,
  });
  const commands = useQuery({
    queryKey: ["commands"],
    queryFn: () => api("/commands"),
    enabled: logged && page === "chat",
  });
  const queryError =
    health.error ||
    configuration.error ||
    records.error ||
    approvals.error ||
    memories.error ||
    history.error ||
    sessions.error ||
    commands.error;
  useEffect(() => {
    if (queryError) setNotice({ text: queryError.message, error: true });
  }, [queryError]);
  const pending = (approvals.data || []).filter(
    (x: Json) => x.status === "pending",
  );
  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    try {
      const result = await work();
      setNotice({
        text:
          result && typeof result === "object" && "text" in result
            ? String(result.text)
            : "操作已完成",
        error: false,
      });
      await qc.invalidateQueries();
    } catch (e) {
      setNotice({
        text: String(e instanceof Error ? e.message : e),
        error: true,
      });
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!logged) return;
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    let disposed = false;
    function connect() {
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws`,
      );
      socket.onopen = () => socket.send(sessionStorage.getItem("token") || "");
      socket.onmessage = (e) => {
        if (JSON.parse(e.data).kind !== "heartbeat") {
          void qc.invalidateQueries({ queryKey: ["health"] });
          void qc.invalidateQueries({ queryKey: ["approvals"] });
        }
      };
      socket.onclose = () => {
        if (!disposed) retry = setTimeout(connect, 5000);
      };
    }
    connect();
    return () => {
      disposed = true;
      clearTimeout(retry);
      socket.close();
    };
  }, [logged, qc]);
  useEffect(() => {
    if (!notice || notice.error) return;
    const timer = setTimeout(() => setNotice(null), 5000);
    return () => clearTimeout(timer);
  }, [notice]);
  if (!logged)
    return (
      <div className="login">
        <div className="brand-mark">
          <Sparkles size={28} />
        </div>
        <p className="eyebrow">YOUR PERSONAL AGENT</p>
        <h1>ShiritaiBot</h1>
        <p>从这里，连接你的数字生活。</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            sessionStorage.setItem("token", token);
            void act(async () => {
              await api("/health");
              setLogged(true);
            });
          }}
        >
          <label>
            管理令牌
            <input
              autoFocus
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="粘贴本机管理令牌"
              required
            />
          </label>
          <button className="primary" disabled={busy}>
            进入控制台 <ArrowUpRight size={16} />
          </button>
        </form>
        <p className="muted">
          在终端运行 <code>uv run shiritaibot token</code> 获取。
        </p>
        <ErrorDialog
          message={notice?.error ? notice.text : null}
          onClose={() => setNotice(null)}
        />
      </div>
    );
  const active = pages.find((x) => x[0] === page)!;
  const save = () =>
    act(async () => {
      await api("/config", "PUT", config);
      setDraft((current: Json) => (current === config ? null : current));
    });
  function approvalCard(row: Json) {
    return (
      <article className="approval" key={row.id}>
        <div>
          <span className="eyebrow">需要你确认 · {row.id}</span>
          <h3>{row.tool}</h3>
          <pre>{JSON.stringify(row.args, null, 2)}</pre>
          <p className="muted">
            {row.scope} ·{" "}
            {new Date(row.expires * 1000).toLocaleString("zh-CN", {
              timeZone: "Asia/Shanghai",
            })}{" "}
            前有效
          </p>
        </div>
        <div className="actions">
          <button
            className="primary"
            onClick={() =>
              void act(() =>
                api("/approvals/" + row.id, "POST", { allow: true }),
              )
            }
          >
            <Check size={14} />
            允许这一次
          </button>
          <button
            onClick={() =>
              void act(() =>
                api("/approvals/" + row.id, "POST", { allow: false }),
              )
            }
          >
            <X size={14} />
            拒绝
          </button>
        </div>
      </article>
    );
  }
  return (
    <div className="layout">
      <aside>
        <a className="brand" onClick={() => setPage("overview")}>
          <span className="brand-mark">
            <Sparkles size={20} />
          </span>
          <span>
            ShiritaiBot<small>PERSONAL WORKSPACE</small>
          </span>
        </a>
        <div className="workspace">
          <span className="avatar">知</span>
          <div>
            <strong>我的工作空间</strong>
            <small>Owner · 私人助理</small>
          </div>
          <span className="dot on" />
        </div>
        <nav>
          {pages.map(([id, label, Icon]) => (
            <button
              key={id}
              className={page === id ? "active" : ""}
              onClick={() => setPage(id)}
            >
              <Icon size={18} />
              <span>{label}</span>
              {id === "approvals" && pending.length > 0 && (
                <b>{pending.length}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className={"dot " + (health.isSuccess ? "on" : "")} />
          {health.isSuccess ? "系统运行中" : "连接未就绪"}
          <small>v0.1.0</small>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <span className="muted">我的工作空间</span>
            <ChevronRight size={13} />
            <span>{active[1]}</span>
          </div>
          <div>
            <span className="local-badge">LOCAL FIRST</span>
            <button
              onClick={() => {
                sessionStorage.removeItem("token");
                setLogged(false);
                qc.clear();
              }}
            >
              退出
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-title">
            <div>
              <p className="eyebrow">SHIRITAI / {page.toUpperCase()}</p>
              <h1>{active[1]}</h1>
              <p className="subtitle">
                {(
                  {
                    overview: "让信息找到你，让想法成为行动。",
                    models: "连接不同的模型，为每种任务选择合适的能力。",
                    adapters:
                      "一个助手，多个连接。QQ 私聊属于你，群聊独立运行。",
                    approvals: "每一次重要操作，都由你掌握。",
                    memory: "把值得记住的事，留给下一次对话。",
                    events: "查看事件、分诊理由和通知结果。",
                  } as Record<string, string>
                )[page] || "查看、配置和管理你的私人助手。"}
              </p>
            </div>
            {draft && (
              <button className="primary" disabled={busy} onClick={save}>
                <Save size={16} />
                保存配置
              </button>
            )}
          </div>
          <ErrorDialog
            message={notice?.error ? notice.text : null}
            onClose={() => setNotice(null)}
          />
          {notice && !notice.error && (
            <div role="status" className="success-toast">
              <span>{notice.text}</span>
              <button aria-label="关闭通知" onClick={() => setNotice(null)}>
                <X size={15} />
              </button>
            </div>
          )}
          {page === "overview" && (
            <>
              <Dashboard
                api={api}
                onError={(e) => setNotice({ text: e.message, error: true })}
              />

              <div className="hero">
                <div>
                  <span className="eyebrow">
                    A LITTLE MORE AWARE, EVERY DAY
                  </span>
                  <h2>一切有序，随时待命。</h2>
                  <p>连接消息、记忆与行动。重要的事情，不再错过。</p>
                  <button
                    onClick={() =>
                      setPage(
                        configuration.data?.providers.length
                          ? "chat"
                          : "models",
                      )
                    }
                  >
                    开始配置 <ArrowUpRight size={16} />
                  </button>
                </div>
                <div className="orbit">
                  <Sparkles size={43} />
                  <i />
                  <b />
                  <em />
                </div>
              </div>
              <div className="metrics">
                {[
                  [
                    "协议连接",
                    Object.keys(health.data?.adapters || {}).length,
                    "已配置的 OneBot",
                  ],
                  [
                    "可用工具",
                    health.data?.tools.length || 0,
                    "内置能力与插件",
                  ],
                  ["等待审批", pending.length, "需要你的确认"],
                  ["子代理任务", health.data?.subagents || 0, "正在执行或排队"],
                ].map(([label, note, sub]) => (
                  <div className="metric" key={label}>
                    <span>{label}</span>
                    <strong>{note}</strong>
                    <small>{sub}</small>
                  </div>
                ))}
              </div>
              {pending.length > 0 && (
                <section className="panel">
                  <h2>待你确认</h2>
                  {pending.map(approvalCard)}
                </section>
              )}
              <div className="two-col">
                <section className="panel">
                  <div className="panel-head">
                    <h2>连接状态</h2>
                    <Network size={17} />
                  </div>
                  {Object.entries(health.data?.adapters || {}).length ? (
                    Object.entries(health.data.adapters).map(([id, status]) => (
                      <div className="list-row" key={id}>
                        <span
                          className={
                            "dot " + (status === "connected" ? "on" : "")
                          }
                        />
                        <strong>{id}</strong>
                        <span className="tag">{String(status)}</span>
                      </div>
                    ))
                  ) : (
                    <Empty text="还没有连接 QQ，在协议连接中添加实例" />
                  )}
                  <button
                    className="text-button"
                    onClick={() => setPage("adapters")}
                  >
                    管理连接 <ArrowUpRight size={14} />
                  </button>
                </section>
                <section className="panel">
                  <div className="panel-head">
                    <h2>最近活动</h2>
                    <Activity size={17} />
                  </div>
                  {records.data?.slice(0, 5).map((r: Json) => (
                    <div className="activity-row" key={r.id}>
                      <span className="dot on" />
                      <div>
                        <strong>{r.kind}</strong>
                        <p>{r.detail}</p>
                      </div>
                    </div>
                  ))}
                  {!records.data?.length && (
                    <Empty text="配置完成后，助手的活动会显示在这里" />
                  )}
                </section>
              </div>
            </>
          )}
          {config && page === "models" && (
            <ProviderWorkspace {...{ config, setConfig: setDraft, api, act }} />
          )}
          {config && page === "adapters" && (
            <Collection
              section="adapters"
              {...{ config, setConfig: setDraft, act }}
            />
          )}
          {config && page === "subagents" && (
            <>
              <Collection
                section="subagents"
                {...{ config, setConfig: setDraft, act }}
              />
              <section className="panel">
                <h2>任务记录</h2>
                {records.data?.map((r: Json) => (
                  <article className="record" key={r.id}>
                    <div className="panel-head">
                      <strong>{r.instance}</strong>
                      <span className="tag">{r.status}</span>
                    </div>
                    <p>{r.result || r.error || r.workspace}</p>
                    {["running", "queued"].includes(r.status) && (
                      <button
                        onClick={() =>
                          void act(() =>
                            api("/subagents/" + r.id + "/cancel", "POST"),
                          )
                        }
                      >
                        停止
                      </button>
                    )}
                    <Details value={r} />
                  </article>
                ))}
                {!records.data?.length && (
                  <Empty text="在对话中委派任务，审批后会出现在这里" />
                )}
              </section>
            </>
          )}
          {config && page === "plugins" && (
            <>
              <Collection
                section="plugins"
                {...{ config, setConfig: setDraft, act }}
              />
            </>
          )}
          {page === "approvals" && (
            <>
              <section className="panel">
                <h2>待审批 · {pending.length}</h2>
                {pending.length ? (
                  pending.map(approvalCard)
                ) : (
                  <Empty text="当前没有待审批操作" />
                )}
              </section>
              {config && (
                <section className="panel">
                  <h2>执行策略</h2>
                  <p className="muted">
                    Shell 默认关闭。启用后，每次调用仍需批准。子进程以运行 Bot
                    的系统账号执行。
                  </p>
                  <Fields
                    value={config.policy}
                    onChange={(v) => setDraft({ ...config, policy: v })}
                  />
                </section>
              )}
            </>
          )}
          {page === "chat" && (
            <>
              <div className="inline">
                <select
                  aria-label="会话"
                  value={session}
                  onChange={(e) => setSession(e.target.value)}
                >
                  {Array.from(
                    new Set([
                      "web:owner",
                      ...(sessions.data || []).map((s: Json) => s.id),
                      session,
                    ]),
                  ).map((s) => (
                    <option key={String(s)} value={String(s)}>
                      {String(s)}
                    </option>
                  ))}
                </select>
                <button onClick={() => setSession("web:" + Date.now())}>
                  <Plus size={14} />
                  新会话
                </button>
              </div>
              <section className="panel chat-panel">
                {!history.data?.length && (
                  <Empty text="从一句话开始。请先配置模型路由。" />
                )}
                {history.data?.map((m: Json, i: number) => (
                  <div className={"message " + m.role} key={i}>
                    <small>
                      {m.role === "user"
                        ? "你 / 上下文"
                        : m.role === "assistant"
                          ? "ShiritaiBot"
                          : "工具结果"}
                    </small>
                    {m.role === "tool" || m.tool_calls ? (
                      <Details value={m} />
                    ) : (
                      <p>{m.content?.split("\n<context")[0]}</p>
                    )}
                  </div>
                ))}
                {records.data
                  ?.filter((r: Json) => r.scope === session)
                  .slice(0, 3)
                  .reverse()
                  .map((r: Json) => (
                    <div className="message delivered" key={r.id}>
                      <small>已投递</small>
                      <p>{r.text}</p>
                    </div>
                  ))}
              </section>
              {session.startsWith("web:") && (
                <form
                  className="composer"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      await api("/messages", "POST", { text: chat, session });
                      setChat("");
                    });
                  }}
                >
                  {chat.startsWith("/") && !chat.includes(" ") && (
                    <div className="command-menu" aria-label="斜杠命令">
                      {commands.data
                        ?.filter((c: Json) =>
                          c.name.startsWith(chat.slice(1).toLowerCase()),
                        )
                        .map((c: Json) => (
                          <button
                            type="button"
                            key={c.name}
                            onClick={() => setChat(`/${c.name} `)}
                          >
                            <strong>/{c.name}</strong>
                            <span>{c.description}</span>
                          </button>
                        ))}
                    </div>
                  )}
                  <textarea
                    aria-label="消息"
                    value={chat}
                    onChange={(e) => setChat(e.target.value)}
                    placeholder="告诉 ShiritaiBot 你想做什么，或输入 / 查看命令…"
                    rows={2}
                  />
                  <button
                    type="button"
                    aria-label="停止当前任务"
                    onClick={() =>
                      void act(() =>
                        api("/messages", "POST", { text: "/stop", session }),
                      )
                    }
                  >
                    <Square size={16} />
                  </button>
                  <button
                    className="primary"
                    disabled={!chat.trim() || busy}
                    aria-label="发送消息"
                  >
                    <Send size={17} />
                  </button>
                </form>
              )}
            </>
          )}
          {page === "memory" && (
            <>
              <section className="panel">
                <h2>记住一件事</h2>
                <form
                  className="inline"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      await api("/memory", "POST", { content: memory });
                      setMemory("");
                    });
                  }}
                >
                  <input
                    value={memory}
                    onChange={(e) => setMemory(e.target.value)}
                    placeholder="例如：解释数学时，先举例再推导"
                    required
                  />
                  <button className="primary">
                    <Plus size={16} />
                    记住
                  </button>
                </form>
              </section>
              <section className="panel">
                <h2>记忆库</h2>
                {memories.data?.map((r: Json) => (
                  <article className="record" key={r.id}>
                    <div className="panel-head">
                      <span className="tag">
                        {r.kind} · {r.visibility}
                      </span>
                      <span className="muted">{r.status}</span>
                    </div>
                    <p>{r.content}</p>
                    {r.status === "active" && (
                      <button
                        onClick={() =>
                          void act(() =>
                            api("/memory/" + r.id + "/archive", "POST"),
                          )
                        }
                      >
                        归档
                      </button>
                    )}
                  </article>
                ))}
                {!memories.data?.length && (
                  <Empty text="明确记下的事实会存入这里，并用于私人对话" />
                )}
              </section>
            </>
          )}
          {page === "routine" && (
            <>
              <section className="panel">
                <h2>安排下一件事</h2>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      await api("/routines", "POST", {
                        kind: routineKind,
                        title,
                        due: due ? new Date(due).toISOString() : null,
                        cron: routineKind === "job" ? cron : "",
                      });
                      setTitle("");
                      setDue("");
                    });
                  }}
                >
                  <div className="fields">
                    <label>
                      类型
                      <select
                        value={routineKind}
                        onChange={(e) => setRoutineKind(e.target.value)}
                      >
                        <option value="todo">待办</option>
                        <option value="event">日程</option>
                        <option value="reminder">提醒</option>
                        <option value="job">定时 Agent 任务</option>
                      </select>
                    </label>
                    <label>
                      内容
                      <input
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                        required
                      />
                    </label>
                    {routineKind === "job" ? (
                      <label>
                        Cron（Asia/Shanghai）
                        <input
                          value={cron}
                          onChange={(e) => setCron(e.target.value)}
                        />
                      </label>
                    ) : (
                      <label>
                        时间（浏览器本地时区）
                        <input
                          type="datetime-local"
                          value={due}
                          onChange={(e) => setDue(e.target.value)}
                          required={["event", "reminder"].includes(routineKind)}
                        />
                      </label>
                    )}
                  </div>
                  <button className="primary">
                    <Plus size={16} />
                    创建
                  </button>
                </form>
              </section>
              <section className="panel">
                <h2>日程与任务</h2>
                {records.data?.map((r: Json) => (
                  <article className="record" key={r.id}>
                    <div className="panel-head">
                      <strong>{r.title}</strong>
                      <span className="tag">
                        {r.kind} · {r.status}
                      </span>
                    </div>
                    <p className="muted">
                      {r.due
                        ? new Date(r.due).toLocaleString("zh-CN", {
                            timeZone: "Asia/Shanghai",
                          })
                        : r.cron || "没有截止时间"}
                    </p>
                    <div className="actions">
                      <button
                        onClick={() =>
                          void act(() =>
                            api("/routines/" + r.id + "/completed", "POST"),
                          )
                        }
                      >
                        完成
                      </button>
                      <button
                        onClick={() =>
                          void act(() =>
                            api("/routines/" + r.id + "/cancelled", "POST"),
                          )
                        }
                      >
                        取消
                      </button>
                    </div>
                  </article>
                ))}
                {!records.data?.length && <Empty text="还没有日程或任务" />}
              </section>
            </>
          )}
          {page === "events" && (
            <section className="panel">
              <h2>事件时间线</h2>
              {records.data?.map((r: Json) => (
                <article className="record" key={r.id}>
                  <div className="panel-head">
                    <strong>{r.event.title}</strong>
                    <span className="tag">
                      {r.decision?.decision || r.status}
                    </span>
                  </div>
                  <p>{r.decision?.reason}</p>
                  <small>
                    {r.event.source} · {r.event.kind}
                  </small>
                  <Details value={r} />
                </article>
              ))}
              {!records.data?.length && (
                <Empty text="来自插件、提醒和子代理的事件会汇集在这里" />
              )}
            </section>
          )}
          {page === "logs" && (
            <section className="panel">
              {records.data?.map((r: Json) => (
                <div className="log-row" key={r.id}>
                  <time>
                    {new Date(r.at).toLocaleTimeString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                    })}
                  </time>
                  <span className="tag">{r.kind}</span>
                  <span>{r.detail}</span>
                </div>
              ))}
              {!records.data?.length && <Empty />}
            </section>
          )}
          {config && page === "settings" && (
            <>
              <section className="panel">
                <h2>基础设置</h2>
                <Fields
                  value={{
                    persona: config.persona,
                    tavily_key: config.tavily_key,
                    concurrency: config.concurrency,
                  }}
                  onChange={(v) => setDraft({ ...config, ...v })}
                />
              </section>
              <section className="panel">
                <h2>主动性与打扰策略</h2>
                <Fields
                  value={config.proactive}
                  onChange={(v) => setDraft({ ...config, proactive: v })}
                />
              </section>
            </>
          )}
          {draft && (
            <div className="save-bar">
              <span>配置有未保存的修改</span>
              <button onClick={() => setDraft(null)}>放弃修改</button>
              <button className="primary" disabled={busy} onClick={save}>
                <Save size={15} />
                保存并应用
              </button>
            </div>
          )}
        </div>
        <footer>
          ShiritaiBot <span>·</span> 为你而运行。
          <span className="muted">本地数据 · 明确权限 · 持续记忆</span>
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
