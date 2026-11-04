# WebUI 对话体验重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修好 WebUI 对话的渲染顺序与工具调用噪音，新增参照 DeepSeek Harness 的「对话模式」布局，并把交互控件换成更鲜明、更清晰的一套视觉规范。

**Architecture:** 前端只改 `web/`。先把渲染与分组逻辑抽成纯函数（`lib/sessions.ts`、`lib/timeline.ts`）并用单元测试钉住，再用新的 `AgentTimeline` 渲染（用户气泡 + 连续工具活动折叠组），随后依次搭出 `ChatComposer`、`ChatSidebar`、`ChatWorkspace`，最后在 `App.tsx` 里加 `console` / `chat` 双模式切换并统一视觉 token。后端接口、Agent 循环、审批行为都不动。

**Tech Stack:** React 19、TypeScript 5.8、Vite 6、TanStack Query 5、lucide-react、Vitest 4 + Testing Library（jsdom）、原生 CSS（`src/styles/*.css`）。

**Spec:** `docs/superpowers/specs/2026-11-04-webui-redesign-design.md`

## Global Constraints

- 不新增运行时依赖；不新增后端接口，只用 `/api/records/{kind}`、`/api/history`、`/api/messages`、`/api/approvals/{id}`、`/api/commands`。
- 只有 `web:` 会话可发送消息；`private:` / `group:` / `background:` 会话只读，沿用文案「这里只发送 WebUI 会话。QQ 会话请直接在 QQ 里对话。」
- 模式持久化 key 必须是 `localStorage["shiritaibot-mode"]`，取值只有 `"console"` / `"chat"`，默认 `"console"`。
- 所有界面文案为简体中文；不用 emoji，图标一律 `lucide-react`。
- 工作目录是 `web/`；测试命令都在 `web/` 下执行。
- **不要运行 `git commit` 或 `git push`**（仓库主人自己提交）。每个任务以「检查 diff」收尾。
- 保留现有 `aria-label`（`消息`、`发送消息`、`停止当前任务`）和可见按钮文案（`新会话` 在控制台页保留，对话模式用 `新对话`）。
- `records("turn")` 返回的字段是 `status` / `started` / `finished` / `scope`（没有 `at`）；时间一律从 `started`（退而 `finished`）解析。

## Review Focus

1. **历史以孤儿 `tool` 消息开头**（上下文截断、中途快照）——不能崩溃、不能吞掉后面的消息；由 Task 2 的 `buildTimeline` 测试钉住。
2. **多个工具的返回顺序与调用顺序不一致**——结果必须按 `tool_call_id` 挂到对应调用上，不能错位或重复；由 Task 2 测试钉住。
3. **工具调用还没有返回结果**（执行中）——状态必须是「执行中」而不是「失败」，折叠组默认仍然收起；由 Task 2/3 测试钉住。
4. **`records("turn")` 混入其他会话或 `started` 无法解析**——分组不能串会话、不能抛异常；由 Task 1 测试钉住。
5. **选中的会话在列表中消失**（新建对话后刷新、列表收缩）——工作区仍要能渲染并显示空态，不能崩；由 Task 6 测试钉住。

---

## File Structure

新建：

- `web/src/lib/sessions.ts` — 会话前缀归类（`sessionRole`）、标题、相对时间、`turn` 记录派生（最近活动 / 运行中 / 最近状态）。
- `web/src/lib/timeline.ts` — 历史消息 → 渲染时间线（用户、助手文本、连续工具折叠组），工具摘要与失败判定。
- `web/src/components/ToolGroup.tsx` — 一个折叠组，内含逐条工具调用。
- `web/src/components/ChatComposer.tsx` — 输入框、斜杠命令菜单、停止/发送，控制台页与对话模式共用。
- `web/src/components/ChatSidebar.tsx` — 四组会话栏，含运行中/待审批标记与抽屉开关。
- `web/src/components/ChatWorkspace.tsx` — 对话模式整页：数据查询、布局、内联审批卡、只读会话提示。
- `web/src/styles/chat.css` — 对话模式布局与消息气泡样式。
- `web/tests/chat-helpers.tsx` — 测试共用的 fake client 与小工具。
- `web/tests/chat-sessions.test.tsx`、`web/tests/chat-timeline.test.tsx`、`web/tests/chat-render.test.tsx`、`web/tests/chat-sidebar.test.tsx`、`web/tests/chat-workspace.test.tsx`。

修改：

- `web/src/api/types.ts` — 新增 `TurnRecord` 与 `RecordMap["turn"]`。
- `web/src/components/AgentTimeline.tsx` — 重写渲染（保留文件与导出名，`ChatPage` 与对话模式共用）。
- `web/src/pages/ChatPage.tsx` — 删除 `delivered` 区块与 `reply` 查询，改用 `ChatComposer`、传 `failed`。
- `web/src/components/AppShell.tsx` — 顶栏新增「对话模式」按钮。
- `web/src/App.tsx` — 模式状态、模式持久化、渲染 `ChatWorkspace`、齿轮跳设置。
- `web/src/styles/tokens.css`、`web/src/styles/base.css`、`web/src/styles/pages.css`、`web/src/styles/index.css`。
- `web/tests/console.test.tsx` — Task 3 里唯一一处断言改名（名字在摘要与行内各出现一次）。

---

### Task 1: 会话列表模型

**Files:**
- Create: `web/src/lib/sessions.ts`
- Modify: `web/src/api/types.ts`（`RecordMap` 附近）
- Test: `web/tests/chat-sessions.test.tsx`

**Interfaces:**
- Consumes: `TurnRecord`（本任务定义）、`TIME_ZONE`（来自 `web/src/lib/utils.ts`）。
- Produces:
  - `type SessionGroupKey = "local" | "private" | "group" | "background"`
  - `interface SessionItem { id: string; title: string }`
  - `interface SessionGroup { key: SessionGroupKey; label: string; items: SessionItem[] }`
  - `sessionRole(id: string): SessionGroupKey`
  - `sessionTitle(id: string): string`
  - `relativeTime(at: number, now?: number): string`
  - `sessionActivity(turns: TurnRecord[]): Record<string, number>`
  - `runningSessions(turns: TurnRecord[]): Set<string>`
  - `latestTurnStatus(turns: TurnRecord[], session: string): string | null`
  - `sessionGroups(ids: string[], activity: Record<string, number>): SessionGroup[]`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from "vitest";
import type { TurnRecord } from "../src/api/types";
import {
  latestTurnStatus,
  relativeTime,
  runningSessions,
  sessionActivity,
  sessionGroups,
  sessionRole,
  sessionTitle,
} from "../src/lib/sessions";

const turn = (scope: string, status: string, started: string): TurnRecord => ({
  id: `${scope}:${started}`,
  scope,
  status,
  started,
});

describe("sessionTitle", () => {
  it("names every session kind from its id", () => {
    expect(sessionTitle("web:owner")).toBe("默认对话");
    expect(sessionTitle("web:1762321920000")).toMatch(
      /^对话 \d{2}-\d{2} \d{2}:\d{2}$/,
    );
    expect(sessionTitle("private:qq:2064438401")).toBe("QQ 2064438401");
    expect(sessionTitle("group:qq:820215017")).toBe("群 820215017");
    expect(sessionTitle("background:proactive:08c31f9a")).toBe("后台任务 08c31f");
    expect(sessionTitle("custom:abc")).toBe("custom:abc");
  });
});

describe("sessionRole", () => {
  it("maps prefixes to the four groups and defaults to local", () => {
    expect(sessionRole("web:owner")).toBe("local");
    expect(sessionRole("private:qq:1")).toBe("private");
    expect(sessionRole("group:qq:1")).toBe("group");
    expect(sessionRole("background:proactive:x")).toBe("background");
    expect(sessionRole("other:1")).toBe("local");
  });
});

describe("relativeTime", () => {
  it("formats recent activity and date for older activity", () => {
    const now = Date.parse("2026-11-04T12:00:00+08:00");
    expect(relativeTime(0, now)).toBe("");
    expect(relativeTime(now - 30_000, now)).toBe("刚刚");
    expect(relativeTime(now - 5 * 60_000, now)).toBe("5 分钟前");
    expect(relativeTime(now - 3 * 3_600_000, now)).toBe("3 小时前");
    expect(relativeTime(now - 30 * 86_400_000, now)).toMatch(/^\d{2}-\d{2}$/);
  });
});

describe("turn records", () => {
  const rows = [
    turn("web:a", "running", "2026-11-04T10:00:00+08:00"),
    turn("web:a", "completed", "2026-11-04T11:00:00+08:00"),
    turn("private:qq:1", "running", "2026-11-04T09:00:00+08:00"),
    turn("group:qq:3", "completed", "not-a-date"),
  ];

  it("derives latest activity and running sessions per scope", () => {
    const activity = sessionActivity(rows);
    expect(activity["web:a"]).toBe(Date.parse("2026-11-04T11:00:00+08:00"));
    expect(activity["group:qq:3"] ?? 0).toBe(0);
    expect([...runningSessions(rows)].sort()).toEqual([
      "private:qq:1",
      "web:a",
    ]);
    expect(latestTurnStatus(rows, "web:a")).toBe("completed");
    expect(latestTurnStatus([], "web:a")).toBeNull();
  });
});

describe("sessionGroups", () => {
  it("groups, sorts by activity and omits empty groups", () => {
    const activity = sessionActivity([
      turn("web:a", "completed", "2026-11-04T10:00:00+08:00"),
      turn("private:qq:2", "running", "2026-11-04T11:00:00+08:00"),
      turn("private:qq:1", "completed", "2026-11-04T09:00:00+08:00"),
    ]);
    const groups = sessionGroups(
      [
        "web:owner",
        "web:a",
        "private:qq:1",
        "private:qq:2",
        "group:qq:3",
        "background:proactive:abc123",
      ],
      activity,
    );
    expect(groups.map((group) => group.key)).toEqual([
      "local",
      "private",
      "group",
      "background",
    ]);
    expect(groups[0].items.map((item) => item.id)).toEqual(["web:a", "web:owner"]);
    expect(groups[1].items.map((item) => item.id)).toEqual([
      "private:qq:2",
      "private:qq:1",
    ]);
    expect(groups[3].items[0].title).toBe("后台任务 abc123");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/chat-sessions.test.tsx`
Expected: FAIL — `Failed to resolve import "../src/lib/sessions"`.

- [ ] **Step 3: Add `TurnRecord` to `web/src/api/types.ts`**

放在 `SessionRecord` 后面；只包含前端用到的字段（后端记录可能还有别的，忽略即可）：

```ts
export interface TurnRecord {
  id: string;
  scope?: string;
  status: string;
  started?: string;
  finished?: string;
  steps?: number;
  route?: string;
  actor?: string;
}
```

并在 `RecordMap` 中加入 `turn: TurnRecord;`。

- [ ] **Step 4: Implement `web/src/lib/sessions.ts`**

- `sessionRole` 按前缀判断：`web:` → `local`，`private:` → `private`，`group:` → `group`，`background:` → `background`，其余 `local`。
- `sessionTitle` 规则见 Spec 第 3 节：`web:owner` → `默认对话`；`web:<数字>` → `对话 MM-DD HH:mm`（用 `Intl.DateTimeFormat(..., { timeZone: TIME_ZONE, month/day/hour/minute: "2-digit", hour12: false }).formatToParts` 拼成 `MM-DD HH:mm`，不要依赖分隔符）；`web:<非数字>` → `对话 <后缀>`；`private:<adapter>:<uid>` → `QQ <uid>`；`group:<adapter>:<gid>` → `群 <gid>`；`background:proactive:<hash>` → `后台任务 <hash 前 6 位>`；其他原样返回。
- `relativeTime(at, now = Date.now())`：`!at` → `""`；< 60s → `刚刚`；< 60min → `N 分钟前`；< 24h → `N 小时前`；否则 `MM-DD`（同样用 Asia/Shanghai）。
- `turnTime(turn)`：`Date.parse(started ?? finished ?? "")`，`NaN` 视为 0。
- `sessionActivity`：按 `scope` 取最大 `turnTime`；`turns` 里没有 `scope` 的条目跳过。
- `runningSessions`：`status === "running"` 且有 `scope` 的集合。
- `latestTurnStatus`：只统计该会话的 turn，取 `turnTime` 最大者的 `status`，没有则 `null`。
- `sessionGroups`：固定顺序 `local, private, group, background`；组内按 `activity[id] ?? 0` 降序、同分按 `id` 升序；空组不输出。

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run tests/chat-sessions.test.tsx`
Expected: PASS（5 个 describe 全绿）。

- [ ] **Step 6: Checkpoint**

Run: `git status --short web/` 并汇报改动文件。不要提交。

---

### Task 2: 时间线模型

**Files:**
- Create: `web/src/lib/timeline.ts`
- Test: `web/tests/chat-timeline.test.tsx`

**Interfaces:**
- Consumes: `HistoryMessage`、`ToolCall`（`web/src/api/types.ts`）。
- Produces:
  - `interface ToolItem { call: ToolCall; result?: HistoryMessage }`
  - `type TimelineEntry = { kind: "user"; message: HistoryMessage } | { kind: "assistant"; message: HistoryMessage } | { kind: "tools"; items: ToolItem[] }`
  - `buildTimeline(history: HistoryMessage[]): TimelineEntry[]`
  - `toolFailure(result: HistoryMessage | undefined): boolean`
  - `toolSummary(items: ToolItem[]): { count: number; names: string }`

- [ ] **Step 1: Write the failing test**

```tsx
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/chat-timeline.test.tsx`
Expected: FAIL — 找不到 `../src/lib/timeline`。

- [ ] **Step 3: Implement `web/src/lib/timeline.ts`**

- 遍历历史：
  - `user` → 推入 `{ kind: "user" }`。
  - `assistant`：`content?.trim()` 非空则先推入 `{ kind: "assistant" }`；若有 `tool_calls`，把每个调用包成 `ToolItem` 追加到「最近的 tools 条目」——仅当当前最后一个条目是 `tools` 时复用，否则新建一个；没有 `tool_calls` 且内容为空则整条跳过。
  - `tool`：只在上一个条目是 `tools` 时，按 `tool_call_id` 找 `items.find(item => item.call.id === id && !item.result)` 并赋 `result`；找不到（孤儿或重复）就忽略。
- `toolFailure(result)`：`result` 为空 → `false`；`JSON.parse(result.content)` 成功且是普通对象且含 `"error"` 键 → `true`，否则 `false`（解析失败按文本处理，不算失败）。
- `toolSummary(items)`：`count = items.length`；按首次出现顺序统计工具名次数，`names = 名称 + (次数 > 1 ? " ×" + 次数 : "")` 用 `", "` 连接。

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/chat-timeline.test.tsx`
Expected: PASS（5 个测试）。

- [ ] **Step 5: Checkpoint**

Run: `git status --short web/` 并汇报。不要提交。

---

### Task 3: 消息渲染（气泡 + 折叠的连续工具组）

**Files:**
- Modify: `web/src/components/AgentTimeline.tsx`（整文件重写）
- Create: `web/src/components/ToolGroup.tsx`
- Modify: `web/src/pages/ChatPage.tsx`（删除 `delivered` / `replies`，传 `failed`）
- Modify: `web/tests/console.test.tsx:1264`（`getByText("mail_search")` → `getAllByText`）
- Test: `web/tests/chat-render.test.tsx`

**Interfaces:**
- Consumes: `buildTimeline`、`toolFailure`、`toolSummary`（Task 2）；`latestTurnStatus`（Task 1）；`Markdown` 组件。
- Produces:
  - `AgentTimeline({ history, assistantName, userLabel?, failed? }: { history: HistoryMessage[]; assistantName: string; userLabel?: string; failed?: boolean })`
  - `ToolGroup({ items }: { items: ToolItem[] })`

- [ ] **Step 1: Write the failing test**

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ApiClient } from "../src/api/client";
import type { HistoryMessage } from "../src/api/types";
import { AgentTimeline } from "../src/components/AgentTimeline";
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/chat-render.test.tsx`
Expected: FAIL — 没有 `.tool-group` / `.failed-turn`，且 `ChatPage` 仍会渲染「已投递」。

- [ ] **Step 3: Implement `web/src/components/ToolGroup.tsx`**

结构（沿用现有 `.tool-call` 样式类，新增 `.tool-group`）：

- 外层 `<details className="tool-group">`，**不带 `open`**（默认收起）。
- `<summary>`：`<Wrench size={14} />` + `{count} 次工具调用` + `<span className="tool-names">{names}</span>` + `<ChevronRight size={14} className="chevron" />`。
- 展开后每行一个 `<details className={"tool-call" + (failed ? " failed" : "")}>`：summary 显示 `Wrench` + 工具名 + 状态标签（`执行中` / `失败` / `完成`）+ chevron；内容显示「参数」（对象展开为 `dl.tool-args`，否则 `pre`）与「返回结果」子 `<details className="tool-result">`。
- 参数/结果的展示沿用旧 `AgentTimeline` 里的 `parse` 与 `show`：字符串值原样显示（不要改成 `JSON.stringify`），对象/数组才 `JSON.stringify(value, null, 2)`——现有测试依赖裸字符串如 `汇丰`。
- 状态与失败判定一律来自 `toolFailure`；`result === undefined` → 「执行中」。

- [ ] **Step 4: Rewrite `web/src/components/AgentTimeline.tsx`**

- `buildTimeline(history)` 后按条目渲染：
  - `user`：`<div className="message user"><small>{userLabel}</small><p>{content?.split("\n<context")[0]}</p></div>`。
  - `assistant`：`<div className="message assistant"><small>{message._speaker || assistantName}</small><div className="assistant-body"><Markdown text={message.content ?? ""} /></div></div>`；若 `failed` 且这是最后一条 assistant 条目，追加 `failed-turn` 类并在 `<small>` 前渲染 `<span className="turn-failed">本轮失败</span>`。
  - `tools`：`<ToolGroup items={entry.items} />`。
- 删除旧的 `Block` / `blocks()` / `ToolCallView`（逻辑已迁到 `lib/timeline.ts` 与 `ToolGroup.tsx`）。

- [ ] **Step 5: Update `web/src/pages/ChatPage.tsx`**

- 删除 `replies` 查询与 `delivered` 变量、`delivered.map(...)` 区块。
- 新增 turns 查询：`queryKey: ["records", "turn"]`，`client.records("turn")`，`refetchInterval: 4000`。
- 给 `AgentTimeline` 传 `failed={latestTurnStatus(turns.data ?? [], session) === "failed"}`。
- 图片/表单结构暂不动（Task 4 再抽 composer）。

- [ ] **Step 6: Fix the one assertion in `web/tests/console.test.tsx:1264`**

`expect(screen.getByText("mail_search")).toBeTruthy()` → `expect(screen.getAllByText("mail_search").length).toBeGreaterThan(0)`（工具名现在同时出现在折叠组摘要与行内）。

- [ ] **Step 7: Run tests to verify they pass**

Run: `npx vitest run tests/chat-render.test.tsx tests/console.test.tsx`
Expected: PASS；`console.test.tsx` 其余断言不变。

- [ ] **Step 8: Checkpoint**

Run: `git status --short web/` 并汇报。不要提交。

---

### Task 4: 共用输入框

**Files:**
- Create: `web/src/components/ChatComposer.tsx`
- Modify: `web/src/pages/ChatPage.tsx`
- Test: `web/tests/chat-render.test.tsx`（追加一个 describe）

**Interfaces:**
- Consumes: `ApiClient`、`Action`（`web/src/pages/common.ts`）。
- Produces: `ChatComposer({ client, act, session, assistantName }: { client: ApiClient; act: Action; session: string; assistantName: string })`

- [ ] **Step 1: Write the failing test**

在 `web/tests/chat-render.test.tsx` 末尾追加：

```tsx
import userEvent from "@testing-library/user-event";
import { ChatComposer } from "../src/components/ChatComposer";

describe("ChatComposer", () => {
  it("sends, clears and fills slash commands", async () => {
    const client = {
      commands: vi
        .fn()
        .mockResolvedValue([{ name: "help", description: "命令帮助" }]),
      sendMessage: vi.fn().mockResolvedValue({ status: "queued" }),
    } as unknown as ApiClient;
    const act = (work: () => Promise<unknown>) => work().then(() => undefined);
    render(<ChatComposer client={client} act={act} session="web:owner" assistantName="小知" />);
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/chat-render.test.tsx`
Expected: FAIL — 找不到 `../src/components/ChatComposer`。

- [ ] **Step 3: Extract `web/src/components/ChatComposer.tsx`**

把 `ChatPage` 现有的 `<form className="composer">` 及其状态/逻辑整体搬进来：`text` 状态、`commands` 查询（`queryKey: ["commands"]`）、`menuOpen`/`matches` 计算、`send`（`act(async () => { await client.sendMessage(message, session); setText(""); }, "")`）、停止按钮（发 `/stop`）。保留 aria-label `消息`、`发送消息`、`停止当前任务` 和 placeholder 文案。

- [ ] **Step 4: Use it from `ChatPage`**

删掉 `ChatPage` 里的 `text`/`commands` 状态与 composer 表单，替换为：

```tsx
{session.startsWith("web:") ? (
  <ChatComposer client={client} act={act} session={session} assistantName={assistantName} />
) : (
  <p className="muted">这里只发送 WebUI 会话。QQ 会话请直接在 QQ 里对话。</p>
)}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx vitest run tests/chat-render.test.tsx tests/console.test.tsx`
Expected: PASS（含原有「sends a chat message from the composer」）。

- [ ] **Step 6: Checkpoint**

Run: `git status --short web/` 并汇报。不要提交。

---

### Task 5: 会话侧栏

**Files:**
- Create: `web/src/components/ChatSidebar.tsx`
- Test: `web/tests/chat-sidebar.test.tsx`

**Interfaces:**
- Consumes: `SessionGroup`、`relativeTime`（Task 1）。
- Produces:

```ts
export interface ChatSidebarProps {
  groups: SessionGroup[];
  activity: Record<string, number>;
  selected: string;
  running: Set<string>;
  pending: Set<string>;
  onSelect: (id: string) => void;
  onNew: () => void;
}
```

- [ ] **Step 1: Write the failing test**

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatSidebar } from "../src/components/ChatSidebar";
import { sessionGroups } from "../src/lib/sessions";

afterEach(cleanup);

const groups = sessionGroups(
  ["web:owner", "private:qq:2064438401", "group:qq:820215017"],
  {},
);

describe("ChatSidebar", () => {
  it("renders groups, markers and reports selection", async () => {
    const onSelect = vi.fn();
    const onNew = vi.fn();
    render(
      <ChatSidebar
        groups={groups}
        activity={{}}
        selected="web:owner"
        running={new Set(["private:qq:2064438401"])}
        pending={new Set(["group:qq:820215017"])}
        onSelect={onSelect}
        onNew={onNew}
      />,
    );
    expect(screen.getByText("本机")).toBeTruthy();
    expect(screen.getByText("QQ 私聊")).toBeTruthy();
    expect(screen.queryByText("后台任务")).toBeNull();
    const active = screen.getByRole("button", { name: /默认对话/ });
    expect(active.getAttribute("aria-current")).toBe("page");
    expect(
      screen
        .getByRole("button", { name: /QQ 2064438401/ })
        .querySelector(".session-dot"),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: /群 820215017/ })
        .querySelector(".session-badge"),
    ).toBeTruthy();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /群 820215017/ }));
    expect(onSelect).toHaveBeenCalledWith("group:qq:820215017");
    await user.click(screen.getByRole("button", { name: /新对话/ }));
    expect(onNew).toHaveBeenCalled();
  });

  it("shows group counts", () => {
    render(
      <ChatSidebar
        groups={sessionGroups(["web:owner", "web:1762321920000"], {})}
        activity={{}}
        selected="web:owner"
        running={new Set()}
        pending={new Set()}
        onSelect={() => {}}
        onNew={() => {}}
      />,
    );
    expect(screen.getByText("2")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/chat-sidebar.test.tsx`
Expected: FAIL — 找不到组件。

- [ ] **Step 3: Implement `web/src/components/ChatSidebar.tsx`**

- 顶部：品牌块（`ShiritaiBot` + 小字「对话」），按钮 `+ 新对话`（`Plus` 图标，`aria-label` 含「新对话」）。
- 每组：`<div className="chat-group"><h2>{label}<span className="chat-group-count">{items.length}</span></h2>`；组内每个会话是 `<button className={"chat-session" + (active ? " active" : "")} aria-current={active ? "page" : undefined}>`，内容：标题、`<small>{relativeTime(activity[id] ?? 0)}</small>`、运行中时 `<span className="session-dot" aria-label="运行中" />`、待审批时 `<span className="session-badge" aria-label="待审批">{…}</span>`（显示 `!`）。
- 不渲染空组（`groups` 已过滤）。

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/chat-sidebar.test.tsx`
Expected: PASS。

- [ ] **Step 5: Checkpoint**

Run: `git status --short web/` 并汇报。不要提交。

---

### Task 6: 对话模式工作区

**Files:**
- Create: `web/src/components/ChatWorkspace.tsx`
- Create: `web/tests/chat-helpers.tsx`
- Test: `web/tests/chat-workspace.test.tsx`

**Interfaces:**
- Consumes: `ChatSidebar`（Task 5）、`ChatComposer`（Task 4）、`AgentTimeline`（Task 3）、`ApprovalCard`、`EmptyState`、`sessionGroups` / `sessionActivity` / `runningSessions` / `latestTurnStatus` / `sessionTitle`（Task 1）。
- Produces:

```ts
export interface ChatWorkspaceProps {
  client: ApiClient;
  act: Action;
  assistantName: string;
  session: string;
  onSelectSession: (id: string) => void;
  onCreateSession: () => void;
  draftSessions: string[];
  onOpenSettings: () => void;
}
```

- [ ] **Step 1: Create `web/tests/chat-helpers.tsx`**

```tsx
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ApiClient } from "../src/api/client";
import type { Action } from "../src/pages/common";

export function fakeChatClient(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    records: vi.fn().mockResolvedValue([]),
    history: vi.fn().mockResolvedValue([]),
    commands: vi.fn().mockResolvedValue([]),
    sendMessage: vi.fn().mockResolvedValue({ status: "queued" }),
    resolveApproval: vi.fn().mockResolvedValue({ ok: true }),
    ...overrides,
  } as unknown as ApiClient;
}

export function withQuery(node: ReactNode) {
  return (
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {node}
    </QueryClientProvider>
  );
}

export const act: Action = (work) => work().then(() => undefined);
```

- [ ] **Step 2: Write the failing test**

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ApprovalRecord, HistoryMessage, SessionRecord, TurnRecord } from "../src/api/types";
import { ChatWorkspace } from "../src/components/ChatWorkspace";
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
    expect(
      screen.getByRole("status", { name: "助手正在回复" }),
    ).toBeTruthy();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "允许这一次" }));
    expect(api.resolveApproval).toHaveBeenCalledWith("a1", true);
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
```

`ChatWorkspaceProps` 与 `ApiClient` 从对应模块 `import type`。运行指示器必须是 `<div className="typing" role="status" aria-label="助手正在回复">`。

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run tests/chat-workspace.test.tsx`
Expected: FAIL — 找不到组件。

- [ ] **Step 4: Implement `web/src/components/ChatWorkspace.tsx`**

- 查询：
  - `sessions`: `records("session")`，`refetchInterval: 10000`
  - `turns`: `records("turn")`，`refetchInterval: 2500`
  - `history`: `history(session)`，`refetchInterval: 2500`
  - `approvals`: `records("approval")`，`refetchInterval: 4000`
- 派生：
  - `ids = Array.from(new Set(["web:owner", ...draftSessions, ...(sessions.data ?? []).map(r => r.id), session]))`
  - `activity = sessionActivity(turns.data ?? [])`、`groups = sessionGroups(ids, activity)`
  - `running = runningSessions(turns.data ?? [])`
  - `pending = new Set((approvals.data ?? []).filter(r => r.status === "pending" && r.reviewer !== "model").map(r => r.scope))`
  - `current = (approvals.data ?? []).filter(r => r.status === "pending" && r.reviewer !== "model" && r.scope === session)`
  - `failed = latestTurnStatus(turns.data ?? [], session) === "failed"`
- 布局：
  - 根 `<div className={"chat-workspace" + (drawerOpen ? " drawer-open" : "")}>`，`drawerOpen` 为本地 state。
  - `<ChatSidebar ... />`
  - `<main className="chat-main">`：`<header className="chat-header">` 内是汉堡按钮（`aria-label="会话列表"`，`Menu` 图标，`className="icon-button chat-menu"`）、`<h1>{sessionTitle(session)}</h1>`、右侧 `<button aria-label="设置" className="icon-button">`（`Settings` 图标 → `onOpenSettings`）。
  - 消息滚动区 `<div className="chat-scroll" ref={scrollRef}>`：
    - `history.isPending` → `<p className="muted">正在读取会话…</p>`
    - 有错误 → `<QueryErrorNotice message={...} />`
    - 空历史 → `<EmptyState text="从一句话开始。请先在「模型与服务」里配置模型路由。" />`
    - `<AgentTimeline history={history.data} assistantName={assistantName} userLabel={session.startsWith("background:") ? "任务" : "你"} failed={failed} />`
    - `current.map(row => <ApprovalCard key={row.id} approval={row} busy={busy} onResolve={(allow) => void act(() => client.resolveApproval(row.id, allow), allow ? "已允许" : "已拒绝")} />)`，`busy` 为本地 state 包住 act。
    - `running.has(session) && <div className="typing" aria-label="助手正在回复"><span /><span /><span /></div>`
  - 底部：`session.startsWith("web:")` → `<ChatComposer ... />`，否则只读提示文案同 ChatPage。
- 自动滚动：`onScroll` 里记录 `stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80`；`useEffect` 依赖 `[history.data, approvals.data, session]`，当 `stickRef.current` 为真时 `el.scrollTop = el.scrollHeight`（不要用 `scrollTo`，jsdom 未实现）。
- 点会话时调用 `onSelectSession(id)`，并 `setDrawerOpen(false)`。

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run tests/chat-workspace.test.tsx`
Expected: PASS。

- [ ] **Step 6: Checkpoint**

Run: `git status --short web/` 并汇报。不要提交。

---

### Task 7: 模式切换与入口

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/AppShell.tsx`
- Create: `web/tests/fixtures.tsx`（把 `console.test.tsx` 的 `baseConfig()` 搬出来）
- Modify: `web/tests/console.test.tsx`（删除本地 `baseConfig`，改为 import）
- Test: `web/tests/chat-workspace.test.tsx`（追加 `App` 的 describe）

**Interfaces:**
- Consumes: `ChatWorkspace`（Task 6）、`MessageSquare`（lucide）。
- Produces:
  - `AppShell` 新 prop `onOpenChat: () => void`，顶栏渲染 `<button className="chat-mode-button" onClick={onOpenChat}><MessageSquare size={14} />对话模式</button>`。
  - `App` 内 `type AppMode = "console" | "chat"`，`localStorage["shiritaibot-mode"]` 持久化。

- [ ] **Step 1: Move `baseConfig()` into a shared fixture**

把 `web/tests/console.test.tsx` 里的 `function baseConfig(): Settings { ... }`（约 39–166 行）整段剪出，粘贴到新建的 `web/tests/fixtures.tsx`，改为 `export function baseConfig(): Settings`，并保留所需的 `import type { Settings } from "../src/api/types";`。在 `console.test.tsx` 顶部加 `import { baseConfig } from "./fixtures";`。

Run: `npx vitest run tests/console.test.tsx`
Expected: PASS（纯搬家，行为不变）。

- [ ] **Step 2: Write the failing test**

在 `web/tests/chat-workspace.test.tsx` 追加：

```tsx
import { App } from "../src/App";
import { baseConfig } from "./fixtures";

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
    expect(await screen.findByRole("heading", { name: /默认对话/ })).toBeTruthy();
    expect(localStorage.getItem("shiritaibot-mode")).toBe("chat");
    first.unmount();

    render(withQuery(<App />));
    expect(await screen.findByRole("heading", { name: /默认对话/ })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(location.hash).toBe("#/settings");
    expect(localStorage.getItem("shiritaibot-mode")).toBe("console");
    expect(await screen.findByRole("heading", { name: "设置" })).toBeTruthy();
    localStorage.removeItem("shiritaibot-mode");
    vi.unstubAllGlobals();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run tests/chat-workspace.test.tsx`
Expected: FAIL — 没有「对话模式」按钮。

- [ ] **Step 4: Implement `AppShell` button**

在 `header` 的右侧按钮组里、「检查更新」之前加：`onOpenChat` prop、`MessageSquare` 图标、按钮文案 `对话模式`，样式类 `chat-mode-button`。

- [ ] **Step 5: Implement mode switching in `web/src/App.tsx`**

- `const MODE_KEY = "shiritaibot-mode";`
- `const [mode, setMode] = useState<"console" | "chat">(() => (localStorage.getItem(MODE_KEY) === "chat" ? "chat" : "console"));`
- `useEffect(() => { localStorage.setItem(MODE_KEY, mode); }, [mode]);`
- `const [session, setSession] = useState("web:owner");`
- `const [drafts, setDrafts] = useState<string[]>([]);`
- `const startConversation = useCallback(() => { const id = "web:" + Date.now(); setDrafts((prev) => [...prev, id]); setSession(id); }, []);`
- 在 `if (!client)` 之后、`content()` 之前：

```tsx
if (mode === "chat") {
  return (
    <ChatWorkspace
      client={client}
      act={act}
      assistantName={draft.config?.assistant_name ?? "ShiritaiBot"}
      session={session}
      onSelectSession={setSession}
      onCreateSession={startConversation}
      draftSessions={drafts}
      onOpenSettings={() => {
        setMode("console");
        navigate("settings");
      }}
    />
  );
}
```

- 给 `<AppShell ... onOpenChat={() => setMode("chat")} />`。
- 所有 hook 必须留在这些提前 return 之前（现有结构已满足，只需把新 state/effect 放到 `if (!client)` 之前）。

- [ ] **Step 6: Run tests to verify they pass**

Run: `npx vitest run tests/chat-workspace.test.tsx tests/console.test.tsx`
Expected: PASS（`console.test.tsx` 的模式默认值必须是 console）。

- [ ] **Step 7: Checkpoint**

Run: `git status --short web/` 并汇报。不要提交。

---

### Task 8: 视觉规范

**Files:**
- Modify: `web/src/styles/tokens.css`
- Modify: `web/src/styles/base.css`
- Create: `web/src/styles/chat.css`
- Modify: `web/src/styles/index.css`（追加 `@import "./chat.css";`）
- Modify: `web/src/styles/pages.css`（替换旧的 `.message` 规则，见下）

**Interfaces:**
- Consumes: Task 3–7 用到的类名：`.chat-workspace`、`.chat-main`、`.chat-header`、`.chat-scroll`、`.chat-sidebar`、`.chat-group`、`.chat-session`、`.session-dot`、`.session-badge`、`.typing`、`.tool-group`、`.tool-names`、`.failed-turn`、`.turn-failed`、`.bubble-user`、`.drawer-open`、`.chat-menu`、`.icon-button`。
- Produces: 无（纯样式）。

- [ ] **Step 1: Update tokens**

`tokens.css` 中改为（其余不动）：

| token | 值 |
|---|---|
| `--accent` | `#4d6bfe` |
| `--accent-strong` | `#3b5bfd` |
| `--accent-soft` | `#eef2ff` |
| `--focus` | `#4d6bfe` |
| `--focus-ring` | `rgba(77, 107, 254, 0.18)` |
| `--success` / `--success-solid` | `#16a34a` |
| `--danger` | `#dc2626` |
| `--bubble-user`（新增） | `#f1f3f7` |
| `--sidebar-active`（新增） | `#eef2ff` |

- [ ] **Step 2: Button and input rules in `base.css`**

- `button { min-height: 34px; border-radius: 8px; }`（保留现有 padding）。
- `button:active:not(:disabled) { filter: brightness(0.94); }`
- 次按钮 hover：`background: var(--accent-soft); border-color: var(--accent); color: var(--accent);`
- `.primary:disabled { opacity: 0.4; }`
- `input/textarea/select`: `border-radius: 8px`；`focus` 时 `border-color: var(--focus); box-shadow: 0 0 0 3px var(--focus-ring);`
- 新增 `.icon-button`：`min-width: 34px; padding: 6px; border: 0; background: transparent; border-radius: 8px;` hover 背景 `var(--accent-soft)`、颜色 `var(--accent)`。
- 链接风格的文字：`a { color: var(--accent); }`，`a:hover { text-decoration: underline; }`。

- [ ] **Step 3: Replace `.message` rules in `pages.css`**

把 131–172 行的 `.message*` 规则替换为：

- `.message`：`margin: 14px 0; max-width: 100%;`（去掉粉色左边框）。
- `.message small`：`display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px;`
- `.message.user`：`margin-left: auto; max-width: 80%; padding: 10px 14px; background: var(--bubble-user); border-radius: 12px;`
- `.message.assistant`：`background: transparent; padding: 0;`，正文 `.assistant-body p` 保持 14px、`white-space: pre-wrap`。
- `.message.failed-turn`：`border-left: 3px solid var(--danger); padding-left: 12px;`，`.turn-failed { color: var(--danger); font-size: 11px; margin-right: 6px; }`
- 删除 `.message.delivered`。

- [ ] **Step 4: Create `web/src/styles/chat.css`**

- `.chat-workspace`：`display: grid; grid-template-columns: 260px 1fr; height: 100vh;`
- `.chat-sidebar`：白底、右侧 1px `var(--border)`、内部纵向 flex、会话列表 `overflow-y: auto`。
- `.chat-group h2`：12px、`letter-spacing` 小、颜色 `var(--muted)`；`.chat-group-count` 右侧灰色小数字。
- `.chat-session`：全宽、左对齐、透明底、`border-left: 3px solid transparent`；`.active` 用 `background: var(--sidebar-active); border-left-color: var(--accent); color: var(--accent);`；hover 用 `var(--accent-soft)`。
- `.session-dot`：8px 圆点、`var(--accent)`、带 1.4s 脉动动画；`.session-badge`：10px、白字、`var(--danger)` 圆角块。
- `.chat-main`：`display: flex; flex-direction: column; min-width: 0;`
- `.chat-header`：高 56px、左右 20px、底边 1px、flex 两端对齐；`h1` 18px。
- `.chat-scroll`：`flex: 1; overflow-y: auto; padding: 24px 20px;`，内部内容 `width: min(100%, 760px); margin: 0 auto;`
- `.tool-group`：浅底 `var(--bg-raised)`、1px `var(--border)`、圆角 10px、padding 8px 12px；`summary` 不换行、`.tool-names` 灰色 12px、`[open] .chevron { transform: rotate(90deg); }`；展开后每行复用现有 `.tool-call`。
- `.typing`：三点 1.2s 依次脉动。
- `.chat-main .composer`：`margin: 0 auto; width: min(100%, 760px);` 且 `position: static`（对话模式里输入框固定在主列底部，外层用 padding 容器）。
- `@media (max-width: 900px)`：
  - `.chat-workspace { grid-template-columns: 1fr; }`
  - `.chat-sidebar { position: fixed; inset: 0 auto 0 0; width: 280px; z-index: 30; transform: translateX(-100%); transition: transform 160ms ease; box-shadow: var(--shadow); }`
  - `.chat-workspace.drawer-open .chat-sidebar { transform: none; }`
  - `.chat-menu { display: inline-flex; }`，桌面端 `.chat-menu { display: none; }`
  - 让 `.chat-workspace` 与 `.chat-main` 都 `overflow-x: hidden;` 保证 390px 无横向滚动。

- [ ] **Step 5: Verify**

Run: `npm test`（全部前端测试）
Expected: PASS。

Run: `npm run build`
Expected: `tsc -b` 与 `vite build` 均成功。

- [ ] **Step 6: Manual visual check (required)**

Run: `npm run dev`，浏览器打开 `http://127.0.0.1:5173`（后端需运行 `shiritaibot run`）：
1. 控制台主按钮/选中项颜色明显比旧版更蓝更亮；hover/焦点态可见。
2. 点「对话模式」→ 左侧四组会话、右侧对话、底部输入。
3. 一次多步工具调用只占一行折叠组，点开看到逐条调用。
4. 宽度拖到 390px：无横向滚动条，汉堡按钮打开/关闭侧栏。
5. 点齿轮回到控制台并停在「设置」页；刷新后仍是控制台（因为切回 console）。

- [ ] **Step 7: Checkpoint**

Run: `git status --short web/` 并汇报。不要提交。

---

## Self-Review Notes

- Spec 覆盖：顺序与去重（Task 3）、折叠（Task 2/3）、对话模式布局（Task 5/6）、四组会话与标题/排序（Task 1/5/6）、内联审批（Task 6）与侧栏标记（Task 5）、设置入口与持久化（Task 6/7）、配色与按钮（Task 8）、响应式（Task 8）。「后台任务默认展开」「不加时间戳」「不做全部展开快捷键」按 Spec 的默认判断保持。
- 类型一致性：`SessionGroup`/`SessionItem`/`ToolItem`/`TimelineEntry`/`TurnRecord` 在任务间名称与字段一致；`records("turn")` 依赖 `RecordMap["turn"]`。
- 已知取舍：失败轮次只标记「该会话最近一轮失败时的最后一条助手消息」，这是现有数据结构下可确定的部分；不引入 turn→message 映射。
