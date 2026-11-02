import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { InstanceCollection } from "../components/InstanceCollection";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { createMcpServer, mcpFields, mcpSummary } from "../config/schema";
import type { McpServerStatus } from "../api/types";
import type { ConfigPageProps } from "./common";

const statuses: Record<McpServerStatus["status"], string> = {
  connected: "已连接",
  connecting: "连接中",
  error: "连接失败",
  disabled: "未启用",
};

export function McpPage({ client, config, setConfig, act }: ConfigPageProps) {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["mcp"],
    queryFn: () => client.mcpServers(),
    refetchInterval: 10000,
  });
  const rows = status.data ?? [];

  return (
    <>
      <InstanceCollection
        title="MCP 服务器"
        items={config.mcp_servers}
        create={() => createMcpServer(config.mcp_servers)}
        onChange={(mcp_servers) => setConfig({ ...config, mcp_servers })}
        fields={mcpFields}
        subtitle={mcpSummary}
        emptyText="添加 stdio 本地进程或 Streamable HTTP 远程 MCP 服务，保存后其工具会出现在工具与权限中。"
        renderActions={(item) => (
          <button
            type="button"
            onClick={() =>
              void act(async () => {
                const result = await client.reconnectMcp(item.id);
                queryClient.setQueryData(["mcp"], result);
              }, "已重新连接，请查看连接状态")
            }
          >
            <RefreshCw size={14} />
            重新连接
          </button>
        )}
      />
      <section className="panel">
        <h2>连接状态</h2>
        <p className="muted">
          MCP 工具以 mcp_服务名_工具名
          注册，受各会话的工具权限与审批控制。状态反映已保存的配置。
        </p>
        {status.error && (
          <QueryErrorNotice
            message={`读取 MCP 状态失败：${status.error.message}`}
          />
        )}
        {rows.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <strong>
                {row.id}
                {row.server.name
                  ? ` · ${row.server.name}${row.server.version ? " " + row.server.version : ""}`
                  : ""}
              </strong>
              <span className="tag">{statuses[row.status]}</span>
            </div>
            {row.error && <p role="alert">{row.error}</p>}
            {row.instructions && <p className="muted">{row.instructions}</p>}
            {row.tools.length > 0 && (
              <ul className="plain-list">
                {row.tools.map((tool) => (
                  <li key={tool.name}>
                    <code>{tool.name}</code>
                    <span className="tag">{tool.risk}</span>
                    {!tool.enabled && <span className="tag">已隐藏</span>}
                    {tool.read_only_hint && (
                      <span className="tag">服务声明只读</span>
                    )}
                    {tool.error && <span role="alert"> {tool.error}</span>}
                    {tool.description && (
                      <small className="muted"> {tool.description}</small>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </article>
        ))}
        {!rows.length && !status.error && (
          <EmptyState text="尚未配置 MCP 服务。" />
        )}
      </section>
    </>
  );
}
