import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act as reactAct,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ApiClient } from "../src/api/client";
import type { PluginInfo, Settings } from "../src/api/types";
import { Dashboard } from "../src/pages/Dashboard";
import { ModelsPage } from "../src/pages/ModelsPage";
import { ChatPage } from "../src/pages/ChatPage";
import { LogsPage } from "../src/pages/LogsPage";
import { PluginsPage } from "../src/pages/PluginsPage";
import { SettingsPage } from "../src/pages/SettingsPage";
import { MemoryPage } from "../src/pages/MemoryPage";
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
    agent_split: false,
    adapters: [],
    subagents: [],
    plugins: [],
    policy: {
      denied_tools: [],
      scope_tools: {},
      approval_timeout: 600,
      approval_allow_threshold: 0.8,
      approval_deny_threshold: 0.9,
      approval_on_deny: "fallback",
      approval_on_unsure: "fallback",
      owner_default: { mode: "agent", preset: "safe_manual", tools: {} },
      private_default: { mode: "chat", preset: "safe_manual", tools: {} },
      group_default: { mode: "chat", preset: "safe_manual", tools: {} },
      proactive_default: { preset: "safe_manual", tools: {} },
      sessions: {},
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
    web_search: {
      enabled: true,
      provider: "tavily",
      max_results: 5,
      timeout: 30,
      tavily: {
        api_key: "env:TAVILY_API_KEY",
        search_depth: "basic",
        topic: "general",
      },
      serpapi: { api_key: "env:SERPAPI_API_KEY", hl: "zh-cn", gl: "cn" },
      deepseek: {
        api_key: "env:DEEPSEEK_API_KEY",
        need_content: false,
        localization: "",
      },
    },
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
    group_context: {
      enabled: false,
      inject_messages: 20,
      inject_chars: 4000,
      query_tool: true,
    },
    memory: {
      inject: true,
      inject_chars: 1500,
      extract: true,
      extract_messages: 20,
      extract_idle_minutes: 10,
    },
    vision: { caption: true, max_images: 4 },
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
    tools: vi.fn().mockResolvedValue({
      session: "web:owner",
      policy: baseConfig().policy.owner_default,
      source: "default",
      tools: [],
    }),
    saveConfig: vi.fn().mockResolvedValue({ ok: true, version: 2 }),
    records: vi.fn().mockResolvedValue([]),
    recordsPage: vi
      .fn()
      .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 }),
    memoriesPage: vi
      .fn()
      .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 }),
    rawLogs: vi.fn().mockResolvedValue({ lines: [] }),
    logs: vi.fn().mockResolvedValue({ entries: [], sources: [] }),
    logLevel: vi.fn().mockResolvedValue({ level: "info" }),
    setLogLevel: vi.fn().mockResolvedValue({ level: "debug" }),
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
    memory: vi
      .fn()
      .mockResolvedValue({ id: "e1", type: "entity", status: "confirmed" }),
    memoryStatus: vi
      .fn()
      .mockResolvedValue({ model: null, indexed: 0, pending: 3 }),
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
  return withQuery(<>{children(config, setConfig)}</>);
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

describe("model categories", () => {
  function renderModels(client: ApiClient, onChange: (next: Settings) => void) {
    render(
      <Host client={client}>
        {(config, setConfig) => (
          <ModelsPage
            client={client}
            act={act}
            config={config}
            setConfig={(next) => {
              onChange(next);
              setConfig(next);
            }}
          />
        )}
      </Host>,
    );
  }

  it("keeps embedding models in their own tab and route", async () => {
    const client = fakeClient();
    let snapshot: Settings | null = null;
    renderModels(client, (next) => (snapshot = next));
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Embedding" }));
    await user.click(screen.getByText("手动添加"));
    expect(snapshot!.models[0]).toMatchObject({ kind: "embedding", dims: 0 });
    expect(snapshot!.routes.embedding).toEqual([snapshot!.models[0].id]);
    expect(snapshot!.routes.agent).toEqual([]);
    expect(screen.getByLabelText("向量维度")).toBeTruthy();
    expect(screen.queryByLabelText("最大输出 tokens")).toBeNull();

    await user.click(screen.getByRole("button", { name: "语言模型" }));
    expect(screen.queryByLabelText("向量维度")).toBeNull();
    await user.click(screen.getByText("手动添加"));
    expect(snapshot!.models[1].kind).toBe("text");

    await user.click(screen.getByRole("button", { name: "Embedding" }));
    await user.click(screen.getByText("模型路由"));
    expect(await screen.findByText(/未配置 Embedding：3 条记忆/)).toBeTruthy();
    expect(screen.getByText(/支持中文的 Embedding 模型/)).toBeTruthy();
    const options = Array.from(
      screen
        .getByLabelText("添加 embedding 路由模型")
        .querySelectorAll("option"),
    ).map((option) => option.textContent);
    expect(options).not.toContain(snapshot!.models[1].id);
  });

  it("offers only image models for the vision route and its caption settings", async () => {
    const client = fakeClient();
    let snapshot: Settings | null = null;
    renderModels(client, (next) => (snapshot = next));
    const user = userEvent.setup();
    await user.click(screen.getByText("手动添加"));
    await user.click(screen.getByText("手动添加"));
    const [, second] = screen.getAllByRole("group", {
      name: "模态支持（输入）",
    });
    await user.click(within(second).getByLabelText("图像"));
    expect(snapshot!.models[1].modalities).toEqual(["text", "image"]);

    await user.click(screen.getByText("模型路由"));
    expect(screen.getByText("图像转述模型")).toBeTruthy();
    const options = Array.from(
      screen.getByLabelText("添加 vision 路由模型").querySelectorAll("option"),
    ).map((option) => option.textContent);
    expect(options).toEqual(["＋ 添加模型", snapshot!.models[1].id]);
    await user.click(screen.getByLabelText("启用图像转述"));
    expect(snapshot!.vision.caption).toBe(false);
  });

  it("creates Jev providers in the decision tab and explains each route", async () => {
    const client = fakeClient();
    let snapshot: Settings | null = null;
    renderModels(client, (next) => (snapshot = next));
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "判断模型（Jev）" }));
    expect(screen.queryByText("main")).toBeNull();
    await user.click(screen.getByLabelText("新增提供商"));
    expect(snapshot!.providers[1]).toMatchObject({
      protocol: "systemone",
      base_url: "https://api.typesafe.ai/v1",
    });
    await user.click(screen.getByText("模型路由"));
    expect(screen.getByText("记忆模型")).toBeTruthy();
    expect(screen.queryByText("Chat 默认模型")).toBeNull();
    expect(screen.getByText(/准确比快重要/)).toBeTruthy();
  });
});

describe("memory page", () => {
  it("browses memories and shows evidence without offering edits", async () => {
    const client = fakeClient({
      memoriesPage: vi.fn().mockResolvedValue({
        total: 1,
        page: 1,
        page_size: 20,
        items: [
          {
            id: "v1",
            type: "event",
            status: "active",
            text: "[事件] 2026-10-29（周四） 梦寻发邮件说 issue #3 已处理完成  #v1",
          },
        ],
      }),
      memory: vi.fn().mockResolvedValue({
        id: "v1",
        type: "event",
        status: "active",
        evidence: [
          {
            source: "外部事件",
            quote: "issue #3 已经处理完成",
            recorded: "2026-10-29 14:32（周四）",
            verdict: "quoted",
          },
        ],
      }),
    } as Partial<ApiClient>);
    render(withQuery(<MemoryPage client={client} act={act} />));
    const user = userEvent.setup();
    expect(await screen.findByText(/梦寻发邮件说 issue #3/)).toBeTruthy();
    expect(screen.queryByText("记住")).toBeNull();
    await user.click(screen.getByText("详情与证据"));
    expect(await screen.findByText(/issue #3 已经处理完成/)).toBeTruthy();
    await user.click(screen.getByText("事件"));
    expect(client.memoriesPage).toHaveBeenLastCalledWith("event", "", 1, 20);
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

it("renders an Agent Loop as text and collapsible tool calls", async () => {
  const client = fakeClient();
  client.history = vi.fn().mockResolvedValue([
    { role: "user", content: "查邮件\n<context local_time='x'>[]</context>" },
    {
      role: "assistant",
      content: "我先看看",
      tool_calls: [
        {
          id: "c1",
          type: "function",
          function: { name: "mail_search", arguments: '{"query":"汇丰"}' },
        },
      ],
    },
    { role: "tool", tool_call_id: "c1", content: '{"count":1}' },
    { role: "assistant", content: "找到一封" },
  ]);
  render(withQuery(<ChatPage client={client} act={act} />));
  expect(await screen.findByText("我先看看")).toBeTruthy();
  expect(screen.getByText("找到一封")).toBeTruthy();
  expect(screen.getByText("查邮件")).toBeTruthy();
  expect(screen.getByText("mail_search")).toBeTruthy();
  expect(screen.getByText("汇丰")).toBeTruthy();
  expect(screen.getByText("完成")).toBeTruthy();
  expect(screen.queryByText(/tool_call_id/)).toBeNull();
});

it("shows the runtime log by default with level and source filters", async () => {
  const client = fakeClient();
  client.logs = vi.fn().mockResolvedValue({
    entries: [
      {
        at: "2026-10-30T08:00:01+08:00",
        level: "LOG",
        source: "qq",
        tags: ["群 1", "张三(42)"],
        message: "早上好",
      },
      {
        at: "2026-10-30T08:00:02+08:00",
        level: "WARNING",
        source: "Proactive",
        tags: [],
        message: "分诊模型输出无效",
      },
    ],
    sources: ["Proactive", "qq"],
  });
  render(withQuery(<LogsPage client={client} act={act} />));
  expect(await screen.findByText("早上好")).toBeTruthy();
  expect(screen.getByText("[张三(42)]")).toBeTruthy();
  expect(
    screen.getByText("分诊模型输出无效").closest(".level-warning"),
  ).toBeTruthy();
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("筛选级别"), "warning");
  await user.selectOptions(screen.getByLabelText("日志来源"), "qq");
  await waitFor(() =>
    expect(client.logs).toHaveBeenLastCalledWith({
      limit: 500,
      level: "warning",
      source: "qq",
      query: "",
    }),
  );
});

it("switches the record level from the runtime log tab", async () => {
  const client = fakeClient();
  render(withQuery(<LogsPage client={client} act={act} />));
  const select = (await screen.findByLabelText(
    "记录级别",
  )) as HTMLSelectElement;
  await waitFor(() => expect(select.disabled).toBe(false));
  expect(select.value).toBe("info");
  await userEvent.setup().selectOptions(select, "debug");
  expect(client.setLogLevel).toHaveBeenCalledWith("debug");
  await waitFor(() => expect(select.value).toBe("debug"));
});

it("shows the raw rolling log with a keyword filter", async () => {
  const client = fakeClient();
  client.rawLogs = vi
    .fn()
    .mockResolvedValue({ lines: ["2026-10-30 08:00:01 INFO  x: notify.sent"] });
  render(withQuery(<LogsPage client={client} act={act} />));
  const user = userEvent.setup();
  await user.click(screen.getByText("原始日志"));
  expect(await screen.findByLabelText("原始日志")).toBeTruthy();
  expect(screen.getByText(/notify\.sent/)).toBeTruthy();
  await user.type(screen.getByLabelText("过滤原始日志"), "n");
  await waitFor(() => expect(client.rawLogs).toHaveBeenCalledWith(500, "n"));
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
            echo: body.policy.sessions["web:owner"]?.mode,
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
    const toggle = await screen.findByLabelText("会话模式");
    const user = userEvent.setup();
    await user.selectOptions(toggle, "chat");
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
    await user.click(screen.getByLabelText("启用群聊上下文感知"));
    fireEvent.change(screen.getByLabelText("每轮注入消息条数"), {
      target: { value: "30" },
    });
    fireEvent.change(screen.getByLabelText("注入字符预算"), {
      target: { value: "6000" },
    });
    await user.click(screen.getByLabelText("提供 group_context_query 工具"));
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
      group_context: {
        enabled: true,
        inject_messages: 30,
        inject_chars: 6000,
        query_tool: false,
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

describe("session tool permissions", () => {
  it("edits modes, per-tool overrides and session defaults while isolating Proactive defaults", async () => {
    const { ApprovalsPage } = await import("../src/pages/ApprovalsPage");
    let latest = baseConfig();
    const client = fakeClient({
      tools: vi.fn().mockResolvedValue({
        session: "web:owner",
        policy: latest.policy.owner_default,
        source: "default",
        tools: [
          {
            name: "shell",
            description: "执行命令",
            plugin: "builtin",
            risk: "dangerous",
            safe: false,
            chat: false,
            permission: "manual",
            decision: "approve",
          },
          {
            name: "current_time",
            description: "查询时间",
            plugin: "builtin",
            risk: "safe",
            safe: true,
            chat: true,
            permission: "allow",
            decision: "allow",
          },
        ],
      }),
      records: vi.fn().mockResolvedValue([]),
    });
    function Harness() {
      const [config, setConfig] = useState(latest);
      return (
        <ApprovalsPage
          client={client}
          act={async (work) => {
            await work();
          }}
          config={config}
          setConfig={(next) => {
            latest = next;
            setConfig(next);
          }}
        />
      );
    }
    render(withQuery(<Harness />));
    const user = userEvent.setup();
    const shell = await screen.findByLabelText("shell 权限");
    await user.selectOptions(shell, "auto");
    expect(latest.policy.sessions["web:owner"].tools.shell).toBe("auto");
    await user.selectOptions(screen.getByLabelText("权限预设"), "full_access");
    expect(latest.policy.sessions["web:owner"].tools).toEqual({});
    expect(latest.policy.sessions["web:owner"].preset).toBe("full_access");
    await user.selectOptions(screen.getByLabelText("会话模式"), "chat");
    expect(latest.policy.sessions["web:owner"].mode).toBe("chat");
    expect(screen.getByText(/当前为 Chat 模式/)).toBeTruthy();
    expect(screen.getAllByText("当前不可用")).toHaveLength(1);
    expect(screen.getByText("权限：无需审批")).toBeTruthy();
    await user.selectOptions(
      screen.getByLabelText("配置范围"),
      "proactive_default",
    );
    expect(screen.queryByLabelText("会话模式")).toBeNull();
    await user.selectOptions(screen.getByLabelText("权限预设"), "safe_auto");
    await user.selectOptions(screen.getByLabelText("shell 权限"), "deny");
    expect(latest.policy.proactive_default).toEqual({
      preset: "safe_auto",
      tools: { shell: "deny" },
    });
    expect(latest.policy.sessions["web:owner"].mode).toBe("chat");
    await user.selectOptions(screen.getByLabelText("配置范围"), "session");
    await user.clear(screen.getByLabelText("Main 会话 ID"));
    await user.type(screen.getByLabelText("Main 会话 ID"), "group:qq:42");
    await user.click(screen.getByText("选择会话"));
    expect((screen.getByLabelText("会话模式") as HTMLSelectElement).value).toBe(
      "chat",
    );
    await user.selectOptions(screen.getByLabelText("会话模式"), "agent");
    expect(latest.policy.sessions["group:qq:42"].mode).toBe("agent");
    await user.click(screen.getByText("恢复继承默认"));
    expect(latest.policy.sessions["group:qq:42"]).toBeUndefined();
    expect(latest.policy.sessions["web:owner"].mode).toBe("chat");
  });

  it("shows model review results separately from human approvals", async () => {
    const { ApprovalsPage } = await import("../src/pages/ApprovalsPage");
    const client = fakeClient({
      recordsPage: vi
        .fn()
        .mockImplementation(async (_kind, page, page_size, filter) => {
          const items = [
            {
              id: "human",
              tool: "shell",
              status: "pending",
              reviewer: "human",
              args: { command: "echo hi" },
              scope: "web:owner",
              expires: 2000000000,
            },
            {
              id: "model",
              tool: "shell",
              status: "denied",
              reviewer: "model",
              args: { command: "rm file" },
              scope: "background:proactive:a",
              reason: "未获明确授权",
            },
          ].filter((row) =>
            filter === "pending"
              ? row.status === "pending" && row.reviewer !== "model"
              : row.status !== "pending" || row.reviewer === "model",
          );
          return { items, total: items.length, page, page_size };
        }),
    });
    render(
      withQuery(
        <ApprovalsPage
          client={client}
          config={baseConfig()}
          setConfig={() => {}}
          act={async (work) => {
            await work();
          }}
        />,
      ),
    );
    await screen.findByText("待人工审批 · 1");
    expect(screen.getAllByRole("button", { name: "允许这一次" })).toHaveLength(
      1,
    );
    expect(screen.getByText(/未获明确授权/)).toBeTruthy();
    expect(screen.getByText("已拒绝")).toBeTruthy();
  });
});

describe("paginated lists and web search settings", () => {
  it("pages events through the API", async () => {
    const { EventsPage } = await import("../src/pages/EventsPage");
    const recordsPage = vi
      .fn()
      .mockImplementation(async (_kind, page, pageSize) => ({
        items: [
          {
            id: `event-${page}`,
            status: "done",
            event: { title: `事件第${page}页`, source: "test", kind: "test" },
          },
        ],
        total: 21,
        page,
        page_size: pageSize,
      }));
    render(
      withQuery(<EventsPage client={fakeClient({ recordsPage })} act={act} />),
    );
    await screen.findByText("事件第1页");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "主动动态下一页" }));
    await screen.findByText("事件第2页");
    expect(recordsPage).toHaveBeenLastCalledWith("event", 2, 20);
    expect(screen.queryByText("事件第1页")).toBeNull();
    expect(
      (
        screen.getByRole("button", {
          name: "主动动态下一页",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("resets memory pagination when searching or changing category", async () => {
    const memoriesPage = vi
      .fn()
      .mockImplementation(async (kind, query, page, pageSize) => ({
        items: [
          {
            id: "f1",
            type: "fact",
            status: "active",
            text: `记忆 ${kind} ${query} 第${page}页`,
          },
        ],
        total: 21,
        page,
        page_size: pageSize,
      }));
    render(
      withQuery(<MemoryPage client={fakeClient({ memoriesPage })} act={act} />),
    );
    const user = userEvent.setup();
    await screen.findByText("记忆 第1页");
    await user.click(screen.getByRole("button", { name: "记忆库下一页" }));
    await screen.findByText("记忆 第2页");
    await user.type(screen.getByLabelText("搜索记忆"), "关键词");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() =>
      expect(memoriesPage).toHaveBeenLastCalledWith("", "关键词", 1, 20),
    );
    await user.click(screen.getByRole("button", { name: "记忆库下一页" }));
    await waitFor(() =>
      expect(memoriesPage).toHaveBeenLastCalledWith("", "关键词", 2, 20),
    );
    await user.click(screen.getByRole("button", { name: "事实" }));
    await waitFor(() =>
      expect(memoriesPage).toHaveBeenLastCalledWith("fact", "关键词", 1, 20),
    );
  });

  it("keeps tool edits across pages and searches the complete catalog", async () => {
    const { ApprovalsPage } = await import("../src/pages/ApprovalsPage");
    const tools = Array.from({ length: 41 }, (_, i) => ({
      name: `tool_${String(i).padStart(2, "0")}`,
      plugin: "builtin",
      safe: true,
      risk: "safe",
      chat: true,
      description: "说明",
    }));
    const client = fakeClient({ tools: vi.fn().mockResolvedValue({ tools }) });
    let latest = baseConfig();
    function Harness() {
      const [config, setConfig] = useState(latest);
      latest = config;
      return (
        <ApprovalsPage
          client={client}
          config={config}
          setConfig={setConfig}
          act={act}
        />
      );
    }
    render(withQuery(<Harness />));
    const user = userEvent.setup();
    await screen.findByLabelText("tool_00 权限");
    expect(screen.queryByLabelText("tool_20 权限")).toBeNull();
    expect(screen.queryByLabelText("合并连续消息")).toBeNull();
    await user.selectOptions(screen.getByLabelText("tool_00 权限"), "deny");
    await user.click(screen.getByRole("button", { name: "工具列表下一页" }));
    expect(screen.getByLabelText("tool_20 权限")).toBeTruthy();
    await user.type(screen.getByLabelText("搜索工具"), "tool_40");
    expect(screen.getByLabelText("tool_40 权限")).toBeTruthy();
    expect(screen.queryByLabelText("tool_20 权限")).toBeNull();
    await user.clear(screen.getByLabelText("搜索工具"));
    expect(
      (screen.getByLabelText("tool_00 权限") as HTMLSelectElement).value,
    ).toBe("deny");
    expect(latest.policy.sessions["web:owner"].tools.tool_00).toBe("deny");
  });

  it("edits provider-specific search options without losing other providers", async () => {
    let latest = baseConfig();
    function Harness() {
      const [config, setConfig] = useState(latest);
      latest = config;
      return (
        <SettingsPage
          client={fakeClient()}
          config={config}
          setConfig={setConfig}
          act={act}
        />
      );
    }
    render(withQuery(<Harness />));
    const user = userEvent.setup();
    expect(screen.getByLabelText("合并连续消息")).toBeTruthy();
    await user.selectOptions(screen.getByLabelText("搜索深度"), "advanced");
    await user.selectOptions(screen.getByLabelText("搜索提供商"), "serpapi");
    await user.clear(screen.getByLabelText("搜索地区"));
    await user.type(screen.getByLabelText("搜索地区"), "us");
    await user.selectOptions(screen.getByLabelText("搜索提供商"), "deepseek");
    await user.click(screen.getByLabelText("返回网页正文"));
    await user.click(screen.getByLabelText("启用网页搜索"));
    expect(latest.web_search.deepseek.need_content).toBe(true);
    expect(latest.web_search.enabled).toBe(false);
    expect(latest.web_search.serpapi.gl).toBe("us");
    await user.selectOptions(screen.getByLabelText("搜索提供商"), "tavily");
    expect((screen.getByLabelText("搜索深度") as HTMLSelectElement).value).toBe(
      "advanced",
    );
    expect(latest.web_search.tavily.api_key).toBe("env:TAVILY_API_KEY");
  });
});
