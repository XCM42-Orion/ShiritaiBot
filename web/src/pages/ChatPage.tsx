import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { AgentTimeline } from "../components/AgentTimeline";
import { ChatComposer } from "../components/ChatComposer";
import { EmptyState } from "../components/EmptyState";
import { latestTurnStatus } from "../lib/sessions";
import type { PageProps } from "./common";

export function ChatPage({
  client,
  act,
  assistantName = "ShiritaiBot",
}: PageProps & { assistantName?: string }) {
  const [session, setSession] = useState("web:owner");
  const sessions = useQuery({
    queryKey: ["records", "session"],
    queryFn: () => client.records("session"),
    refetchInterval: 10000,
  });
  const history = useQuery({
    queryKey: ["history", session],
    queryFn: () => client.history(session),
    refetchInterval: 2500,
  });
  const turns = useQuery({
    queryKey: ["records", "turn"],
    queryFn: () => client.records("turn"),
    refetchInterval: 4000,
  });

  const options = Array.from(
    new Set([
      "web:owner",
      ...(sessions.data ?? []).map((row) => row.id),
      session,
    ]),
  );

  return (
    <>
      <div className="inline chat-toolbar">
        <select
          aria-label="会话"
          value={session}
          onChange={(event) => setSession(event.target.value)}
        >
          {options.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => setSession("web:" + Date.now())}>
          <Plus size={14} />
          新会话
        </button>
      </div>
      <section className="panel chat-panel" aria-label="会话记录">
        {history.error && (
          <QueryErrorNotice
            message={`读取会话失败：${history.error.message}`}
          />
        )}
        {!history.data?.length && (
          <EmptyState text="从一句话开始。请先在「模型与服务」里配置模型路由。" />
        )}
        {history.data && (
          <AgentTimeline
            history={history.data}
            assistantName={assistantName}
            userLabel={session.startsWith("background:") ? "任务" : "你"}
            failed={latestTurnStatus(turns.data ?? [], session) === "failed"}
          />
        )}
      </section>
      {session.startsWith("web:") ? (
        <ChatComposer
          client={client}
          act={act}
          session={session}
          assistantName={assistantName}
        />
      ) : (
        <p className="muted">
          这里只发送 WebUI 会话。QQ 会话请直接在 QQ 里对话。
        </p>
      )}
    </>
  );
}
