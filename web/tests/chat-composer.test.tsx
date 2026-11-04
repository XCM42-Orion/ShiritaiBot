import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ApiClient } from "../src/api/client";
import type {
  CommandInfo,
  ModelConfig,
  Settings,
  ToolCatalog,
} from "../src/api/types";
import { ChatComposer } from "../src/components/ChatComposer";
import type { ChatComposerProps } from "../src/components/ChatComposer";
import { composerHeight } from "../src/lib/composer";
import { act, fakeChatClient, withQuery } from "./chat-helpers";
import { baseConfig } from "./fixtures";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const commands: CommandInfo[] = [
  { name: "model", description: "切换模型" },
  { name: "models", description: "列出模型" },
  { name: "status", description: "查看状态" },
];

function model(id: string): ModelConfig {
  return {
    id,
    enabled: true,
    provider: "main",
    model: id,
    max_output: 0,
    max_context: 0,
    modalities: ["text"],
    extra_body: {},
    kind: "text",
    dims: 0,
    currency: "CNY",
    price_input: 0,
    price_cached: 0,
    price_output: 0,
  };
}

function configWithModels(): Settings {
  const config = baseConfig();
  config.models = [model("m1"), model("m2")];
  return config;
}

const catalog: ToolCatalog = {
  session: "web:owner",
  policy: { mode: "agent", preset: "safe_manual", tools: {} },
  source: "default",
  tools: [],
};

function node(
  props: Partial<ChatComposerProps>,
  client: ReturnType<typeof fakeChatClient>,
) {
  return withQuery(
    <ChatComposer
      client={client}
      act={act}
      session="web:owner"
      assistantName="小知"
      {...props}
    />,
  );
}

function renderComposer(
  props: Partial<ChatComposerProps> = {},
  overrides: Partial<ApiClient> = {},
) {
  const api = fakeChatClient({
    commands: vi.fn().mockResolvedValue(commands),
    config: vi.fn().mockResolvedValue(configWithModels()),
    tools: vi.fn().mockResolvedValue(catalog),
    ...overrides,
  });
  const view = render(node(props, api));
  return { api, view };
}

describe("composer height", () => {
  it("clamps between the base box and the scrolling cap", () => {
    expect(composerHeight(10)).toBe(88);
    expect(composerHeight(180)).toBe(180);
    expect(composerHeight(9999)).toBe(240);
  });
});

describe("ChatComposer", () => {
  it("restores a draft for each session", async () => {
    const user = userEvent.setup();
    const first = renderComposer();
    const box = screen.getByLabelText("消息") as HTMLTextAreaElement;
    await user.type(box, "草稿内容");
    expect(localStorage.getItem("shiritaibot-draft:web:owner")).toBe(
      "草稿内容",
    );
    first.view.unmount();

    const other = renderComposer({ session: "web:other" });
    expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe(
      "",
    );
    other.view.unmount();

    renderComposer();
    expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe(
      "草稿内容",
    );
  });

  it("sends on Enter and keeps Shift+Enter as a newline", async () => {
    const { api } = renderComposer();
    const user = userEvent.setup();
    const box = screen.getByLabelText("消息") as HTMLTextAreaElement;
    await user.type(box, "第一行{Shift>}{Enter}{/Shift}第二行");
    expect(api.sendMessage).not.toHaveBeenCalled();
    expect(box.value).toContain("\n");
    await user.type(box, "{Enter}");
    expect(api.sendMessage).toHaveBeenCalledWith("第一行\n第二行", "web:owner");
    expect(box.value).toBe("");
    expect(localStorage.getItem("shiritaibot-draft:web:owner")).toBeNull();
  });

  it("does not send while the input method is composing", () => {
    const { api } = renderComposer();
    const box = screen.getByLabelText("消息");
    fireEvent.change(box, { target: { value: "zhong" } });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(api.sendMessage).not.toHaveBeenCalled();
  });

  it("navigates the slash menu with the keyboard", async () => {
    const { api } = renderComposer();
    const user = userEvent.setup();
    const box = screen.getByLabelText("消息") as HTMLTextAreaElement;
    await user.type(box, "/");
    expect(
      await screen.findByRole("option", { name: /^\/model / }),
    ).toBeTruthy();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(box.value).toBe("/models ");
    expect(api.sendMessage).not.toHaveBeenCalled();
  });

  it("closes the slash menu with Escape before stopping", async () => {
    const { api } = renderComposer({ running: true });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("消息"), "/");
    await screen.findByRole("option", { name: /^\/model / });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("option", { name: /^\/model / })).toBeNull();
    expect(api.sendMessage).not.toHaveBeenCalledWith("/stop", "web:owner");
  });

  it("sends immediately while running in steer mode", async () => {
    const { api } = renderComposer({ running: true });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("消息"), "插话一条{Enter}");
    expect(api.sendMessage).toHaveBeenCalledWith("插话一条", "web:owner");
  });

  it("queues messages while running and flushes them when idle", async () => {
    localStorage.setItem("shiritaibot-send-mode", "queue");
    const { api, view } = renderComposer({ running: true });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("消息"), "排队一条{Enter}");
    expect(api.sendMessage).not.toHaveBeenCalled();
    expect(screen.getByLabelText("排队消息")).toBeTruthy();
    expect(screen.getByText("排队一条")).toBeTruthy();

    view.rerender(node({ running: false }, api));
    await waitFor(() =>
      expect(api.sendMessage).toHaveBeenCalledWith("排队一条", "web:owner"),
    );
    await waitFor(() => expect(screen.queryByLabelText("排队消息")).toBeNull());
  });

  it("stops the running turn with Escape", async () => {
    const { api } = renderComposer({ running: true });
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("消息"));
    await user.keyboard("{Escape}");
    expect(api.sendMessage).toHaveBeenCalledWith("/stop", "web:owner");
  });

  it("recalls the last sent message with ArrowUp on an empty draft", async () => {
    const { api } = renderComposer();
    const user = userEvent.setup();
    const box = screen.getByLabelText("消息") as HTMLTextAreaElement;
    await user.type(box, "上一条{Enter}");
    expect(api.sendMessage).toHaveBeenCalledWith("上一条", "web:owner");
    await user.keyboard("{ArrowUp}");
    expect(box.value).toBe("上一条");
  });

  it("switches the session model from the composer", async () => {
    const { api } = renderComposer();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "模型" }));
    await user.click(await screen.findByRole("option", { name: /m1/ }));
    expect(api.sendMessage).toHaveBeenCalledWith("/model m1", "web:owner");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "模型" }).textContent,
      ).toContain("m1"),
    );
  });

  it("switches the approval preset from the composer", async () => {
    const { api } = renderComposer();
    const user = userEvent.setup();
    const chip = await screen.findByRole("button", { name: "审查方式" });
    await waitFor(() => expect(chip.textContent).toContain("安全外人工"));
    await user.click(chip);
    await user.click(await screen.findByRole("option", { name: /完全放行/ }));
    expect(api.sendMessage).toHaveBeenCalledWith(
      "/tools preset full_access",
      "web:owner",
    );
  });

  it("remembers the send mode choice", async () => {
    renderComposer();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "发送性质" }));
    await user.click(await screen.findByRole("option", { name: /排队/ }));
    expect(localStorage.getItem("shiritaibot-send-mode")).toBe("queue");
    expect(
      screen.getByRole("button", { name: "发送性质" }).textContent,
    ).toContain("排队");
  });
});
