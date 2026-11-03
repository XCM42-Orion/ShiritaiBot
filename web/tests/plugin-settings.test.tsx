import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PluginSettingSchema } from "../src/api/types";
import { PluginSettingsForm } from "../src/components/PluginSettingsForm";

afterEach(cleanup);

function setup(
  properties: Record<string, PluginSettingSchema>,
  settings: Record<string, unknown> = {},
  defaults: Record<string, unknown> = {},
) {
  const changes = vi.fn<(value: Record<string, unknown>) => void>();
  function Harness() {
    const [value, setValue] = useState(settings);
    return (
      <PluginSettingsForm
        plugin={{ id: "demo", enabled: true, settings: value }}
        metadata={{
          id: "demo",
          enabled: true,
          name: "Demo",
          description: "",
          status: "loaded",
          error: "",
          settings_schema: { type: "object", properties },
          defaults,
        }}
        onChange={(next) => {
          setValue(next);
          changes(next);
        }}
      />
    );
  }
  render(<Harness />);
  return {
    user: userEvent.setup(),
    last: () => changes.mock.calls.at(-1)?.[0],
  };
}

it("renders nested objects as a fieldset instead of a JSON box", async () => {
  const { user, last } = setup(
    {
      provider: {
        type: "object",
        title: "服务商",
        properties: {
          endpoint: { type: "string", title: "地址", default: "" },
          retry: { type: "integer", title: "重试次数", default: 2 },
        },
        default: { endpoint: "", retry: 2 },
      },
    },
    { provider: { endpoint: "", retry: 2, extra: true } },
  );
  const group = screen.getByRole("group", { name: "服务商" });
  expect(group.tagName).toBe("FIELDSET");
  expect(screen.queryByRole("textbox", { name: "服务商" })).toBeNull();
  await user.type(within(group).getByRole("textbox", { name: "地址" }), "x");
  expect(last()).toEqual({
    provider: { endpoint: "x", retry: 2, extra: true },
  });
});

it("renders sliders with a linked number input", () => {
  const { last } = setup(
    {
      temperature: {
        type: "number",
        title: "温度",
        minimum: 0,
        maximum: 2,
        "x-step": 0.1,
        "x-widget": "slider",
        default: 0.7,
      },
    },
    {},
    { temperature: 0.7 },
  );
  const range = screen.getByRole("slider", { name: "温度滑块" });
  expect(range.getAttribute("max")).toBe("2");
  expect(range.getAttribute("step")).toBe("0.1");
  fireEvent.change(range, { target: { value: "1.5" } });
  expect(last()).toEqual({ temperature: 1.5 });
  expect(
    (screen.getByRole("spinbutton", { name: "温度" }) as HTMLInputElement)
      .value,
  ).toBe("1.5");
});

it("shows enum labels and subtitles but stores the raw option", async () => {
  const { user, last } = setup(
    {
      mode: {
        type: "string",
        title: "模式",
        enum: ["fast", "slow"],
        "x-labels": ["快速", "慢速"],
        "x-subtitles": ["低延迟", "更准确"],
        default: "fast",
      },
      platforms: {
        type: "array",
        title: "平台",
        items: { type: "string", enum: ["qq", "web"] },
        "x-labels": ["QQ", "网页"],
        default: [],
      },
    },
    { mode: "fast", platforms: [] },
  );
  const select = screen.getByRole("combobox", { name: "模式" });
  expect(within(select).getByRole("option", { name: "慢速" })).toBeTruthy();
  expect(screen.getByText("低延迟")).toBeTruthy();
  await user.selectOptions(select, "slow");
  expect(last()?.mode).toBe("slow");
  expect(screen.getByText("更准确")).toBeTruthy();
  await user.click(screen.getByRole("checkbox", { name: "网页" }));
  expect(last()?.platforms).toEqual(["web"]);
});

it("hides invisible fields but preserves their values", async () => {
  const { user, last } = setup(
    {
      token_cache: { type: "string", title: "缓存", "x-invisible": true },
      name: { type: "string", title: "名称" },
    },
    { token_cache: "keep-me", name: "" },
  );
  expect(screen.queryByRole("textbox", { name: "缓存" })).toBeNull();
  expect(screen.queryByText("缓存")).toBeNull();
  await user.type(screen.getByRole("textbox", { name: "名称" }), "a");
  expect(last()).toEqual({ token_cache: "keep-me", name: "a" });
});

it("adds, edits and removes template_list entries", async () => {
  const { user, last } = setup(
    {
      sources: {
        type: "array",
        title: "订阅源",
        "x-widget": "template_list",
        "x-templates": {
          rss: {
            name: "RSS",
            hint: "普通 RSS 订阅",
            display_item: "feed.url",
            hide_hint_in_list: false,
            properties: {
              feed: {
                type: "object",
                title: "源",
                properties: {
                  url: { type: "string", title: "地址", default: "" },
                },
                default: { url: "" },
              },
              interval: { type: "integer", title: "间隔", default: 30 },
            },
          },
        },
        items: { type: "object" },
        default: [],
      },
    },
    {
      sources: [{ __template_key: "rss", feed: { url: "https://a.example" } }],
    },
  );
  expect(screen.getByText("https://a.example")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "添加条目" }));
  expect(last()?.sources).toEqual([
    { __template_key: "rss", feed: { url: "https://a.example" } },
    { __template_key: "rss", feed: { url: "" }, interval: 30 },
  ]);
  const inputs = screen.getAllByRole("spinbutton", { name: "间隔" });
  fireEvent.change(inputs[1], { target: { value: "45" } });
  expect((last()?.sources as unknown[])[1]).toMatchObject({ interval: 45 });
  await user.click(screen.getByRole("button", { name: "移除第 1 项 RSS" }));
  expect(last()?.sources).toEqual([
    { __template_key: "rss", feed: { url: "" }, interval: 45 },
  ]);
  expect(screen.queryByText("https://a.example")).toBeNull();
});

it("edits dict values through the template schema", async () => {
  const { user, last } = setup(
    {
      params: {
        type: "object",
        title: "模型参数",
        "x-widget": "dict",
        additionalProperties: true,
        "x-template-schema": {
          top_p: {
            type: "number",
            title: "Top P",
            "x-name": "top_p",
            default: 1,
          },
          stop: {
            type: "string",
            title: "停止词",
            "x-name": "stop",
            default: "",
          },
        },
        default: {},
      },
    },
    { params: { top_p: 0.9 } },
  );
  expect(
    (screen.getByRole("spinbutton", { name: "Top P" }) as HTMLInputElement)
      .value,
  ).toBe("0.9");
  const add = screen.getByRole("combobox", { name: "模型参数可添加参数" });
  expect(within(add).queryByRole("option", { name: "Top P" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "添加参数" }));
  expect(last()?.params).toEqual({ top_p: 0.9, stop: "" });
  await user.type(screen.getByRole("textbox", { name: "停止词" }), "END");
  expect(last()?.params).toEqual({ top_p: 0.9, stop: "END" });
  await user.click(screen.getByRole("button", { name: "移除参数 Top P" }));
  expect(last()?.params).toEqual({ stop: "END" });
  expect(screen.getByRole("button", { name: "添加参数" })).toBeTruthy();
});

it("keeps rendering legacy JSON Schema settings", async () => {
  const { user, last } = setup(
    {
      api_key: { type: "string", title: "密钥配置", writeOnly: true },
      region: { type: "string", title: "地区", enum: ["cn", "us"] },
      limit: { type: "integer", title: "上限", minimum: 1, maximum: 9 },
      verbose: { type: "boolean", title: "详细日志" },
      groups: { type: "array", title: "群号", items: { type: "string" } },
      matrix: { type: "array", title: "矩阵", items: { type: "number" } },
      raw: { type: "object", title: "原始配置" },
      note: {
        type: "string",
        title: "备注",
        description: "随便写",
        "x-obvious-hint": true,
      },
    },
    { region: "cn", limit: 3, groups: ["1"], matrix: [1], raw: { a: 1 } },
    { undeclared: 5 },
  );
  expect(screen.getByPlaceholderText("直接输入密钥")).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "地区" })).toBeTruthy();
  expect(
    screen.getByRole("spinbutton", { name: "上限" }).getAttribute("max"),
  ).toBe("9");
  expect(screen.getByRole("checkbox", { name: "详细日志" })).toBeTruthy();
  expect(
    (screen.getByRole("textbox", { name: "群号" }) as HTMLTextAreaElement)
      .value,
  ).toBe("1");
  expect(screen.getByRole("textbox", { name: "矩阵" })).toBeTruthy();
  expect(screen.getByRole("textbox", { name: "原始配置" })).toBeTruthy();
  expect(screen.getByText("随便写").className).toContain("field-help-obvious");
  expect(screen.getByRole("spinbutton", { name: "undeclared" })).toBeTruthy();
  await user.click(screen.getByRole("checkbox", { name: "详细日志" }));
  expect(last()).toMatchObject({ verbose: true, undeclared: 5, raw: { a: 1 } });
  expect(screen.getByText("编辑完整配置 JSON")).toBeTruthy();
});
