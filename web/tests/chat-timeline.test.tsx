import { describe, expect, it } from "vitest";
import type { HistoryMessage, ToolCall } from "../src/api/types";
import { buildTimeline, toolFailure, toolSummary } from "../src/lib/timeline";

const call = (id: string, name = "shell"): ToolCall => ({
  id,
  type: "function",
  function: { name, arguments: "{}" },
});

function entriesOf(history: HistoryMessage[]) {
  return buildTimeline(history).map((entry) => entry.kind);
}

describe("buildTimeline", () => {
  it("keeps order and merges one contiguous tool run", () => {
    const entries = buildTimeline([
      { role: "user", content: "跑一下" },
      { role: "assistant", content: "我看看", tool_calls: [call("1")] },
      { role: "tool", tool_call_id: "1", content: "ok" },
      {
        role: "assistant",
        tool_calls: [call("2", "read"), call("3", "read")],
      },
      { role: "tool", tool_call_id: "2", content: "a" },
      { role: "tool", tool_call_id: "3", content: '{"error":"nope"}' },
      { role: "assistant", content: "完成" },
    ]);
    expect(entries.map((entry) => entry.kind)).toEqual([
      "user",
      "assistant",
      "tools",
      "assistant",
    ]);
    const tools = entries[2];
    if (tools.kind !== "tools") throw new Error("expected tools entry");
    expect(tools.items.map((item) => item.call.id)).toEqual(["1", "2", "3"]);
    expect(toolSummary(tools.items)).toEqual({
      count: 3,
      names: "shell, read ×2",
    });
    expect(toolFailure(tools.items[2].result)).toBe(true);
    expect(toolFailure(tools.items[1].result)).toBe(false);
  });

  it("starts a new group after a text-only assistant message", () => {
    expect(
      entriesOf([
        { role: "assistant", tool_calls: [call("1")] },
        { role: "tool", tool_call_id: "1", content: "ok" },
        { role: "assistant", content: "先说一句" },
        { role: "assistant", tool_calls: [call("2")] },
      ]),
    ).toEqual(["tools", "assistant", "tools"]);
  });

  it("ignores orphan tool results and leaves pending calls unfinished", () => {
    const entries = buildTimeline([
      { role: "tool", tool_call_id: "missing", content: "orphan" },
      { role: "assistant", tool_calls: [call("9")] },
    ]);
    expect(entries.map((entry) => entry.kind)).toEqual(["tools"]);
    const tools = entries[0];
    if (tools.kind !== "tools") throw new Error("expected tools entry");
    expect(tools.items[0].result).toBeUndefined();
    expect(toolFailure(undefined)).toBe(false);
  });

  it("attaches each result to its own call id regardless of arrival order", () => {
    const entries = buildTimeline([
      { role: "assistant", tool_calls: [call("a"), call("b", "read")] },
      { role: "tool", tool_call_id: "b", content: "B" },
      { role: "tool", tool_call_id: "a", content: "A" },
    ]);
    const tools = entries[0];
    if (tools.kind !== "tools") throw new Error("expected tools entry");
    expect(tools.items[0].result?.content).toBe("A");
    expect(tools.items[1].result?.content).toBe("B");
  });

  it("skips empty assistant messages", () => {
    expect(
      entriesOf([
        { role: "assistant", content: "   " },
        { role: "assistant", content: "有内容" },
      ]),
    ).toEqual(["assistant"]);
  });
});
