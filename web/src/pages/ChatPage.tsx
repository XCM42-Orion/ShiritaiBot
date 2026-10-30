import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Send, Square } from "lucide-react";
import { AgentTimeline } from "../components/AgentTimeline";
import { EmptyState } from "../components/EmptyState";
import type { PageProps } from "./common";

export function ChatPage({
  client,
  act,
  assistantName = "ShiritaiBot",
}: PageProps & { assistantName?: string }) {
  const [session, setSession] = useState("web:owner");
  const [text, setText] = useState("");
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
  const commands = useQuery({
    queryKey: ["commands"],
    queryFn: () => client.commands(),
  });
  const replies = useQuery({
    queryKey: ["records", "reply"],
    queryFn: () => client.records("reply"),
    refetchInterval: 4000,
  });

  const options = Array.from(
    new Set([
      "web:owner",
      ...(sessions.data ?? []).map((row) => row.id),
      session,
    ]),
  );
  const delivered = (replies.data ?? [])
    .filter((row) => row.scope === session)
    .slice(0, 3)
    .reverse();
  const menuOpen = text.startsWith("/") && !text.includes(" ");
  const matches = (commands.data ?? []).filter((command) =>
    command.name.startsWith(text.slice(1).toLowerCase()),
  );

  function send(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    const message = text.trim();
    void act(async () => {
      await client.sendMessage(message, session);
      setText("");
    }, "");
  }

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
          />
        )}
        {delivered.map((row) => (
          <div className="message delivered" key={row.id}>
            <small>已投递</small>
            <p>{row.text}</p>
          </div>
        ))}
      </section>
      {session.startsWith("web:") ? (
        <form className="composer" onSubmit={send}>
          {menuOpen && matches.length > 0 && (
            <div className="command-menu" aria-label="斜杠命令">
              {matches.map((command) => (
                <button
                  type="button"
                  key={command.name}
                  onClick={() => setText("/" + command.name + " ")}
                >
                  <strong>/{command.name}</strong>
                  <span>{command.description}</span>
                </button>
              ))}
            </div>
          )}
          <textarea
            aria-label="消息"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={`告诉 ${assistantName} 你想做什么，或输入 / 查看命令…`}
            rows={2}
          />
          <button
            type="button"
            aria-label="停止当前任务"
            onClick={() =>
              void act(
                () => client.sendMessage("/stop", session),
                "已发送停止指令",
              )
            }
          >
            <Square size={16} />
          </button>
          <button
            type="submit"
            className="primary"
            disabled={!text.trim()}
            aria-label="发送消息"
          >
            <Send size={17} />
          </button>
        </form>
      ) : (
        <p className="muted">
          这里只发送 WebUI 会话。QQ 会话请直接在 QQ 里对话。
        </p>
      )}
    </>
  );
}
