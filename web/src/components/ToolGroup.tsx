import { ChevronRight, Wrench } from "lucide-react";
import { toolFailure, toolSummary } from "../lib/timeline";
import type { ToolItem } from "../lib/timeline";

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

function ToolCallRow({ item }: { item: ToolItem }) {
  const { call, result } = item;
  const args = parse(call.function.arguments);
  const output = parse(result?.content);
  const failed = toolFailure(result);
  const status = result === undefined ? "执行中" : failed ? "失败" : "完成";
  const entries =
    typeof args === "object" && args !== null && !Array.isArray(args)
      ? Object.entries(args)
      : null;
  return (
    <details className={"tool-call" + (failed ? " failed" : "")}>
      <summary>
        <Wrench size={12} aria-hidden="true" />
        <code>{call.function.name}</code>
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

export function ToolGroup({ items }: { items: ToolItem[] }) {
  const { count, names } = toolSummary(items);
  return (
    <details className="tool-group">
      <summary>
        <Wrench size={14} aria-hidden="true" />
        <span>{count} 次工具调用</span>
        <span className="tool-names">{names}</span>
        <ChevronRight size={14} className="chevron" aria-hidden="true" />
      </summary>
      <div className="tool-group-body">
        {items.map((item) => (
          <ToolCallRow key={item.call.id} item={item} />
        ))}
      </div>
    </details>
  );
}
