import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act as reactAct,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ApiClient } from "../src/api/client";
import type { PluginInfo, Settings } from "../src/api/types";
import { Dashboard } from "../src/pages/Dashboard";
import { ModelsPage } from "../src/pages/ModelsPage";
import { ChatPage } from "../src/pages/ChatPage";
import { PluginsPage } from "../src/pages/PluginsPage";
import { SettingsPage } from "../src/pages/SettingsPage";
import { SecretField } from "../src/components/SecretField";
import { NoticeProvider, useNotice } from "../src/hooks/useNotice";
import { FieldForm } from "../src/components/FieldForm";
import { PAGES, pageFromHash } from "../src/navigation";
import { App } from "../src/App";

afterEach(cleanup);

function baseConfig(): Settings {
  return {
    providers: [
      {
        id: "main",
        enabled: true,
        protocol: "chat_completions",
        base_url: "https://api.example/v1",
        api_key: "secret:saved",
        timeout: 120,
        max_retries: 2,
        proxy: "",
      },
    ],
    models: [],
    routes: { agent: [], chat: [], light: [] },
    adapters: [],
    subagents: [],
    plugins: [],
    policy: {
      denied_tools: [],
      scope_tools: {},
      approval_timeout: 600,
      shell_enabled: false,
      shell_command: ["bash", "--noprofile", "--norc", "-c"],
      max_steps: 25,
      max_turn_seconds: 1200,
      max_context_chars: 100000,
    },
    proactive: {
      enabled: false,
      timezone: "Asia/Shanghai",
      quiet_start: "23:30",
      quiet_end: "08:00",
      throttle_seconds: 600,
    },
    tavily_key: "env:TAVILY_API_KEY",
    concurrency: 4,
    persona: "test",
    personas: [],
    default_persona: "",
    assistant_name: "ShiritaiBot",
    owner_name: "主人",
    wake_keywords: [],
    private_require_keyword: false,
    context: {
      strategy: "discard",
      max_turns: 50,
      compression_model: "",
      keep_recent_turns: 10,
    },
  };
}

function fakeClient(overrides: Partial<ApiClient> = {}): ApiClient {
  const stub = () => vi.fn().mockResolvedValue(undefined);
  return {
    health: vi.fn().mockResolvedValue({
      status: "ok",
      version: "0.1.0",
      config_version: 1,
      adapters: {},
      plugins: [],
      tools: [],
      actors: 0,
      subagents: 0,
    }),
    config: vi.fn().mockResolvedValue(baseConfig()),
    saveConfig: vi.fn().mockResolvedValue({ ok: true, version: 2 }),
    records: vi.fn().mockResolvedValue([]),
    stats: vi.fn().mockResolvedValue({
      hours: 24,
      timezone: "Asia/Shanghai",
      start: "2026-10-26T04:00:00Z",
      end: "2026-10-27T04:00:00Z",
      totals: {
        input_tokens: 0,
        output_tokens: 0,
        cached_tokens: 0,
        total_tokens: 0,
        llm_calls: 0,
        incoming_messages: 0,
        outgoing_messages: 0,
        messages: 0,
        failed_turns: 0,
      },
      buckets: [],
    }),
    commands: vi
      .fn()
      .mockResolvedValue([{ name: "help", description: "命令帮助" }]),
    history: vi.fn().mockResolvedValue([]),
    sendMessage: vi.fn().mockResolvedValue({ status: "queued" }),
    resolveApproval: vi.fn().mockResolvedValue({ ok: true }),
    memories: vi.fn().mockResolvedValue([]),
    addMemory: vi.fn().mockResolvedValue({}),
    archiveMemory: vi.fn().mockResolvedValue({ ok: true }),
    createRoutine: stub(),
    setRoutineStatus: stub(),
    cancelSubagent: stub(),
    detectSubagent: stub(),
    plugins: vi.fn().mockResolvedValue([]),
    reloadPlugins: vi.fn().mockResolvedValue([]),
    reloadPlugin: stub(),
    installPluginZip: vi.fn().mockResolvedValue([]),
    installPluginGithub: vi.fn().mockResolvedValue([]),
    removePlugin: vi.fn().mockResolvedValue([]),
    discoverModels: vi.fn().mockResolvedValue([]),
    testModel: vi.fn().mockResolvedValue({ text: "OK", usage: {} }),
    sessions: vi.fn().mockResolvedValue([]),
    ...overrides,
  } as unknown as ApiClient;
}

const act = (work: () => Promise<unknown>) => work().then(() => undefined);

describe("email plugin configuration", () => {
  it("edits existing mail settings without losing credentials and can fill a new template", async () => {
    const client = fakeClient({
      plugins: vi.fn().mockResolvedValue([
        {
          id: "email",
          name: "Email",
          description: "",
          enabled: false,
          status: "disabled",
          error: "",
          defaults: {
            imap_host: "",
            imap_port: 993,
            imap_security: "ssl",
            mailbox: "INBOX",
            poll: true,
            poll_seconds: 300,
            smtp_port: 465,
            notify_to: [],
          },
          settings_schema: {
            type: "object",
            properties: {
              imap_host: { type: "string", title: "IMAP 服务器" },
              imap_port: { type: "integer", title: "IMAP 端口" },
              password: { type: "string", title: "邮箱密码", writeOnly: true },
              notify_to: {
                type: "array",
                title: "通知收件人",
                items: { type: "string" },
              },
            },
          },
        },
      ]),
    });
    let latest = baseConfig();
    latest.plugins = [
      {
        id: "email",
        enabled: false,
        settings: {
          imap_host: "imap.example.com",
          password: "secret:mail-password",
          smtp_security: "starttls",
          notify_to: ["owner@example.com"],
        },
      },
    ];
    function EmailHost() {
      const [config, setConfig] = useState(latest);
      return (
        <PluginsPage
          client={client}
          act={act}
          config={config}
          setConfig={(next) => {
            latest = next;
            setConfig(next);
          }}
        />
      );
    }
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <EmailHost />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await screen.findByLabelText("IMAP 服务器");
    expect((screen.getByLabelText("密钥") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("IMAP 端口") as HTMLInputElement).value).toBe(
      "993",
    );
    await user.clear(screen.getByLabelText("IMAP 服务器"));
    await user.type(
      screen.getByLabelText("IMAP 服务器"),
      "imap.updated.example",
    );
    expect(latest.plugins[0].settings).toMatchObject({
      imap_host: "imap.updated.example",
      password: "secret:mail-password",
      smtp_security: "starttls",
      notify_to: ["owner@example.com"],
    });
    await user.click(screen.getByRole("button", { name: "填入配置模板" }));
    expect(latest.plugins[0].settings).toMatchObject({
      imap_port: 993,
      imap_security: "ssl",
      mailbox: "INBOX",
      poll: true,
      poll_seconds: 300,
      smtp_port: 465,
      notify_to: [],
    });
    await user.type(
      screen.getByLabelText("通知收件人"),
      "first@example.com\nsecond@example.com",
    );
    expect(latest.plugins[0].settings.notify_to).toEqual([
      "first@example.com",
      "second@example.com",
    ]);
  });
});

function Host({
  client,
  children,
}: {
  client: ApiClient;
  children: (
    config: Settings,
    setConfig: (next: Settings) => void,
  ) => React.ReactNode;
}) {
  const [config, setConfig] = useState(baseConfig());
  return <>{children(config, setConfig)}</>;
}

function withQuery(node: React.ReactNode) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {node}
    </QueryClientProvider>
  );
}

describe("models workspace", () => {
  it("tests an unsaved model using the current provider form without saving", async () => {
    const client = fakeClient();
    render(
      <Host client={client}>
        {(config, setConfig) => (
          <ModelsPage
            client={client}
            act={act}
            config={config}
            setConfig={setConfig}
          />
        )}
      </Host>,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("密钥"), "draft-key");
    await user.click(screen.getByText("手动添加"));
    await user.clear(screen.getByLabelText("模型标识"));
    await user.type(screen.getByLabelText("模型标识"), "GLM-5.3-Flash");
    await user.type(screen.getByLabelText("提供商模型名称"), "GLM-5.3-Flash");
    fireEvent.change(screen.getByLabelText("最大重试轮数"), {
      target: { value: "4" },
    });
    await user.type(
      screen.getByLabelText("网络请求代理"),
      "http://127.0.0.1:7890",
    );
    fireEvent.change(screen.getByLabelText("最大上下文 tokens"), {
      target: { value: "64000" },
    });
    await user.click(screen.getByLabelText("图像"));
    fireEvent.change(screen.getByLabelText("自定义请求体参数（JSON）"), {
      target: { value: '{"temperature":0.7,"stop":null}' },
    });
    await user.click(screen.getByText("测试当前配置"));
    expect(client.testModel).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        api_key: "draft-key",
        max_retries: 4,
        proxy: "http://127.0.0.1:7890",
      }),
      expect.objectContaining({
        id: "GLM-5.3-Flash",
        model: "GLM-5.3-Flash",
        max_context: 64000,
        modalities: ["text", "image"],
        extra_body: '{"temperature":0.7,"stop":null}',
      }),
    );
    expect(client.saveConfig).not.toHaveBeenCalled();
  });

  it.each([false, true])(
    "restores the test button after completion (failure=%s)",
    async (failure) => {
      let resolve!: (value: unknown) => void;
      let reject!: (reason: Error) => void;
      const pending = new Promise((yes, no) => {
        resolve = yes;
        reject = no;
      });
      const testModel = vi.fn().mockReturnValue(pending);
      const client = fakeClient({ testModel });
      const errors: unknown[] = [];
      const action = async (work: () => Promise<unknown>) => {
        try {
          await work();
        } catch (error) {
          errors.push(error);
        }
      };
      render(
        <Host client={client}>
          {(config, setConfig) => (
            <ModelsPage
              client={client}
              config={config}
              setConfig={setConfig}
              act={action}
            />
          )}
        </Host>,
      );
      const user = userEvent.setup();
      await user.click(screen.getByText("手动添加"));
      await user.click(screen.getByText("手动添加"));
      const [button, other] = screen.getAllByRole("button", {
        name: "测试当前配置",
      }) as HTMLButtonElement[];
      await user.click(button);
      expect(button.disabled).toBe(true);
      expect(button.getAttribute("aria-busy")).toBe("true");
      expect(button.querySelector(".spinner")).toBeTruthy();
      expect(other.disabled).toBe(false);
      await user.click(button);
      expect(testModel).toHaveBeenCalledTimes(1);
      await reactAct(async () => {
        if (failure) reject(new Error("test failed"));
        else resolve({ text: "OK", usage: {} });
      });
      expect(button.disabled).toBe(false);
      expect(button.textContent).toBe("测试当前配置");
      expect(button.querySelector(".spinner")).toBeNull();
      expect(errors.length).toBe(failure ? 1 : 0);
    },
  );

  it("keeps invalid JSON visible and blocks testing until corrected", async () => {
    const client = fakeClient();
    render(
      <Host client={client}>
        {(config, setConfig) => (
          <ModelsPage
            client={client}
            config={config}
            setConfig={setConfig}
            act={act}
          />
        )}
      </Host>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByText("手动添加"));
    const editor = screen.getByLabelText(
      "自定义请求体参数（JSON）",
    ) as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: '{"temperature":' } });
    expect(editor.value).toBe('{"temperature":');
    expect(editor.getAttribute("aria-invalid")).toBe("true");
    const button = screen.getByRole("button", {
      name: "测试当前配置",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.change(editor, { target: { value: '{"temperature":0}' } });
    expect(button.disabled).toBe(false);
    await user.click(button);
    expect(client.testModel).toHaveBeenCalledExactlyOnceWith(
      expect.anything(),
      expect.objectContaining({ extra_body: '{"temperature":0}' }),
    );
  });

  it("adds a discovered model with the plus button and keeps routes in sync", async () => {
    const client = fakeClient({
      discoverModels: vi
        .fn()
        .mockResolvedValue([
          { id: "GLM-5.3-Flash", name: "GLM", max_output: 4096 },
        ]),
    } as Partial<ApiClient>);
    let snapshot: Settings | null = null;
    render(
      <Host client={client}>
        {(config, setConfig) => {
          snapshot = config;
          return (
            <ModelsPage
              client={client}
              act={act}
              config={config}
              setConfig={(next) => {
                snapshot = next;
                setConfig(next);
              }}
            />
          );
        }}
      </Host>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByText("获取提供商模型"));
    await user.click(await screen.findByLabelText("添加模型 GLM-5.3-Flash"));
    expect(snapshot!.models[0].id).toBe("GLM-5.3-Flash");
    expect(snapshot!.routes.agent).toEqual(["GLM-5.3-Flash"]);
    expect(
      screen.getByLabelText("添加模型 GLM-5.3-Flash").hasAttribute("disabled"),
    ).toBe(true);

    await user.clear(screen.getByLabelText("提供商名称"));
    await user.type(screen.getByLabelText("提供商名称"), "renamed");
    expect(snapshot!.models[0].provider).toBe("renamed");

    await user.click(screen.getByLabelText("移除提供商"));
    expect(snapshot!.models).toEqual([]);
    expect(snapshot!.routes.agent).toEqual([]);
  });
});

describe("secret field", () => {
  it("keeps stored credentials hidden and supports replacing and clearing them", async () => {
    let current = "";
    function SecretHost() {
      const [value, setValue] = useState("secret:hidden-name");
      current = value;
      return <SecretField value={value} onChange={setValue} />;
    }
    const { container } = render(<SecretHost />);
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
});

describe("field form", () => {
  it("renders typed controls and reports typed values", async () => {
    function FormHost() {
      const [value, setValue] = useState({
        count: 3,
        title: "hello",
        enabled: false,
      });
      return (
        <FieldForm
          value={value}
          fields={[
            { key: "count", label: "数量", kind: "number", min: 1 },
            { key: "title", label: "标题", kind: "text" },
            { key: "enabled", label: "启用", kind: "boolean" },
          ]}
          onChange={setValue}
        />
      );
    }
    render(<FormHost />);
    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("数量"));
    await user.type(screen.getByLabelText("数量"), "7");
    expect((screen.getByLabelText("数量") as HTMLInputElement).value).toBe("7");
    await user.click(screen.getByLabelText("启用"));
    expect((screen.getByLabelText("启用") as HTMLInputElement).checked).toBe(
      true,
    );
    await user.clear(screen.getByLabelText("数量"));
    expect((screen.getByLabelText("数量") as HTMLInputElement).value).toBe("");
  });
});

it("switches the dashboard between 24, 72 and 168 hours", async () => {
  const client = fakeClient({
    stats: vi.fn().mockResolvedValue({
      hours: 24,
      timezone: "Asia/Shanghai",
      start: "2026-10-26T04:00:00Z",
      end: "2026-10-27T04:00:00Z",
      totals: {
        input_tokens: 0,
        output_tokens: 0,
        cached_tokens: 0,
        total_tokens: 10,
        llm_calls: 1,
        incoming_messages: 1,
        outgoing_messages: 1,
        messages: 2,
        failed_turns: 0,
      },
      buckets: [
        {
          hour: "2026-10-27T04:00:00Z",
          input_tokens: 10,
          output_tokens: 2,
          cached_tokens: 0,
          total_tokens: 12,
          llm_calls: 1,
          incoming_messages: 1,
          outgoing_messages: 1,
          messages: 2,
          failed_turns: 0,
        },
      ],
    }),
  } as Partial<ApiClient>);
  render(withQuery(<Dashboard client={client} />));
  expect(await screen.findByText("Token 用量")).toBeTruthy();
  const user = userEvent.setup();
  await user.click(screen.getByText("3 天"));
  await waitFor(() => expect(client.stats).toHaveBeenCalledWith(72));
  await user.click(screen.getByText("7 天"));
  await waitFor(() => expect(client.stats).toHaveBeenCalledWith(168));
  expect(client.stats).toHaveBeenCalledWith(24);
});

it("sends a chat message from the composer", async () => {
  const client = fakeClient();
  render(withQuery(<ChatPage client={client} act={act} />));
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("消息"), "你好");
  await user.click(screen.getByLabelText("发送消息"));
  await waitFor(() =>
    expect(client.sendMessage).toHaveBeenCalledWith("你好", "web:owner"),
  );
});

function NoticeControls() {
  const { succeed, fail, warn } = useNotice();
  return (
    <>
      <button onClick={() => succeed("操作已完成")}>成功提示</button>
      <button onClick={() => fail("无法获取模型")}>错误提示</button>
      <button onClick={() => warn("请检查配置")}>警告提示</button>
    </>
  );
}

describe("notifications", () => {
  it.each([
    ["成功提示", "status", "success", "操作已完成"],
    ["错误提示", "alert", "error", "无法获取模型"],
    ["警告提示", "alert", "warning", "请检查配置"],
  ])(
    "shows %s outside the layout and allows dismissal",
    async (button, role, tone, text) => {
      const { container } = render(
        <NoticeProvider>
          <NoticeControls />
        </NoticeProvider>,
      );
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: button }));
      const notice = screen.getByRole(role);
      expect(notice.textContent).toContain(text);
      expect(notice.classList.contains(`toast--${tone}`)).toBe(true);
      expect(notice.parentElement).toBe(document.body);
      expect(container.contains(notice)).toBe(false);
      expect(screen.queryByRole("dialog")).toBeNull();
      await user.click(screen.getByRole("button", { name: "关闭通知" }));
      expect(screen.queryByRole(role)).toBeNull();
    },
  );

  it("expires success notices, restarts repeated notices, and keeps errors and warnings visible", () => {
    vi.useFakeTimers();
    try {
      render(
        <NoticeProvider>
          <NoticeControls />
        </NoticeProvider>,
      );
      fireEvent.click(screen.getByText("成功提示"));
      const first = screen.getByRole("status");
      reactAct(() => vi.advanceTimersByTime(4000));
      fireEvent.click(screen.getByText("成功提示"));
      expect(screen.getByRole("status")).not.toBe(first);
      reactAct(() => vi.advanceTimersByTime(4000));
      expect(screen.getByRole("status")).toBeTruthy();
      reactAct(() => vi.advanceTimersByTime(1000));
      expect(screen.queryByRole("status")).toBeNull();
      for (const button of ["错误提示", "警告提示"]) {
        fireEvent.click(screen.getByText(button));
        reactAct(() => vi.advanceTimersByTime(10000));
        expect(screen.getByRole("alert")).toBeTruthy();
      }
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports page query failures outside the page content", async () => {
    const client = fakeClient({
      stats: vi.fn().mockRejectedValue(new Error("连接中断")),
    });
    const { container } = render(
      withQuery(
        <NoticeProvider>
          <Dashboard client={client} />
        </NoticeProvider>,
      ),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("读取统计失败：连接中断");
    expect(container.textContent).not.toContain("读取统计失败");
  });

  it("shows login failures without leaving the login form", async () => {
    sessionStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: "管理令牌无效" }),
      }),
    );
    try {
      const { container } = render(withQuery(<App />));
      const user = userEvent.setup();
      await user.type(screen.getByLabelText("管理令牌"), "wrong-token");
      await user.click(screen.getByRole("button", { name: "进入控制台" }));
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toBe("管理令牌无效");
      expect(container.contains(alert)).toBe(false);
      expect(screen.getByLabelText("管理令牌")).toBeTruthy();
      expect(sessionStorage.getItem("shiritaibot-token")).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

it("falls back to the overview page for unknown hashes", () => {
  expect(pageFromHash("#/models")).toBe("models");
  expect(pageFromHash("#/models/extra")).toBe("models");
  expect(pageFromHash("#/nope")).toBe("overview");
  expect(pageFromHash("")).toBe("overview");
});
describe("app shell", () => {
  it("loads the console, navigates between pages and keeps the token", async () => {
    const config = baseConfig();
    const responses: Record<string, unknown> = {
      "/api/health": {
        status: "ok",
        version: "0.1.0",
        config_version: 1,
        adapters: {},
        plugins: [],
        tools: ["web_search"],
        actors: 0,
        subagents: 0,
      },
      "/api/config": config,
      "/api/records/approval": [],
      "/api/records/activity": [],
      "/api/records/session": [],
      "/api/records/reply": [],
      "/api/commands": [],
      "/api/history": [],
      "/api/stats": {
        hours: 24,
        timezone: "Asia/Shanghai",
        start: "2026-10-26T04:00:00Z",
        end: "2026-10-27T04:00:00Z",
        totals: {
          input_tokens: 0,
          output_tokens: 0,
          cached_tokens: 0,
          total_tokens: 0,
          llm_calls: 0,
          incoming_messages: 0,
          outgoing_messages: 0,
          messages: 0,
          failed_turns: 0,
        },
        buckets: [],
      },
      "/api/memory": [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const url = String(input);
        const key = Object.keys(responses)
          .sort((a, b) => b.length - a.length)
          .find((candidate) => url.startsWith(candidate));
        const body = key ? responses[key] : [];
        return {
          ok: true,
          status: 200,
          json: async () => body,
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
    sessionStorage.setItem("shiritaibot-token", "test-token");
    location.hash = "#/overview";

    render(withQuery(<App />));
    expect(await screen.findByText("数据看板")).toBeTruthy();
    expect(screen.getByText("本机控制台")).toBeTruthy();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "模型与服务" }));
    expect(await screen.findByText("模型路由")).toBeTruthy();
    expect(location.hash).toBe("#/models");

    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(await screen.findByLabelText("基础人设")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "退出" }));
    expect(await screen.findByLabelText("管理令牌")).toBeTruthy();
    expect(sessionStorage.getItem("shiritaibot-token")).toBeNull();
    vi.unstubAllGlobals();
  });
});
describe("page rendering", () => {
  it("renders every page without crashing or leaking undefined values", async () => {
    const config = baseConfig();
    const emptyStats = {
      hours: 24,
      timezone: "Asia/Shanghai",
      start: "2026-10-26T04:00:00Z",
      end: "2026-10-27T04:00:00Z",
      totals: {
        input_tokens: 0,
        output_tokens: 0,
        cached_tokens: 0,
        total_tokens: 0,
        llm_calls: 0,
        incoming_messages: 0,
        outgoing_messages: 0,
        messages: 0,
        failed_turns: 0,
      },
      buckets: [],
    };
    const responses: Record<string, unknown> = {
      "/api/config": config,
      "/api/stats": emptyStats,
      "/api/health": {
        status: "ok",
        version: "0.1.0",
        config_version: 1,
        adapters: {},
        plugins: [],
        tools: ["web_search"],
        actors: 0,
        subagents: 0,
      },
      "/api/commands": [],
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
    sessionStorage.setItem("shiritaibot-token", "test-token");

    for (const page of PAGES) {
      location.hash = "#/" + page.id;
      const { unmount } = render(withQuery(<App />));
      const heading = await screen.findByRole("heading", {
        level: 1,
        name: page.label,
      });
      expect(heading).toBeTruthy();
      expect(document.body.textContent).not.toContain("undefined");
      expect(document.body.textContent).not.toContain("NaN");
      unmount();
    }
    vi.unstubAllGlobals();
  });
});
describe("configuration editing", () => {
  it("keeps edits in a draft and saves them from the sticky bar", async () => {
    const config = baseConfig();
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/config" && method === "PUT") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            version: 2,
            echo: body.policy.shell_enabled,
          }),
        } as unknown as Response;
      }
      if (url === "/api/config") {
        return {
          ok: true,
          status: 200,
          json: async () => config,
        } as unknown as Response;
      }
      if (url === "/api/health") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "ok",
            version: "0.1.0",
            config_version: 1,
            adapters: {},
            plugins: [],
            tools: [],
            actors: 0,
            subagents: 0,
          }),
        } as unknown as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => [],
      } as unknown as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    class SilentSocket {
      onopen: (() => void) | null = null;
      onmessage: (() => void) | null = null;
      onclose: (() => void) | null = null;
      send() {}
      close() {}
    }
    vi.stubGlobal("WebSocket", SilentSocket);
    sessionStorage.setItem("shiritaibot-token", "test-token");
    location.hash = "#/approvals";

    render(withQuery(<App />));
    const toggle = await screen.findByLabelText("启用 Shell 工具");
    const user = userEvent.setup();
    await user.click(toggle);
    expect(screen.getByText("配置有未保存的修改")).toBeTruthy();
    await user.click(screen.getByText("保存并应用"));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url) === "/api/config" &&
            (init as RequestInit | undefined)?.method === "PUT",
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(screen.queryByText("配置有未保存的修改")).toBeNull(),
    );
    vi.unstubAllGlobals();
  });
});

describe("identity, personas and context settings", () => {
  it("edits settings and maintains the default persona through rename, disable and removal", async () => {
    let latest = baseConfig();
    latest.models = [
      {
        id: "summary",
        provider: "main",
        enabled: true,
        model: "summarizer",
        max_output: 4096,
      },
    ];
    function Host() {
      const [config, setConfig] = useState(latest);
      return (
        <SettingsPage
          client={fakeClient()}
          act={act}
          config={config}
          setConfig={(next) => {
            latest = next;
            setConfig(next);
          }}
        />
      );
    }
    render(<Host />);
    const user = userEvent.setup();
    expect(
      (screen.getByLabelText("主人私聊 Agent 需要关键词") as HTMLInputElement)
        .checked,
    ).toBe(false);
    await user.clear(screen.getByLabelText("助手称呼"));
    await user.type(screen.getByLabelText("助手称呼"), "小知");
    await user.clear(screen.getByLabelText("对主人的称呼"));
    await user.type(screen.getByLabelText("对主人的称呼"), "老师");
    fireEvent.change(screen.getByLabelText("唤起关键词"), {
      target: { value: "小知\n助手" },
    });
    await user.click(screen.getByLabelText("主人私聊 Agent 需要关键词"));
    await user.click(screen.getByRole("button", { name: "添加" }));
    await user.clear(screen.getByLabelText("人格标识"));
    await user.type(screen.getByLabelText("人格标识"), "kind");
    await user.clear(screen.getByLabelText("人格名称"));
    await user.type(screen.getByLabelText("人格名称"), "温柔");
    await user.clear(screen.getByLabelText("人格提示词"));
    await user.type(screen.getByLabelText("人格提示词"), "秘密提示词");
    await user.selectOptions(screen.getByLabelText("默认人格"), "kind");
    await user.selectOptions(
      screen.getByLabelText("上下文管理策略"),
      "compress",
    );
    await user.selectOptions(
      screen.getByLabelText("上下文压缩模型"),
      "summary",
    );
    fireEvent.change(screen.getByLabelText("最大保留上下文轮数"), {
      target: { value: "8" },
    });
    fireEvent.change(screen.getByLabelText("压缩时保留最近轮数"), {
      target: { value: "2" },
    });
    expect(latest).toMatchObject({
      assistant_name: "小知",
      owner_name: "老师",
      wake_keywords: ["小知", "助手"],
      private_require_keyword: true,
      default_persona: "kind",
      personas: [
        { id: "kind", name: "温柔", prompt: "秘密提示词", enabled: true },
      ],
      context: {
        strategy: "compress",
        max_turns: 8,
        keep_recent_turns: 2,
        compression_model: "summary",
      },
    });
    await user.clear(screen.getByLabelText("人格标识"));
    await user.type(screen.getByLabelText("人格标识"), "gentle");
    expect(latest.default_persona).toBe("gentle");
    await user.click(screen.getByLabelText("启用人格"));
    expect(latest.default_persona).toBe("");
    await user.click(screen.getByRole("button", { name: "移除" }));
    expect(latest.personas).toEqual([]);
    expect(latest.assistant_name).toBe("小知");
  });
});

describe("discovered plugin management", () => {
  const metadata: PluginInfo = {
    id: "new_plugin",
    name: "New plugin",
    description: "A scanned plugin",
    enabled: false,
    status: "disabled",
    error: "",
    defaults: { frequency: 30 },
    settings_schema: {
      type: "object",
      properties: {
        frequency: { type: "integer", title: "自定义检查间隔", minimum: 10 },
      },
    },
  };

  function renderPlugins(client: ApiClient, initial = baseConfig()) {
    let latest = initial;
    function PluginHost() {
      const [config, setConfig] = useState(initial);
      return (
        <PluginsPage
          client={client}
          act={act}
          config={config}
          setConfig={(next) => {
            latest = next;
            setConfig(next);
          }}
        />
      );
    }
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <PluginHost />
      </QueryClientProvider>,
    );
    return () => latest;
  }

  it("shows a scanned plugin absent from the config and renders its own schema", async () => {
    const client = fakeClient({
      plugins: vi.fn().mockResolvedValue([metadata]),
    });
    const latest = renderPlugins(client);
    const input = await screen.findByLabelText("自定义检查间隔");
    expect((input as HTMLInputElement).value).toBe("30");
    fireEvent.change(input, { target: { value: "45" } });
    expect(latest().plugins).toEqual([
      { id: "new_plugin", enabled: false, settings: { frequency: 45 } },
    ]);
    expect(screen.queryByRole("textbox", { name: "插件标识" })).toBeNull();
  });

  it("confirms removal before deleting and keeps other unsaved configuration", async () => {
    const remove = vi.fn().mockResolvedValue([]);
    const client = fakeClient({
      plugins: vi.fn().mockResolvedValue([metadata]),
      removePlugin: remove,
    });
    const initial = baseConfig();
    initial.owner_name = "未保存的名字";
    initial.plugins = [
      { id: metadata.id, enabled: false, settings: { frequency: 50 } },
    ];
    const latest = renderPlugins(client, initial);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "移除" }));
    expect(remove).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "确认移除插件" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "移除" }));
    await user.click(screen.getByRole("button", { name: "确认移除" }));
    await waitFor(() => expect(latest().plugins).toEqual([]));
    expect(remove).toHaveBeenCalledWith("new_plugin");
    expect(latest().owner_name).toBe("未保存的名字");
  });

  it("rescans plugins and installs ZIP or GitHub packages through the API", async () => {
    const scan = vi.fn().mockResolvedValue([]);
    const zip = vi.fn().mockResolvedValue([]);
    const github = vi.fn().mockResolvedValue([]);
    const client = fakeClient({
      reloadPlugins: scan,
      installPluginZip: zip,
      installPluginGithub: github,
    });
    renderPlugins(client);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "重载插件" }));
    expect(scan).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "安装插件" }));
    await user.type(screen.getByLabelText("安装插件标识"), "new_plugin");
    const file = new File(["zip bytes"], "plugin.zip", {
      type: "application/zip",
    });
    await user.upload(screen.getByLabelText("ZIP 插件包"), file);
    expect(
      (screen.getByLabelText("ZIP 插件包") as HTMLInputElement).files?.[0],
    ).toBe(file);
    await user.click(screen.getByRole("button", { name: "安装并扫描" }));
    expect(zip).toHaveBeenCalledWith("new_plugin", file);
    await user.click(screen.getByRole("button", { name: "安装插件" }));
    await user.selectOptions(screen.getByLabelText("安装来源"), "github");
    await user.type(
      screen.getByLabelText("GitHub 仓库地址"),
      "https://github.com/owner/plugin",
    );
    await user.click(screen.getByRole("button", { name: "安装并扫描" }));
    expect(github).toHaveBeenCalledWith(
      "new_plugin",
      "https://github.com/owner/plugin",
    );
  });
});
