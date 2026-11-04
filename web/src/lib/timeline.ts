import type { HistoryMessage, ToolCall } from "../api/types";

export interface ToolItem {
  call: ToolCall;
  result?: HistoryMessage;
}

export type TimelineEntry =
  | { kind: "user"; message: HistoryMessage }
  | { kind: "assistant"; message: HistoryMessage }
  | { kind: "tools"; items: ToolItem[] };

export function buildTimeline(history: HistoryMessage[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  for (const message of history) {
    if (message.role === "user") {
      entries.push({ kind: "user", message });
      continue;
    }
    if (message.role === "assistant") {
      if (message.content?.trim()) {
        entries.push({ kind: "assistant", message });
      }
      if (!message.tool_calls?.length) continue;
      const items = message.tool_calls.map((call) => ({ call }));
      const last = entries[entries.length - 1];
      if (last?.kind === "tools") {
        last.items.push(...items);
      } else {
        entries.push({ kind: "tools", items });
      }
      continue;
    }
    if (message.role === "tool") {
      const last = entries[entries.length - 1];
      if (last?.kind !== "tools" || !message.tool_call_id) continue;
      const item = last.items.find(
        (candidate) =>
          candidate.call.id === message.tool_call_id && !candidate.result,
      );
      if (item) item.result = message;
    }
  }
  return entries;
}

export function toolFailure(result: HistoryMessage | undefined): boolean {
  if (!result) return false;
  try {
    const parsed: unknown = JSON.parse(result.content ?? "");
    return (
      typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed) &&
      "error" in parsed
    );
  } catch {
    return false;
  }
}

export function toolSummary(items: ToolItem[]): {
  count: number;
  names: string;
} {
  const counts = new Map<string, number>();
  for (const item of items) {
    const name = item.call.function.name;
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  const names = [...counts]
    .map(([name, count]) => (count > 1 ? `${name} ×${count}` : name))
    .join(", ");
  return { count: items.length, names };
}
