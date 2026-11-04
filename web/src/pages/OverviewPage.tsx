import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowUpRight, CheckCircle2, Circle, Network } from "lucide-react";
import type { ApiClient } from "../api/client";
import type { PageId } from "../navigation";
import { ApprovalCard } from "../components/ApprovalCard";
import { EmptyState } from "../components/EmptyState";
import type { Action } from "./common";
import type { NavigationProps } from "./common";
import { Dashboard } from "./Dashboard";
import { formatDateTime } from "../lib/utils";

function FirstRunChecklist({
  client,
  onNavigate,
}: {
  client: ApiClient;
  onNavigate: (page: PageId) => void;
}) {
  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => client.config(),
  });
  const data = config.data;
  if (!data) return null;
  const steps: { label: string; done: boolean; page: PageId }[] = [
    {
      label: "添加并启用提供商（API Key）",
      done: data.providers.some((provider) => provider.enabled),
      page: "models",
    },
    {
      label: "添加并启用模型",
      done: data.models.some((model) => model.enabled),
      page: "models",
    },
    {
      label: "给 chat / agent 路由选模型",
      done: ["chat", "agent", "agent_chat"].every(
        (route) => (data.routes[route] ?? []).length > 0,
      ),
      page: "models",
    },
    {
      label: "填写网页搜索密钥（可选）",
      done:
        !data.web_search.enabled ||
        Boolean(data.web_search[data.web_search.provider].api_key),
      page: "settings",
    },
    {
      label: "连接 NapCat（适配器）",
      done: data.adapters.some((adapter) => adapter.enabled),
      page: "adapters",
    },
  ];
  if (steps.every((step) => step.done)) return null;
  return (
    <section className="panel">
      <h2>开始使用</h2>
      <p className="muted">按顺序完成下面几项，助手就能接消息了。</p>
      <ul className="checklist">
        {steps.map((step) => (
          <li key={step.label} className={step.done ? "done" : undefined}>
            {step.done ? (
              <CheckCircle2 size={16} aria-label="已完成" />
            ) : (
              <Circle size={16} aria-label="未完成" />
            )}
            <span className={step.done ? "muted" : ""}>{step.label}</span>
            {!step.done && (
              <button
                type="button"
                className="text-button"
                onClick={() => onNavigate(step.page)}
              >
                去设置
                <ArrowUpRight size={14} />
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function OverviewPage({
  client,
  act,
  onNavigate,
}: { client: ApiClient; act: Action } & NavigationProps) {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => client.health(),
    refetchInterval: 10000,
  });
  const approvals = useQuery({
    queryKey: ["records", "approval"],
    queryFn: () => client.records("approval"),
    refetchInterval: 4000,
  });
  const activity = useQuery({
    queryKey: ["records", "activity"],
    queryFn: () => client.records("activity"),
    refetchInterval: 4000,
  });
  const pending = (approvals.data ?? []).filter(
    (row) => row.status === "pending" && row.reviewer !== "model",
  );
  const adapters = Object.entries(health.data?.adapters ?? {});

  return (
    <>
      <FirstRunChecklist client={client} onNavigate={onNavigate} />
      <div className="metrics">
        {(
          [
            ["协议连接", adapters.length, "已配置的 OneBot 实例"],
            ["可用工具", health.data?.tools.length ?? 0, "内置能力与插件"],
            ["等待审批", pending.length, "需要你的确认"],
            ["子代理任务", health.data?.subagents ?? 0, "正在执行或排队"],
          ] as const
        ).map(([label, value, note]) => (
          <div className="metric" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{note}</small>
          </div>
        ))}
      </div>
      {pending.length > 0 && (
        <section className="panel">
          <h2>待你确认 · {pending.length}</h2>
          {pending.map((row) => (
            <ApprovalCard
              key={row.id}
              approval={row}
              busy={false}
              onResolve={(allow) =>
                void act(
                  () => client.resolveApproval(row.id, allow),
                  allow ? "已允许本次调用" : "已拒绝本次调用",
                )
              }
            />
          ))}
        </section>
      )}
      <Dashboard client={client} />
      <div className="two-col">
        <section className="panel">
          <div className="panel-head">
            <h2>连接状态</h2>
            <Network size={17} aria-hidden="true" />
          </div>
          {adapters.length ? (
            adapters.map(([id, status]) => (
              <div className="list-row" key={id}>
                <span
                  className={"dot " + (status === "connected" ? "on" : "")}
                  aria-hidden="true"
                />
                <strong>{id}</strong>
                <span className="tag">{String(status)}</span>
              </div>
            ))
          ) : (
            <EmptyState text="还没有连接 QQ，在「协议连接」中添加实例。" />
          )}
          <button
            type="button"
            className="text-button"
            onClick={() => onNavigate("adapters")}
          >
            管理连接
            <ArrowUpRight size={14} />
          </button>
        </section>
        <section className="panel">
          <div className="panel-head">
            <h2>最近活动</h2>
            <Activity size={17} aria-hidden="true" />
          </div>
          {activity.data?.length ? (
            activity.data.slice(0, 5).map((row) => (
              <div className="activity-row" key={row.id}>
                <span className="dot on" aria-hidden="true" />
                <div>
                  <strong>{row.kind}</strong>
                  <p>{row.detail}</p>
                </div>
              </div>
            ))
          ) : (
            <EmptyState text="配置完成后，助手的活动会显示在这里。" />
          )}
          <p className="muted activity-time">
            {activity.data?.[0]
              ? "最近记录：" + formatDateTime(activity.data[0].at)
              : ""}
          </p>
        </section>
      </div>
    </>
  );
}
