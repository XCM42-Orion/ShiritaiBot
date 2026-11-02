import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "../src/App";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

function setup({ available = true, rejectRestart = false } = {}) {
  let instance = "before";
  let offline = false;
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    let body: unknown = [];
    if (url === "/api/health") {
      if (offline) throw new Error("offline");
      body = {
        status: "ok",
        version: "0.1.0",
        instance_id: instance,
        restart_available: available,
      };
    }
    if (url === "/api/config") body = { assistant_name: "ShiritaiBot" };
    if (url === "/api/restart") {
      expect(options?.method).toBe("POST");
      expect(options?.headers).toMatchObject({
        Authorization: "Bearer test-token",
      });
      if (rejectRestart)
        return {
          ok: false,
          status: 503,
          json: async () => ({ detail: "服务暂不支持重启" }),
        };
      body = { status: "restarting", instance_id: instance };
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
    fetchMock,
    async health(nextInstance: string, disconnected = false) {
      instance = nextInstance;
      offline = disconnected;
      await act(async () => {
        await queries.invalidateQueries({ queryKey: ["health"] });
      });
    },
  };
}

it("waits through disconnection until a new process is healthy and keeps the login", async () => {
  const backend = setup();
  const button = await screen.findByRole("button", { name: "重启" });
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  );
  expect(screen.queryByText("我的工作空间")).toBeNull();
  expect(screen.queryByText("个人控制台")).toBeNull();
  expect(screen.getByText("控制台")).toBeTruthy();
  fireEvent.click(button);
  await screen.findByRole("button", { name: "正在重启" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(button);
  expect(
    backend.fetchMock.mock.calls.filter(([url]) => url === "/api/restart"),
  ).toHaveLength(1);
  await backend.health("before");
  expect(screen.queryByText("重启完成")).toBeNull();
  await backend.health("before", true);
  expect(screen.queryByRole("alert")).toBeNull();
  await backend.health("after");
  expect(await screen.findByText("重启完成")).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "重启" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
  expect(sessionStorage.getItem("shiritaibot-token")).toBe("test-token");
});

it("shows a rejected restart and allows retry", async () => {
  setup({ rejectRestart: true });
  const button = await screen.findByRole("button", { name: "重启" });
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  );
  fireEvent.click(button);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "服务暂不支持重启",
  );
  expect((button as HTMLButtonElement).disabled).toBe(false);
});

it("disables restart when the server cannot restart itself", async () => {
  setup({ available: false });
  await screen.findByText("系统运行中");
  const button = screen.getByRole("button", {
    name: "重启",
  }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  expect(button.title).toContain("shiritaibot run");
});
