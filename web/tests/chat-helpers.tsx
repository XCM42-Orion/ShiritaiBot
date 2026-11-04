import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ApiClient } from "../src/api/client";
import type { Action } from "../src/pages/common";
import { baseConfig } from "./fixtures";

export function fakeChatClient(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    records: vi.fn().mockResolvedValue([]),
    history: vi.fn().mockResolvedValue([]),
    commands: vi.fn().mockResolvedValue([]),
    config: vi.fn().mockResolvedValue(baseConfig()),
    tools: vi.fn().mockResolvedValue({
      session: "web:owner",
      policy: { mode: "agent", preset: "safe_manual", tools: {} },
      source: "default",
      tools: [],
    }),
    sendMessage: vi.fn().mockResolvedValue({ status: "queued" }),
    resolveApproval: vi.fn().mockResolvedValue({ ok: true }),
    ...overrides,
  } as unknown as ApiClient;
}

export function withQuery(node: ReactNode) {
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

export const act: Action = (work) => work().then(() => undefined);
