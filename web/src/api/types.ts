// Domain types mirroring the FastAPI/Pydantic models in src/shiritaibot.
// Keep this file in sync with config/__init__.py, core/__init__.py and web/__init__.py.

export type Protocol =
  "chat_completions" | "responses" | "anthropic" | "gemini";
export type Permission = "read_only" | "edit";
export type SubagentKind = "codex" | "claude_code" | "generic";
export type RoutineKind = "todo" | "event" | "reminder" | "job";
export type Urgency = "low" | "normal" | "high" | "critical";

export interface Instance {
  id: string;
  enabled: boolean;
}

export interface ProviderConfig extends Instance {
  protocol: Protocol;
  base_url: string;
  api_key: string;
  timeout: number;
  max_retries: number;
  proxy: string;
}

export interface ModelConfig extends Instance {
  provider: string;
  model: string;
  max_output: number;
  max_context: number;
  modalities: ("text" | "image" | "audio" | "video")[];
  // API accepts an object or JSON editor text and returns a normalized object.
  extra_body: Record<string, unknown> | string;
}

export interface AdapterConfig extends Instance {
  url: string;
  token: string;
  self_id: string;
  owner_id: string;
  groups: string[];
  friends: string[];
  ignore_users: string[];
}

export interface SubagentConfig extends Instance {
  kind: SubagentKind;
  command: string[];
  permission: Permission;
  timeout: number;
  model: string;
}

export interface PluginConfig extends Instance {
  settings: Record<string, unknown>;
}

export interface Policy {
  denied_tools: string[];
  scope_tools: Record<string, string[]>;
  approval_timeout: number;
  shell_enabled: boolean;
  shell_command: string[];
  max_steps: number;
  max_turn_seconds: number;
  max_context_chars: number;
}

export interface ProactiveConfig {
  enabled: boolean;
  timezone: string;
  quiet_start: string;
  quiet_end: string;
  throttle_seconds: number;
}

export interface Settings {
  providers: ProviderConfig[];
  models: ModelConfig[];
  routes: Record<string, string[]>;
  adapters: AdapterConfig[];
  subagents: SubagentConfig[];
  plugins: PluginConfig[];
  policy: Policy;
  proactive: ProactiveConfig;
  tavily_key: string;
  concurrency: number;
  persona: string;
}

export interface Health {
  status: string;
  version: string;
  config_version: number;
  adapters: Record<string, string>;
  plugins: string[];
  tools: string[];
  actors: number;
  subagents: number;
}

export interface CommandInfo {
  name: string;
  description: string;
}

export interface DiscoveredModel {
  id: string;
  name: string;
  max_output: number;
}

export interface ModelTestResult {
  text: string;
  usage: Record<string, number>;
}

export interface TokenBucket {
  hour: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  llm_calls: number;
  incoming_messages: number;
  outgoing_messages: number;
  messages: number;
  failed_turns: number;
}

export interface Statistics {
  hours: number;
  timezone: string;
  buckets: TokenBucket[];
  start: string;
  end: string;
  totals: Omit<TokenBucket, "hour">;
}

export interface ExternalEvent {
  source: string;
  kind: string;
  dedup_key: string;
  title: string;
  payload: Record<string, unknown>;
  urgency: Urgency;
  occurred_at: string;
}

export interface TriageDecision {
  decision: "notify" | "digest" | "log" | "ignore";
  message: string;
  reason: string;
  urgency: Urgency;
}

export interface ActivityRecord {
  id: string;
  scope: string;
  kind: string;
  detail: string;
  at: string;
}

export type ApprovalStatus =
  "pending" | "approved" | "denied" | "expired" | "cancelled";

export interface ApprovalRecord {
  id: string;
  scope: string;
  tool: string;
  args: Record<string, unknown>;
  hash: string;
  status: ApprovalStatus;
  expires: number;
  turn: string;
}

export interface EventRecord {
  id: string;
  scope?: string;
  status: string;
  event: ExternalEvent;
  decision?: TriageDecision;
  processed_at?: string;
}

export interface RoutineRecord {
  id: string;
  scope?: string;
  kind: RoutineKind;
  title: string;
  due: string | null;
  end: string | null;
  cron: string;
  timezone: string;
  status: string;
  next_fire: number | null;
}

export interface SubagentRunRecord {
  id: string;
  scope?: string;
  instance: string;
  status: string;
  workspace: string;
  result?: string;
  error?: string;
  exit_code?: number | null;
  native_session_id?: string | null;
  truncated?: boolean;
}

export interface ReplyRecord {
  id: string;
  scope: string;
  text: string;
  at: string;
}

export interface SessionRecord {
  id: string;
  scope?: string;
  mode: string;
  actor: string;
}

export interface ToolCall {
  id: string;
  type?: string;
  function: { name: string; arguments: string };
}

export interface HistoryMessage {
  role: "user" | "assistant" | "tool";
  content?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

export interface MemoryRecord {
  id: string;
  scope?: string;
  visibility: "private" | "public";
  kind: string;
  content: string;
  status: "active" | "archived";
  created: number;
}

export interface RoutineDraft {
  kind: RoutineKind;
  title: string;
  due: string | null;
  cron: string;
  timezone?: string;
}

export interface ConfigSaveResult {
  ok: boolean;
  version: number;
}

export interface RecordMap {
  activity: ActivityRecord;
  approval: ApprovalRecord;
  event: EventRecord;
  routine: RoutineRecord;
  subagent_run: SubagentRunRecord;
  reply: ReplyRecord;
  session: SessionRecord;
}

export type RecordKind = keyof RecordMap;
