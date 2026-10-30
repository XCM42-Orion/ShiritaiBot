import type {
  CommandInfo,
  ConfigSaveResult,
  DiscoveredModel,
  Health,
  HistoryMessage,
  LogFeed,
  MemoryRecord,
  ModelConfig,
  ModelTestResult,
  PluginInfo,
  ProviderConfig,
  RecordKind,
  RecordMap,
  RoutineDraft,
  RoutineRecord,
  Settings,
  Statistics,
  ToolCatalog,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface LogFilter {
  limit: number;
  level: string;
  source: string;
  query: string;
}

export type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";

export interface ApiClient {
  health(): Promise<Health>;
  config(): Promise<Settings>;
  tools(session?: string): Promise<ToolCatalog>;
  saveConfig(settings: Settings): Promise<ConfigSaveResult>;
  records<K extends RecordKind>(kind: K): Promise<RecordMap[K][]>;
  stats(hours: number): Promise<Statistics>;
  commands(): Promise<CommandInfo[]>;
  history(session: string): Promise<HistoryMessage[]>;
  rawLogs(lines: number, query: string): Promise<{ lines: string[] }>;
  logs(filter: LogFilter): Promise<LogFeed>;
  sendMessage(text: string, session: string): Promise<unknown>;
  resolveApproval(id: string, allow: boolean): Promise<unknown>;
  memories(): Promise<MemoryRecord[]>;
  addMemory(content: string): Promise<unknown>;
  archiveMemory(id: string): Promise<unknown>;
  createRoutine(draft: RoutineDraft): Promise<RoutineRecord>;
  setRoutineStatus(id: string, status: string): Promise<RoutineRecord>;
  cancelSubagent(id: string): Promise<unknown>;
  detectSubagent(id: string): Promise<unknown>;
  plugins(): Promise<PluginInfo[]>;
  reloadPlugins(): Promise<PluginInfo[]>;
  reloadPlugin(id: string): Promise<PluginInfo[]>;
  installPluginZip(id: string, file: File): Promise<PluginInfo[]>;
  installPluginGithub(id: string, url: string): Promise<PluginInfo[]>;
  removePlugin(id: string): Promise<PluginInfo[]>;
  discoverModels(provider: ProviderConfig): Promise<DiscoveredModel[]>;
  testModel(
    provider: ProviderConfig,
    model: ModelConfig,
  ): Promise<ModelTestResult>;
}

function messageFor(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (status === 401) return "管理令牌无效或已过期，请重新登录。";
  return `请求失败（HTTP ${status}），请检查后端服务。`;
}

export function createClient(getToken: () => string): ApiClient {
  async function request<T>(
    path: string,
    method: HttpMethod = "GET",
    body?: unknown,
  ): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: "Bearer " + getToken(),
    };
    const binary = body instanceof Blob;
    if (body !== undefined)
      headers["Content-Type"] = binary ? "application/zip" : "application/json";
    let response: Response;
    try {
      response = await fetch("/api" + path, {
        method,
        headers,
        body:
          body === undefined ? undefined : binary ? body : JSON.stringify(body),
      });
    } catch {
      throw new ApiError("无法连接后端服务，请确认进程仍在运行。", 0);
    }
    if (response.ok) {
      if (response.status === 204) return undefined as T;
      return (await response.json()) as T;
    }
    const payload = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new ApiError(
      messageFor(response.status, payload?.detail),
      response.status,
    );
  }

  return {
    health: () => request<Health>("/health"),
    config: () => request<Settings>("/config"),
    tools: (session = "web:owner") =>
      request<ToolCatalog>("/tools?session=" + encodeURIComponent(session)),
    saveConfig: (settings) =>
      request<ConfigSaveResult>("/config", "PUT", settings),
    records: <K extends RecordKind>(kind: K) =>
      request<RecordMap[K][]>("/records/" + kind),
    stats: (hours) => request<Statistics>("/stats?hours=" + hours),
    commands: () => request<CommandInfo[]>("/commands"),
    history: (session) =>
      request<HistoryMessage[]>(
        "/history?session=" + encodeURIComponent(session),
      ),
    rawLogs: (lines, query) =>
      request<{ lines: string[] }>(
        "/logs/raw?lines=" + lines + "&q=" + encodeURIComponent(query),
      ),
    logs: ({ limit, level, source, query }) =>
      request<LogFeed>(
        "/logs?" +
          new URLSearchParams({
            limit: String(limit),
            level,
            source,
            q: query,
          }).toString(),
      ),
    sendMessage: (text, session) =>
      request("/messages", "POST", { text, session }),
    resolveApproval: (id, allow) =>
      request("/approvals/" + encodeURIComponent(id), "POST", { allow }),
    memories: () => request<MemoryRecord[]>("/memory"),
    addMemory: (content) => request("/memory", "POST", { content }),
    archiveMemory: (id) =>
      request("/memory/" + encodeURIComponent(id) + "/archive", "POST"),
    createRoutine: (draft) =>
      request<RoutineRecord>("/routines", "POST", draft),
    setRoutineStatus: (id, status) =>
      request<RoutineRecord>(
        "/routines/" + encodeURIComponent(id) + "/" + status,
        "POST",
      ),
    cancelSubagent: (id) =>
      request("/subagents/" + encodeURIComponent(id) + "/cancel", "POST"),
    detectSubagent: (id) =>
      request("/subagents/" + encodeURIComponent(id) + "/detect", "POST"),
    plugins: () => request<PluginInfo[]>("/plugins"),
    reloadPlugins: () => request<PluginInfo[]>("/plugins/reload", "POST"),
    reloadPlugin: (id) =>
      request<PluginInfo[]>(
        "/plugins/" + encodeURIComponent(id) + "/reload",
        "POST",
      ),
    installPluginZip: (id, file) =>
      request<PluginInfo[]>(
        "/plugins/install/zip/" + encodeURIComponent(id),
        "POST",
        file,
      ),
    installPluginGithub: (id, url) =>
      request<PluginInfo[]>("/plugins/install/github", "POST", { id, url }),
    removePlugin: (id) =>
      request<PluginInfo[]>("/plugins/" + encodeURIComponent(id), "DELETE"),
    discoverModels: (provider) =>
      request<DiscoveredModel[]>("/providers/discover", "POST", provider),
    testModel: (provider, model) =>
      request<ModelTestResult>("/models/test", "POST", { provider, model }),
  };
}
