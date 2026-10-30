import { ChevronRight, Wrench } from "lucide-react";
import type { HistoryMessage, ToolCall } from "../api/types";

type Block =
  | { kind: "user"; message: HistoryMessage }
  | { kind: "loop"; messages: HistoryMessage[] };

function parse(text: string | undefined): unknown {
  if (text === undefined) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function show(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

/** Group history into user turns and the Agent Loop steps that answer them. */
function blocks(history: HistoryMessage[]): Block[] {
  const result: Block[] = [];
  for (const message of history) {
    if (message.role === "user") {
      result.push({ kind: "user", message });
      continue;
    }
    const last = result[result.length - 1];
    if (last?.kind === "loop") last.messages.push(message);
    else result.push({ kind: "loop", messages: [message] });
  }
  return result;
}

function ToolCallView({
  call,
  result,
}: {
  call: ToolCall;
  result: HistoryMessage | undefined;
}) {
  const args = parse(call.function.arguments);
  const output = parse(result?.content);
  const failed =
    typeof output === "object" &&
    output !== null &&
    !Array.isArray(output) &&
    "error" in output;
  const status = result === undefined ? "执行中" : failed ? "失败" : "完成";
  const entries =
    typeof args === "object" && args !== null && !Array.isArray(args)
      ? Object.entries(args)
      : null;
  return (
    <details className={"tool-call" + (failed ? " failed" : "")}>
      <summary>
        <Wrench size={12} aria-hidden="true" />
        调用工具 <code>{call.function.name}</code>
        <span className="tool-status">{status}</span>
        <ChevronRight size={12} aria-hidden="true" />
      </summary>
      <div className="tool-call-body">
        <span className="tool-label">参数</span>
        {entries ? (
          entries.length ? (
            <dl className="tool-args">
              {entries.map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{show(value)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="muted">无参数</p>
          )
        ) : (
          <pre>{show(args)}</pre>
        )}
        {result !== undefined && (
          <details className="tool-result">
            <summary>
              返回结果
              <ChevronRight size={12} aria-hidden="true" />
            </summary>
            <pre>{show(output)}</pre>
          </details>
        )}
      </div>
    </details>
  );
}

export function AgentTimeline({
  history,
  assistantName,
  userLabel = "你",
}: {
  history: HistoryMessage[];
  assistantName: string;
  userLabel?: string;
}) {
  const results = new Map(
    history
      .filter((message) => message.role === "tool" && message.tool_call_id)
      .map((message) => [message.tool_call_id!, message]),
  );
  return (
    <>
      {blocks(history).map((block, index) =>
        block.kind === "user" ? (
          <div className="message user" key={index}>
            <small>{userLabel}</small>
            <p>{block.message.content?.split("\n<context")[0]}</p>
          </div>
        ) : (
          <div className="message assistant agent-loop" key={index}>
            <small>
              {block.messages.find((m) => m._speaker)?._speaker ||
                assistantName}
            </small>
            {block.messages
              .filter((message) => message.role === "assistant")
              .map((message, step) => (
                <div className="loop-step" key={step}>
                  {message.content?.trim() && <p>{message.content}</p>}
                  {message.tool_calls?.map((call) => (
                    <ToolCallView
                      key={call.id}
                      call={call}
                      result={results.get(call.id)}
                    />
                  ))}
                </div>
              ))}
          </div>
        ),
      )}
    </>
  );
}
