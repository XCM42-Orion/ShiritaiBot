import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ApiClient } from "../src/api/client";
import type { HistoryMessage } from "../src/api/types";
import { AgentTimeline } from "../src/components/AgentTimeline";
import { ChatComposer } from "../src/components/ChatComposer";
import { ChatPage } from "../src/pages/ChatPage";

afterEach(cleanup);

const loop: HistoryMessage[] = [
  { role: "user", content: "查邮件\n<context local_time='x'>[]</context>" },
  {
    role: "assistant",
    content: "我先看看",
    tool_calls: [
      { id: "1", function: { name: "mail_search", arguments: '{"q":"x"}' } },
    ],
  },
  { role: "tool", tool_call_id: "1", content: '{"count":1}' },
  {
    role: "assistant",
    tool_calls: [
      { id: "2", function: { name: "read", arguments: "{}" } },
      { id: "3", function: { name: "read", arguments: '{"p":"bad"}' } },
    ],
  },
  { role: "tool", tool_call_id: "2", content: "ok" },
  { role: "tool", tool_call_id: "3", content: '{"error":"denied"}' },
  { role: "assistant", content: "**找到一封**" },
];

describe("AgentTimeline", () => {
  it("renders text outside one collapsed tool group", () => {
    const { container } = render(
      <AgentTimeline history={loop} assistantName="小知" />,
    );
    expect(screen.getByText("查邮件")).toBeTruthy();
    expect(screen.queryByText(/local_time/)).toBeNull();
    expect(screen.getByText("我先看看")).toBeTruthy();
    expect(screen.getByText("找到一封")).toBeTruthy();
    const group = container.querySelector(
      "details.tool-group",
    ) as HTMLDetailsElement;
    expect(group).toBeTruthy();
    expect(group.open).toBe(false);
    expect(group.textContent).toContain("3 次工具调用");
    expect(group.textContent).toContain("mail_search, read ×2");
    const failed = container.querySelectorAll(".tool-call.failed");
    expect(failed).toHaveLength(1);
  });

  it("marks the last assistant message when the turn failed", () => {
    const { container } = render(
      <AgentTimeline history={loop} assistantName="小知" failed />,
    );
    const marked = container.querySelector(".message.failed-turn");
    expect(marked?.textContent).toContain("找到一封");
    expect(marked?.textContent).toContain("本轮失败");
  });
});

describe("ChatPage", () => {
  it("renders history only, without a delivered block", async () => {
    const client = {
      records: vi.fn(async (kind: string) =>
        kind === "reply"
          ? [{ id: "r1", scope: "web:owner", text: "重复的回复", at: "" }]
          : [],
      ),
      history: vi.fn().mockResolvedValue(loop),
      commands: vi.fn().mockResolvedValue([]),
      sendMessage: vi.fn(),
    } as unknown as ApiClient;
    const act = (work: () => Promise<unknown>) => work().then(() => undefined);
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <ChatPage client={client} act={act} assistantName="小知" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("找到一封")).toBeTruthy();
    expect(screen.queryByText("已投递")).toBeNull();
    expect(client.records).not.toHaveBeenCalledWith("reply");
  });
});

describe("ChatComposer", () => {
  it("sends, clears and fills slash commands", async () => {
    const client = {
      commands: vi
        .fn()
        .mockResolvedValue([{ name: "help", description: "命令帮助" }]),
      sendMessage: vi.fn().mockResolvedValue({ status: "queued" }),
    } as unknown as ApiClient;
    const act = (work: () => Promise<unknown>) => work().then(() => undefined);
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <ChatComposer
          client={client}
          act={act}
          session="web:owner"
          assistantName="小知"
        />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("消息"), "/hel");
    await user.click(await screen.findByText("命令帮助"));
    expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe(
      "/help ",
    );
    await user.type(screen.getByLabelText("消息"), "x");
    await user.click(screen.getByLabelText("发送消息"));
    expect(client.sendMessage).toHaveBeenCalledWith("/help x", "web:owner");
    expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe("");
  });
});
