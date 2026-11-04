import type { HistoryMessage } from "../api/types";
import { buildTimeline } from "../lib/timeline";
import { Markdown } from "./Markdown";
import { ToolGroup } from "./ToolGroup";

export function AgentTimeline({
  history,
  assistantName,
  userLabel = "你",
  failed = false,
}: {
  history: HistoryMessage[];
  assistantName: string;
  userLabel?: string;
  failed?: boolean;
}) {
  const entries = buildTimeline(history);
  const lastAssistant = entries.reduce(
    (found, entry, index) => (entry.kind === "assistant" ? index : found),
    -1,
  );
  return (
    <>
      {entries.map((entry, index) => {
        if (entry.kind === "user") {
          return (
            <div className="message user" key={index}>
              <small>{userLabel}</small>
              <p>{entry.message.content?.split("\n<context")[0]}</p>
            </div>
          );
        }
        if (entry.kind === "tools") {
          return <ToolGroup key={index} items={entry.items} />;
        }
        const marked = failed && index === lastAssistant;
        return (
          <div
            className={"message assistant" + (marked ? " failed-turn" : "")}
            key={index}
          >
            <small>
              {marked && <span className="turn-failed">本轮失败</span>}
              {entry.message._speaker || assistantName}
            </small>
            <div className="assistant-body">
              <Markdown text={entry.message.content ?? ""} />
            </div>
          </div>
        );
      })}
    </>
  );
}
