import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProviderWorkspace, SecretField } from "../src/ConfigWorkspace";
import { Dashboard } from "../src/Dashboard";
import { ErrorDialog } from "../src/ErrorDialog";

afterEach(cleanup);

function initial() {
  return {
    providers: [
      {
        id: "main",
        enabled: true,
        protocol: "chat_completions",
        base_url: "https://api.example/v1",
        api_key: "secret:saved",
        timeout: 120,
      },
    ],
    models: [],
    routes: { agent: [], chat: [], light: [] },
  };
}

describe("provider workspace", () => {
  it("tests an unsaved model using the current provider form without saving", async () => {
    const api = vi.fn().mockResolvedValue({ text: "OK" });
    function Host() {
      const [config, setConfig] = useState(initial());
      return (
        <ProviderWorkspace
          config={config}
          setConfig={setConfig}
          api={api}
          act={(f) => void f()}
        />
      );
    }
    render(<Host />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("密钥"), "draft-key");
    await user.click(screen.getByText("手动添加"));
    await user.clear(screen.getByLabelText("模型标识"));
    await user.type(screen.getByLabelText("模型标识"), "GLM-5.3-Flash");
    await user.type(screen.getByLabelText("提供商模型名称"), "GLM-5.3-Flash");
    await user.click(screen.getByText("测试当前配置"));
    expect(api).toHaveBeenCalledExactlyOnceWith("/models/test", "POST", {
      provider: expect.objectContaining({ api_key: "draft-key" }),
      model: expect.objectContaining({
        id: "GLM-5.3-Flash",
        model: "GLM-5.3-Flash",
      }),
    });
  });
  it("fetches models from the current draft and adds a dotted ID using the plus button", async () => {
    const api = vi
      .fn()
      .mockResolvedValue([{ id: "GLM-5.3-Flash", name: "GLM" }]);
    let current: any;
    function Host() {
      const [config, setConfig] = useState(initial());
      current = config;
      return (
        <ProviderWorkspace
          config={config}
          setConfig={setConfig}
          api={api}
          act={(f) => void f()}
        />
      );
    }
    render(<Host />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("密钥"), "new-key");
    await user.click(screen.getByText("获取提供商模型"));
    expect(api).toHaveBeenCalledWith(
      "/providers/discover",
      "POST",
      expect.objectContaining({ api_key: "new-key" }),
    );
    await user.click(await screen.findByLabelText("添加模型 GLM-5.3-Flash"));
    expect(current.models[0].id).toBe("GLM-5.3-Flash");
    expect(current.routes.agent).toEqual(["GLM-5.3-Flash"]);
    expect(
      screen.getByLabelText("添加模型 GLM-5.3-Flash").hasAttribute("disabled"),
    ).toBe(true);
    await user.clear(screen.getByLabelText("提供商名称"));
    await user.type(screen.getByLabelText("提供商名称"), "renamed");
    expect(current.models[0].provider).toBe("renamed");
    await user.click(screen.getByLabelText("移除提供商"));
    expect(current.models).toEqual([]);
    expect(current.routes.agent).toEqual([]);
  });

  it("keeps stored credentials hidden and supports replacing and clearing them", async () => {
    let current = "";
    function Host() {
      const [value, setValue] = useState("secret:hidden-name");
      current = value;
      return <SecretField value={value} onChange={setValue} />;
    }
    const { container } = render(<Host />);
    expect(container.innerHTML).not.toContain("hidden-name");
    expect((screen.getByLabelText("密钥") as HTMLInputElement).value).toBe("");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("密钥"), "replacement");
    expect(current).toBe("replacement");
    await user.click(screen.getByLabelText("显示密钥"));
    expect(screen.getByLabelText("密钥").getAttribute("type")).toBe("text");
    await user.click(screen.getByText("清除"));
    expect(current).toBe("");
  });

  it("hides stale discovery results after changing providers", async () => {
    const api = vi
      .fn()
      .mockResolvedValue([{ id: "first-only", name: "First" }]);
    function Host() {
      const [config, setConfig] = useState(initial());
      return (
        <ProviderWorkspace
          config={config}
          setConfig={setConfig}
          api={api}
          act={(f) => void f()}
        />
      );
    }
    render(<Host />);
    const user = userEvent.setup();
    await user.click(screen.getByText("获取提供商模型"));
    expect(await screen.findByLabelText("添加模型 first-only")).toBeTruthy();
    await user.click(screen.getByLabelText("新增提供商"));
    expect(screen.queryByLabelText("添加模型 first-only")).toBeNull();
  });
});

it("switches the dashboard between 24, 72 and 168 hours", async () => {
  const api = vi.fn().mockResolvedValue({
    totals: {
      messages: 2,
      total_tokens: 12,
      llm_calls: 1,
      failed_turns: 0,
      cached_tokens: 0,
    },
    start: "2026-10-26T04:00:00Z",
    end: "2026-10-27T04:00:00Z",
    buckets: [
      {
        hour: "2026-10-27T04:00:00Z",
        input_tokens: 10,
        output_tokens: 2,
        incoming_messages: 1,
        outgoing_messages: 1,
      },
    ],
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Dashboard api={api} onError={vi.fn()} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Token 用量")).toBeTruthy();
  const user = userEvent.setup();
  await user.click(screen.getByText("3 天"));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/stats?hours=72"));
  await user.click(screen.getByText("7 天"));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/stats?hours=168"));
  expect(api).toHaveBeenCalledWith("/stats?hours=24");
});

it("opens and dismisses errors as a modal dialog", async () => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  function Host() {
    const [message, setMessage] = useState<string | null>("无法获取模型");
    return <ErrorDialog message={message} onClose={() => setMessage(null)} />;
  }
  render(<Host />);
  expect(screen.getByRole("dialog").textContent).toContain("无法获取模型");
  await userEvent.setup().click(screen.getByText("知道了"));
  expect(screen.queryByRole("dialog")).toBeNull();
});
