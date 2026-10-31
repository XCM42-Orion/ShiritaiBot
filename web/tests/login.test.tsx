import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "../src/App";

const TOKEN_KEY = "shiritaibot-token";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

function renderApp() {
  vi.stubGlobal("scrollTo", vi.fn());
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>
    </StrictMode>,
  );
}

describe("startup login link", () => {
  it("validates and clears the link token, without logging in again after logout", async () => {
    sessionStorage.setItem(TOKEN_KEY, "stale-token");
    history.replaceState(null, "", "/token#token=local-test-token");
    const requests = vi.fn(async (url: string, init: RequestInit) => {
      expect(init.headers).toEqual({
        Authorization: "Bearer local-test-token",
      });
      expect(url).not.toContain("local-test-token");
      if (url === "/api/config") return new Promise<never>(() => {});
      const data =
        url === "/api/health" ? { status: "ok", version: "0.1.0" } : [];
      return { ok: true, status: 200, json: async () => data };
    });
    vi.stubGlobal("fetch", requests);
    vi.stubGlobal(
      "WebSocket",
      class {
        send() {}
        close() {}
      },
    );

    renderApp();
    expect(location.pathname).toBe("/");
    expect(location.hash).toBe("");
    await screen.findByText("本机控制台");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe("local-test-token");
    expect(screen.queryByLabelText("管理令牌")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "退出" }));
    expect(await screen.findByLabelText("管理令牌")).toBeTruthy();
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("rejects a bad link without saving it or falling back to an old session", async () => {
    sessionStorage.setItem(TOKEN_KEY, "stale-token");
    history.replaceState(null, "", "/token#token=wrong-token");
    const requests = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "管理令牌无效" }),
    });
    vi.stubGlobal("fetch", requests);

    renderApp();
    expect((await screen.findByRole("alert")).textContent).toBe("管理令牌无效");
    await waitFor(() => {
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "进入控制台" })
          .disabled,
      ).toBe(false);
    });
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(location.pathname).toBe("/");
    expect(location.hash).toBe("");
    expect(requests.mock.calls.every(([url]) => url === "/api/health")).toBe(
      true,
    );
  });

  it("keeps manual login available when /token has no credential", () => {
    history.replaceState(null, "", "/token");
    const requests = vi.fn();
    vi.stubGlobal("fetch", requests);

    renderApp();
    expect(screen.getByLabelText("管理令牌")).toBeTruthy();
    expect(requests).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});
