import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ApiClient } from "../src/api/client";
import { App } from "../src/App";
import { baseConfig } from "./fixtures";
import type {
  ApprovalRecord,
  HistoryMessage,
  SessionRecord,
  TurnRecord,
} from "../src/api/types";
import { ChatWorkspace } from "../src/components/ChatWorkspace";
import type { ChatWorkspaceProps } from "../src/components/ChatWorkspace";
import { act, fakeChatClient, withQuery } from "./chat-helpers";

afterEach(cleanup);

const sessions: SessionRecord[] = [
  { id: "web:owner", mode: "agent", actor: "owner" },
  { id: "private:qq:2064438401", mode: "chat", actor: "2064438401" },
];
const turns: TurnRecord[] = [
  {
    id: "t1",
    scope: "web:owner",
    status: "running",
    started: new Date().toISOString(),
  },
];
const approval: ApprovalRecord = {
  id: "a1",
  scope: "web:owner",
  tool: "shell",
  args: { command: "echo hi" },
  hash: "h",
  status: "pending",
  expires: Date.now() / 1000 + 600,
  turn: "t1",
  reviewer: "human",
};
const history: HistoryMessage[] = [
  { role: "user", content: "你好" },
  { role: "assistant", content: "在的" },
];

function renderWorkspace(
  props: Partial<ChatWorkspaceProps> = {},
  overrides: Partial<ApiClient> = {},
) {
  const api = fakeChatClient({
    records: vi.fn(async (kind: string) => {
      if (kind === "session") return sessions;
      if (kind === "turn") return turns;
      if (kind === "approval") return [approval];
      return [];
    }),
    history: vi.fn().mockResolvedValue(history),
    ...overrides,
  });
  render(
    withQuery(
      <ChatWorkspace
        client={api}
        act={act}
        assistantName="小知"
        session="web:owner"
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        draftSessions={[]}
        onOpenSettings={() => {}}
        {...props}
      />,
    ),
  );
  return api;
}

describe("ChatWorkspace", () => {
  it("loads sessions, history and shows the inline approval", async () => {
    const api = renderWorkspace();
    expect(await screen.findByText("在的")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "默认对话" })).toBeTruthy();
    expect(screen.getByText("QQ 私聊")).toBeTruthy();
    expect(screen.getByRole("status", { name: "助手正在回复" })).toBeTruthy();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "允许这一次" }));
    expect(api.resolveApproval).toHaveBeenCalledWith("a1", true);
  });

  it("steers a message into the running turn", async () => {
    const api = renderWorkspace();
    await screen.findByText("在的");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("消息"), "再来一条{Enter}");
    expect(api.sendMessage).toHaveBeenCalledWith("再来一条", "web:owner");
  });

  it("switches to a read-only QQ session", async () => {
    renderWorkspace({ session: "private:qq:2064438401" });
    expect(await screen.findByText(/这里只发送 WebUI 会话/)).toBeTruthy();
    expect(screen.queryByLabelText("消息")).toBeNull();
    expect(screen.getByRole("heading", { name: "QQ 2064438401" })).toBeTruthy();
  });

  it("renders an empty state for a session with no history", async () => {
    renderWorkspace(
      { session: "web:1762321920000", draftSessions: ["web:1762321920000"] },
      { history: vi.fn().mockResolvedValue([]) },
    );
    expect(await screen.findByText(/从一句话开始/)).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: /对话 \d{2}-\d{2}/ }),
    ).toBeTruthy();
  });
});

function stubApp(config: unknown) {
  const responses: Record<string, unknown> = {
    "/api/health": {
      status: "ok",
      version: "0.2.2",
      instance_id: "i1",
      restart_available: true,
      config_version: 1,
      adapters: {},
      plugins: [],
      tools: [],
      actors: 0,
      subagents: 0,
    },
    "/api/config": config,
    "/api/records/session": sessions,
    "/api/records/turn": turns,
    "/api/records/approval": [],
    "/api/records/reply": [],
    "/api/commands": [],
    "/api/history": [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const url = String(input);
      const key = Object.keys(responses)
        .sort((a, b) => b.length - a.length)
        .find((candidate) => url.startsWith(candidate));
      return {
        ok: true,
        status: 200,
        json: async () => (key ? responses[key] : []),
      } as unknown as Response;
    }),
  );
  class SilentSocket {
    onopen: (() => void) | null = null;
    onmessage: (() => void) | null = null;
    onclose: (() => void) | null = null;
    send() {}
    close() {}
  }
  vi.stubGlobal("WebSocket", SilentSocket);
}

describe("chat mode", () => {
  it("switches modes, persists the choice and returns to settings", async () => {
    localStorage.clear();
    sessionStorage.setItem("shiritaibot-token", "test-token");
    location.hash = "#/overview";
    stubApp(baseConfig());
    const first = render(withQuery(<App />));
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /对话模式/ }));
    expect(
      await screen.findByRole("heading", { name: /默认对话/ }),
    ).toBeTruthy();
    expect(localStorage.getItem("shiritaibot-mode")).toBe("chat");
    first.unmount();

    render(withQuery(<App />));
    expect(
      await screen.findByRole("heading", { name: /默认对话/ }),
    ).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(location.hash).toBe("#/settings");
    expect(localStorage.getItem("shiritaibot-mode")).toBe("console");
    expect(await screen.findByRole("heading", { name: "设置" })).toBeTruthy();
    localStorage.removeItem("shiritaibot-mode");
    vi.unstubAllGlobals();
  });
});
