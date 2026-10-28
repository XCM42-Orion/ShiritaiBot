import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useQuery } from "@tanstack/react-query";
import { Details } from "../components/Details";
import { EmptyState } from "../components/EmptyState";
import { InstanceCollection } from "../components/InstanceCollection";
import { SUBAGENT_FIELDS, createSubagent } from "../config/schema";
import type { ConfigPageProps } from "./common";

export function SubagentsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const runs = useQuery({
    queryKey: ["records", "subagent_run"],
    queryFn: () => client.records("subagent_run"),
    refetchInterval: 4000,
  });

  return (
    <>
      <InstanceCollection
        title="外部 Agent 实例"
        items={config.subagents}
        create={() => createSubagent(config.subagents)}
        onChange={(subagents) => setConfig({ ...config, subagents })}
        fields={SUBAGENT_FIELDS}
        subtitle={(item) => item.kind + " · " + item.permission}
        emptyText="添加 Codex、Claude Code 或自定义 CLI 实例。"
        renderActions={(item) => (
          <button
            type="button"
            onClick={() =>
              void act(() => client.detectSubagent(item.id), "已检测到 CLI")
            }
          >
            检测 CLI
          </button>
        )}
      />
      <section className="panel">
        <h2>任务记录</h2>
        {runs.error && (
          <QueryErrorNotice message={`读取任务失败：${runs.error.message}`} />
        )}
        {runs.data?.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <strong>{row.instance}</strong>
              <span className="tag">{row.status}</span>
            </div>
            <p>{row.result || row.error || row.workspace}</p>
            {(row.status === "running" || row.status === "queued") && (
              <button
                type="button"
                onClick={() =>
                  void act(() => client.cancelSubagent(row.id), "已请求停止")
                }
              >
                停止
              </button>
            )}
            <Details value={row} />
          </article>
        ))}
        {!runs.data?.length && !runs.error && (
          <EmptyState text="在对话中委派任务，审批后会出现在这里。" />
        )}
      </section>
    </>
  );
}
