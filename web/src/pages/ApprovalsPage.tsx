import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApprovalCard } from "../components/ApprovalCard";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/FieldForm";
import { POLICY_FIELDS } from "../config/schema";
import { ScopeToolsField } from "../components/ScopeToolsField";
import type { ConfigPageProps } from "./common";

export function ApprovalsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const [busy, setBusy] = useState(false);
  const approvals = useQuery({
    queryKey: ["records", "approval"],
    queryFn: () => client.records("approval"),
    refetchInterval: 4000,
  });
  const pending = (approvals.data ?? []).filter(
    (row) => row.status === "pending",
  );

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
        <h2>待审批 · {pending.length}</h2>
        {approvals.error && (
          <p className="error-text">读取审批失败：{approvals.error.message}</p>
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
          <EmptyState text="当前没有待审批操作。" />
        )}
      </section>
      <section className="panel">
        <h2>执行策略</h2>
        <p className="muted">
          Shell 默认关闭。启用后，每次调用仍需批准，子进程以运行 Bot
          的系统账号执行，工作目录本身不是沙箱。
        </p>
        <div className="field-grid">
          {POLICY_FIELDS.map((spec) => (
            <Field
              key={spec.key}
              spec={spec}
              value={config.policy[spec.key as keyof typeof config.policy]}
              onChange={(next) =>
                setConfig({
                  ...config,
                  policy: { ...config.policy, [spec.key]: next },
                })
              }
            />
          ))}
        </div>
        <ScopeToolsField
          value={config.policy.scope_tools}
          onChange={(scope_tools) =>
            setConfig({ ...config, policy: { ...config.policy, scope_tools } })
          }
        />
      </section>
    </>
  );
}
