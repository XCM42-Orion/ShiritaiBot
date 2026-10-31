import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApprovalCard } from "../components/ApprovalCard";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/FieldForm";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { ScopeToolsField } from "../components/ScopeToolsField";
import { POLICY_FIELDS } from "../config/schema";
import type {
  PermissionPreset,
  SessionPolicy,
  ToolInfo,
  ToolPermission,
  ToolPolicy,
} from "../api/types";
import type { ConfigPageProps } from "./common";

const PRESETS: Record<PermissionPreset, string> = {
  all_manual: "一切工具需要人工审批",
  safe_manual: "安全工具无需审批，其他人工审批",
  safe_auto: "安全工具无需审批，其他由模型自动审批",
  full_access: "完全访问：所有工具无需审批",
};
const PERMISSIONS: Record<ToolPermission, string> = {
  deny: "禁止使用",
  manual: "需要人工审批",
  auto: "模型自动审批",
  allow: "无需审批",
};
type Target =
  | "session"
  | "owner_default"
  | "private_default"
  | "group_default"
  | "proactive_default";

function inherited(policy: ToolPolicy, tool?: ToolInfo): ToolPermission {
  if (policy.preset === "full_access") return "allow";
  if (policy.preset === "all_manual") return "manual";
  return tool?.safe
    ? "allow"
    : policy.preset === "safe_auto"
      ? "auto"
      : "manual";
}

export function ApprovalsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const [target, setTarget] = useState<Target>("session");
  const [session, setSession] = useState("web:owner");
  const [sessionInput, setSessionInput] = useState("web:owner");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const catalog = useQuery({
    queryKey: ["tools"],
    queryFn: () => client.tools(),
    refetchInterval: 10000,
  });
  const sessions = useQuery({
    queryKey: ["records", "session"],
    queryFn: () => client.records("session"),
  });
  const approvals = useQuery({
    queryKey: ["records", "approval"],
    queryFn: () => client.records("approval"),
    refetchInterval: 4000,
  });
  const pending = (approvals.data ?? []).filter(
    (row) => row.status === "pending" && row.reviewer !== "model",
  );
  const reviews = (approvals.data ?? [])
    .filter((row) => row.reviewer === "model")
    .slice(0, 20);
  const policy = config.policy;
  const row = sessions.data?.find((row) => row.id === session);
  const owner =
    session.startsWith("web:") ||
    row?.owner ||
    config.adapters.some((a) => session === `private:${a.id}:${a.owner_id}`);
  const fallback = session.startsWith("group:")
    ? policy.group_default
    : owner
      ? policy.owner_default
      : policy.private_default;
  const profile: ToolPolicy | SessionPolicy =
    target === "session"
      ? (policy.sessions[session] ?? fallback)
      : policy[target];
  const mode =
    target === "proactive_default" ? "agent" : (profile as SessionPolicy).mode;
  const knownSessions = [
    ...new Set([
      "web:owner",
      ...Object.keys(policy.sessions),
      ...(sessions.data ?? [])
        .filter(
          (row) => !row.background && /^(web|private|group):/.test(row.id),
        )
        .map((row) => row.id),
    ]),
  ].sort();
  const tools = catalog.data?.tools ?? [];
  const toolNames = [
    ...new Set([
      ...tools.map((tool) => tool.name),
      ...Object.keys(profile.tools),
    ]),
  ].sort();

  function update(next: ToolPolicy | SessionPolicy) {
    setConfig({
      ...config,
      policy:
        target === "session"
          ? {
              ...policy,
              sessions: {
                ...policy.sessions,
                [session]: next as SessionPolicy,
              },
            }
          : { ...policy, [target]: next },
    });
  }

  async function resolve(id: string, allow: boolean) {
    setBusy(true);
    try {
      await act(
        () => client.resolveApproval(id, allow),
        allow ? "已允许" : "已拒绝",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="panel">
        <h2>工具可用性</h2>
        <p className="muted">
          每个 Main 会话都可选择 Chat 或 Agent。Chat 仅对话；Agent
          按下方权限调用工具，包括 Shell。配置通过底部保存条保存。
        </p>
        <div className="field-grid">
          <label className="field">
            配置范围
            <select
              value={target}
              onChange={(event) => setTarget(event.target.value as Target)}
            >
              <option value="session">指定 Main 会话</option>
              <option value="owner_default">主人私聊 / WebUI 默认</option>
              <option value="private_default">其他私聊默认</option>
              <option value="group_default">群聊默认</option>
              <option value="proactive_default">新 Proactive Loop 默认</option>
            </select>
          </label>
          {target === "session" && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setSession(sessionInput.trim());
              }}
            >
              <label className="field">
                Main 会话 ID
                <input
                  list="main-sessions"
                  value={sessionInput}
                  pattern="(web|private|group):[^\s]{1,200}"
                  required
                  onChange={(event) => setSessionInput(event.target.value)}
                />
                <datalist id="main-sessions">
                  {knownSessions.map((id) => (
                    <option key={id} value={id} />
                  ))}
                </datalist>
              </label>
              <button type="submit">选择会话</button>
            </form>
          )}
          {target !== "proactive_default" && (
            <label className="field">
              会话模式
              <select
                value={mode}
                onChange={(event) =>
                  update({
                    ...profile,
                    mode: event.target.value as SessionPolicy["mode"],
                  })
                }
              >
                <option value="chat">Chat · 基础对话</option>
                <option value="agent">Agent · 允许工具调用</option>
              </select>
            </label>
          )}
          <label className="field">
            权限预设
            <select
              aria-label="权限预设"
              value={profile.preset}
              onChange={(event) =>
                update({
                  ...profile,
                  preset: event.target.value as PermissionPreset,
                  tools: {},
                })
              }
            >
              {Object.entries(PRESETS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <small>切换预设会清除当前范围的逐工具覆盖。</small>
          </label>
        </div>
        {target === "session" && (
          <div className="actions">
            <span className="muted">
              {session} ·{" "}
              {policy.sessions[session]
                ? "已单独配置，作为该会话的默认策略"
                : "继承默认策略"}
            </span>
            {policy.sessions[session] && (
              <button
                type="button"
                onClick={() => {
                  const next = { ...policy.sessions };
                  delete next[session];
                  setConfig({
                    ...config,
                    policy: { ...policy, sessions: next },
                  });
                }}
              >
                恢复继承默认
              </button>
            )}
          </div>
        )}
        {target === "proactive_default" && (
          <p className="muted">
            仅新建的 Proactive Loop
            使用此默认值，运行中的任务保留创建时的权限快照。人工审批会发送给主人，并等待审批或超时。
          </p>
        )}
        {mode === "chat" && (
          <p className="muted">
            当前为 Chat
            模式，仅基础对话工具（如时间、网页搜索、群聊上下文查询）按下方权限可用；其余扩展工具在切换为
            Agent 后使用。
          </p>
        )}
        {catalog.error && (
          <QueryErrorNotice
            message={`读取工具失败：${catalog.error.message}`}
          />
        )}
        {sessions.error && (
          <QueryErrorNotice
            message={`读取会话失败：${sessions.error.message}`}
          />
        )}
        <label className="field">
          搜索工具
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="工具名、插件或说明"
          />
        </label>
        <div className="tool-permission-list">
          {toolNames
            .filter((name) => {
              const tool = tools.find((tool) => tool.name === name);
              return `${name} ${tool?.plugin ?? ""} ${tool?.description ?? ""}`
                .toLowerCase()
                .includes(search.toLowerCase());
            })
            .map((name) => {
              const tool = tools.find((tool) => tool.name === name);
              const permission =
                profile.tools[name] ?? inherited(profile, tool);
              return (
                <article className="tool-permission-row" key={name}>
                  <div>
                    <strong>{name}</strong>
                    <span className="badge">{tool?.plugin ?? "未加载"}</span>
                    <span className="badge">
                      {tool?.safe
                        ? "安全"
                        : tool
                          ? `非安全 · ${tool.risk}`
                          : "安全性未知"}
                    </span>
                    <p className="muted">
                      {tool?.description ??
                        "插件未加载，保留的权限配置将在工具重新注册后使用。"}
                    </p>
                    <small className="muted">
                      {!tool || (mode === "chat" && !tool.chat)
                        ? "当前不可用"
                        : `权限：${PERMISSIONS[permission]}`}
                    </small>
                  </div>
                  <label className="field">
                    {name} 权限
                    <select
                      value={profile.tools[name] ?? "inherit"}
                      onChange={(event) => {
                        const next = { ...profile.tools };
                        if (event.target.value === "inherit") delete next[name];
                        else next[name] = event.target.value as ToolPermission;
                        update({ ...profile, tools: next });
                      }}
                    >
                      <option value="inherit">
                        跟随预设 · {PERMISSIONS[inherited(profile, tool)]}
                      </option>
                      {Object.entries(PERMISSIONS).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                </article>
              );
            })}
          {!catalog.isPending && !toolNames.length && (
            <EmptyState text="尚无已注册工具。" />
          )}
        </div>
      </section>
      <section className="panel">
        <h2>审批与执行设置</h2>
        <p className="muted">
          自动审批模型在“模型与服务 → 工具自动审批模型”路由中设置，可配置备用模型和
          Jev 判断模型；Jev 拒绝或把握不足时可交给路由里的文本模型复核。最终拒绝、
          超时或返回无效时均不执行。
        </p>
        <div className="field-grid">
          {POLICY_FIELDS.filter((spec) => spec.key !== "denied_tools").map(
            (spec) => (
              <Field
                key={spec.key}
                spec={spec}
                value={policy[spec.key as keyof typeof policy]}
                onChange={(next) =>
                  setConfig({
                    ...config,
                    policy: { ...policy, [spec.key]: next },
                  })
                }
              />
            ),
          )}
        </div>
        <details>
          <summary>全局限制（兼容已有配置）</summary>
          <p className="muted">
            全局禁用与作用域白名单仍是额外限制；命中时会阻止工具调用。
          </p>
          {POLICY_FIELDS.filter((spec) => spec.key === "denied_tools").map(
            (spec) => (
              <Field
                key={spec.key}
                spec={spec}
                value={policy.denied_tools}
                onChange={(next) =>
                  setConfig({
                    ...config,
                    policy: { ...policy, denied_tools: next as string[] },
                  })
                }
              />
            ),
          )}
          <ScopeToolsField
            value={policy.scope_tools}
            onChange={(scope_tools) =>
              setConfig({ ...config, policy: { ...policy, scope_tools } })
            }
          />
        </details>
      </section>
      <section className="panel">
        <h2>待人工审批 · {pending.length}</h2>
        {approvals.error && (
          <QueryErrorNotice
            message={`读取审批失败：${approvals.error.message}`}
          />
        )}
        {pending.length ? (
          pending.map((row) => (
            <ApprovalCard
              key={row.id}
              approval={row}
              busy={busy}
              onResolve={(allow) => void resolve(row.id, allow)}
            />
          ))
        ) : (
          <EmptyState text="当前没有待人工审批操作。" />
        )}
      </section>
      <section className="panel">
        <h2>模型自动审批记录</h2>
        {reviews.length ? (
          reviews.map((row) => (
            <article className="tool-permission-row" key={row.id}>
              <div>
                <strong>{row.tool}</strong>
                <span className="badge">
                  {row.status === "approved"
                    ? "已批准"
                    : row.status === "denied"
                      ? "已拒绝"
                      : row.status}
                </span>
                <p className="muted">
                  {row.scope} · {row.reason ?? "正在审查"}
                </p>
                <details>
                  <summary>调用参数</summary>
                  <pre>{JSON.stringify(row.args, null, 2)}</pre>
                </details>
              </div>
            </article>
          ))
        ) : (
          <EmptyState text="暂无模型自动审批记录。" />
        )}
      </section>
    </>
  );
}
