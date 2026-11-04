import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Menu, Settings } from "lucide-react";
import type { ApiClient } from "../api/client";
import { AgentTimeline } from "./AgentTimeline";
import { ApprovalCard } from "./ApprovalCard";
import { ChatComposer } from "./ChatComposer";
import { ChatSidebar } from "./ChatSidebar";
import { EmptyState } from "./EmptyState";
import { QueryErrorNotice } from "./QueryErrorNotice";
import type { Action } from "../pages/common";
import {
  latestTurnStatus,
  runningSessions,
  sessionActivity,
  sessionGroups,
  sessionTitle,
} from "../lib/sessions";

export interface ChatWorkspaceProps {
  client: ApiClient;
  act: Action;
  assistantName: string;
  session: string;
  onSelectSession: (id: string) => void;
  onCreateSession: () => void;
  draftSessions: string[];
  onOpenSettings: () => void;
}

export function ChatWorkspace({
  client,
  act,
  assistantName,
  session,
  onSelectSession,
  onCreateSession,
  draftSessions,
  onOpenSettings,
}: ChatWorkspaceProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  const sessions = useQuery({
    queryKey: ["records", "session"],
    queryFn: () => client.records("session"),
    refetchInterval: 10000,
  });
  const turns = useQuery({
    queryKey: ["records", "turn"],
    queryFn: () => client.records("turn"),
    refetchInterval: 2500,
  });
  const history = useQuery({
    queryKey: ["history", session],
    queryFn: () => client.history(session),
    refetchInterval: 2500,
  });
  const approvals = useQuery({
    queryKey: ["records", "approval"],
    queryFn: () => client.records("approval"),
    refetchInterval: 4000,
  });

  const ids = Array.from(
    new Set([
      "web:owner",
      ...draftSessions,
      ...(sessions.data ?? []).map((row) => row.id),
      session,
    ]),
  );
  const activity = sessionActivity(turns.data ?? []);
  const groups = sessionGroups(ids, activity);
  const running = runningSessions(turns.data ?? []);
  const pending = new Set(
    (approvals.data ?? [])
      .filter((row) => row.status === "pending" && row.reviewer !== "model")
      .map((row) => row.scope),
  );
  const current = (approvals.data ?? []).filter(
    (row) =>
      row.status === "pending" &&
      row.reviewer !== "model" &&
      row.scope === session,
  );
  const failed = latestTurnStatus(turns.data ?? [], session) === "failed";

  useEffect(() => {
    const element = scrollRef.current;
    if (!element || !stickRef.current) return;
    element.scrollTop = element.scrollHeight;
  }, [history.data, approvals.data, session]);

  function resolve(id: string, allow: boolean) {
    setBusy(true);
    void act(
      () => client.resolveApproval(id, allow),
      allow ? "已允许" : "已拒绝",
    ).finally(() => setBusy(false));
  }

  function select(id: string) {
    onSelectSession(id);
    setDrawerOpen(false);
  }

  return (
    <div className={"chat-workspace" + (drawerOpen ? " drawer-open" : "")}>
      <ChatSidebar
        groups={groups}
        activity={activity}
        selected={session}
        running={running}
        pending={pending}
        onSelect={select}
        onNew={onCreateSession}
      />
      <main className="chat-main">
        <header className="chat-header">
          <button
            type="button"
            aria-label="会话列表"
            className="icon-button chat-menu"
            onClick={() => setDrawerOpen((open) => !open)}
          >
            <Menu size={18} aria-hidden="true" />
          </button>
          <h1>{sessionTitle(session)}</h1>
          <button
            type="button"
            aria-label="设置"
            className="icon-button"
            onClick={onOpenSettings}
          >
            <Settings size={18} aria-hidden="true" />
          </button>
        </header>
        <div
          className="chat-scroll"
          ref={scrollRef}
          onScroll={(event) => {
            const element = event.currentTarget;
            stickRef.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              80;
          }}
        >
          {history.isPending && <p className="muted">正在读取会话…</p>}
          {history.error && (
            <QueryErrorNotice
              message={`读取会话失败：${history.error.message}`}
            />
          )}
          {history.data?.length === 0 && (
            <EmptyState text="从一句话开始。请先在「模型与服务」里配置模型路由。" />
          )}
          {history.data && (
            <AgentTimeline
              history={history.data}
              assistantName={assistantName}
              userLabel={session.startsWith("background:") ? "任务" : "你"}
              failed={failed}
            />
          )}
          {current.map((row) => (
            <ApprovalCard
              key={row.id}
              approval={row}
              busy={busy}
              onResolve={(allow) => resolve(row.id, allow)}
            />
          ))}
          {running.has(session) && (
            <div className="typing" role="status" aria-label="助手正在回复">
              <span />
              <span />
              <span />
            </div>
          )}
        </div>
        {session.startsWith("web:") ? (
          <ChatComposer
            client={client}
            act={act}
            session={session}
            assistantName={assistantName}
            running={running.has(session)}
          />
        ) : (
          <p className="muted chat-readonly">
            这里只发送 WebUI 会话。QQ 会话请直接在 QQ 里对话。
          </p>
        )}
      </main>
    </div>
  );
}
