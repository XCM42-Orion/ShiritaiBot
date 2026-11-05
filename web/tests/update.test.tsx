import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "../src/App";
import { compareVersions, isNewer } from "../src/lib/version";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

const RELEASES = [
  {
    tag: "v1.3.0-beta.1",
    name: "v1.3.0-beta.1",
    body: "## Beta\n- risky change",
    prerelease: true,
    published_at: "2026-10-20T00:00:00Z",
    url: null,
  },
  {
    tag: "v1.2.0",
    name: "ShiritaiBot 1.2",
    body: "## 新功能\n- 支持 **自动更新**\n- 修复 `bug`\n\n<script>alert(1)</script>",
    prerelease: false,
    published_at: "2026-10-10T00:00:00Z",
    url: "https://github.com/x/releases/v1.2.0",
  },
  {
    tag: "v1.1.0",
    name: "v1.1.0",
    body: "",
    prerelease: false,
    published_at: "2026-09-01T00:00:00Z",
    url: null,
  },
];

function setup(
  inspection: {
    files: { path: string; kind: "modified" | "removed" }[];
    patch: string | null;
  } = { files: [], patch: null },
) {
  let instance = "before";
  let phase = "idle";
  let current = {
    version: "1.1.0",
    kind: "release",
    ref: "v1.1.0",
    sha: null,
    deployed_at: null,
  };
  const applied: unknown[] = [];
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    let body: unknown = [];
    const parsed = new URL(url, "http://local");
    if (url === "/api/health")
      body = {
        status: "ok",
        version: "0.1.0",
        instance_id: instance,
        restart_available: true,
      };
    if (url === "/api/config") body = { assistant_name: "ShiritaiBot" };
    if (parsed.pathname === "/api/update/status")
      body = {
        phase,
        message: phase === "failed" ? "uv 执行失败" : "正在构建 WebUI",
        target: null,
        output: phase === "idle" ? [] : ["» 正在构建 WebUI"],
        available: true,
        reason: null,
        repository: "ShiritaiBot-Devs/ShiritaiBot",
        current,
      };
    if (parsed.pathname === "/api/update/releases") {
      const beta = parsed.searchParams.get("prerelease") === "true";
      const rows = RELEASES.filter((r) => beta || !r.prerelease);
      const size = Number(parsed.searchParams.get("size"));
      const page = Number(parsed.searchParams.get("page"));
      body = {
        items: rows.slice((page - 1) * size, page * size),
        total: rows.length,
        page,
      };
    }
    if (parsed.pathname === "/api/update/preview")
      body = {
        commit: {
          sha: "f".repeat(40),
          message: "feat: unreleased thing",
          date: "2026-11-01T00:00:00Z",
          url: null,
          base: "v1.3.0-beta.1",
          ahead_by: 1,
          commits: [
            {
              sha: "f".repeat(40),
              message: "feat: unreleased thing",
              author: "dev",
              date: null,
            },
          ],
        },
      };
    if (parsed.pathname === "/api/update/inspect") body = inspection;
    if (parsed.pathname === "/api/update/apply") {
      applied.push(JSON.parse(String(options?.body)));
      phase = "building";
      body = {
        phase,
        message: "正在下载",
        target: null,
        output: [],
        available: true,
        reason: null,
        repository: "x",
        current,
      };
    }
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal(
    "WebSocket",
    class {
      close() {}
    },
  );
  sessionStorage.setItem("shiritaibot-token", "test-token");
  location.hash = "#/chat";
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queries}>
      <App />
    </QueryClientProvider>,
  );
  return {
    applied,
    async set(next: { phase?: string; instance?: string; ref?: string }) {
      phase = next.phase ?? phase;
      instance = next.instance ?? instance;
      if (next.ref) current = { ...current, ref: next.ref };
      await act(async () => {
        await queries.invalidateQueries();
      });
    },
  };
}

it("compares versions including prereleases", () => {
  expect(compareVersions("v1.2.0", "1.10.0")).toBe(-1);
  expect(compareVersions("v1.2.0-beta.2", "v1.2.0-beta.10")).toBe(-1);
  expect(compareVersions("v1.2.0-beta", "v1.2.0")).toBe(-1);
  expect(compareVersions("main", "v1")).toBeNull();
  const current = {
    version: "0.1.0",
    kind: null,
    ref: null,
    sha: null,
    deployed_at: null,
  };
  expect(isNewer(RELEASES[1], current)).toBe(true);
  expect(
    isNewer(RELEASES[1], { ...current, kind: "release", ref: "v1.2.0" }),
  ).toBe(false);
});

it("lists stable releases, shows notes, and toggles beta and unreleased commits", async () => {
  const backend = setup();
  const open = await screen.findByRole("button", { name: /检查更新/ });
  await waitFor(() => expect(screen.getByLabelText("有新版本")).toBeTruthy());
  fireEvent.click(open);
  const dialog = await screen.findByRole("dialog", { name: "检查更新" });
  const versions = within(dialog).getByRole("listbox", { name: "版本" });
  await within(versions).findByText("v1.2.0");
  expect(within(versions).queryByText("v1.3.0-beta.1")).toBeNull();
  expect(within(versions).getByText("当前")).toBeTruthy();
  expect(within(versions).getByText("新版本")).toBeTruthy();
  const notes = within(dialog).getByRole("region", { name: "更新日志" });
  expect(within(notes).getByText("自动更新").tagName).toBe("STRONG");
  expect(within(notes).getByText("bug").tagName).toBe("CODE");
  expect(notes.querySelector("script")).toBeNull();

  fireEvent.click(within(dialog).getByLabelText("显示测试版"));
  await within(versions).findByText("v1.3.0-beta.1");
  expect(within(versions).getAllByText("测试版").length).toBe(1);

  fireEvent.click(within(dialog).getByLabelText("显示未发布的最新提交"));
  await within(versions).findByText("开发版 fffffff");
  expect(
    await within(notes).findByText("相对 v1.3.0-beta.1 新增 1 个提交"),
  ).toBeTruthy();

  fireEvent.click(within(versions).getByText("v1.1.0"));
  expect(
    (
      within(dialog).getByRole("button", {
        name: "已是当前版本",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(within(notes).getByText("此版本没有填写更新日志。")).toBeTruthy();
  expect(backend.applied).toEqual([]);
});

it("confirms, tracks progress, waits for restart and reports completion", async () => {
  const backend = setup();
  fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));
  const dialog = await screen.findByRole("dialog", { name: "检查更新" });
  fireEvent.click(await within(dialog).findByText("v1.2.0"));
  fireEvent.click(
    within(dialog).getByRole("button", { name: "切换到 v1.2.0" }),
  );
  fireEvent.click(
    await within(dialog).findByRole("button", { name: "确认切换" }),
  );
  await within(dialog).findByText("构建 WebUI");
  expect(backend.applied).toEqual([{ kind: "release", ref: "v1.2.0" }]);
  expect(
    (within(dialog).getByRole("button", { name: "关闭" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await backend.set({ phase: "restarting" });
  await screen.findByRole("button", { name: "正在重启" });
  await backend.set({ phase: "idle", instance: "after", ref: "v1.2.0" });
  expect(await within(dialog).findByText("更新完成")).toBeTruthy();
  expect(within(dialog).getByText("当前版本 v1.2.0")).toBeTruthy();
});

it("warns about local edits before overwriting them", async () => {
  const backend = setup({
    files: [
      { path: "src/shiritaibot/adapters/onebot11.py", kind: "modified" },
      { path: "local-notes.txt", kind: "removed" },
    ],
    patch: "data/patches/local-20261105-120000.patch",
  });
  fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));
  const dialog = await screen.findByRole("dialog", { name: "检查更新" });
  fireEvent.click(await within(dialog).findByText("v1.2.0"));
  fireEvent.click(
    within(dialog).getByRole("button", { name: "切换到 v1.2.0" }),
  );
  expect(await within(dialog).findByText(/本地改过的文件/)).toBeTruthy();
  expect(
    within(dialog).getByText("src/shiritaibot/adapters/onebot11.py"),
  ).toBeTruthy();
  expect(within(dialog).getByText("local-notes.txt")).toBeTruthy();
  expect(
    within(dialog).getByText("data/patches/local-20261105-120000.patch"),
  ).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "仍然更新" }));
  await waitFor(() =>
    expect(backend.applied).toEqual([{ kind: "release", ref: "v1.2.0" }]),
  );
});

it("shows a failed update and returns to the version list", async () => {
  const backend = setup();
  fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));
  const dialog = await screen.findByRole("dialog", { name: "检查更新" });
  await backend.set({ phase: "failed" });
  expect(await within(dialog).findByText("更新失败")).toBeTruthy();
  expect(within(dialog).getByRole("alert").textContent).toBe("uv 执行失败");
  fireEvent.click(within(dialog).getByRole("button", { name: "返回版本列表" }));
  await within(dialog).findByRole("listbox", { name: "版本" });
  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
