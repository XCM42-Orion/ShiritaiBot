export type SendMode = "steer" | "queue";

export interface ComposerOption<T extends string> {
  value: T;
  label: string;
  hint: string;
}

export const SEND_MODES: ComposerOption<SendMode>[] = [
  { value: "steer", label: "插话", hint: "执行中的任务直接把这条交给模型" },
  { value: "queue", label: "排队", hint: "等这一轮结束后再按顺序发送" },
];

export type Preset = "all_manual" | "safe_manual" | "safe_auto" | "full_access";

export const PRESETS: ComposerOption<Preset>[] = [
  { value: "all_manual", label: "全部人工", hint: "一切工具都需要人工审批" },
  {
    value: "safe_manual",
    label: "安全外人工",
    hint: "安全工具免审，其他人工审批",
  },
  {
    value: "safe_auto",
    label: "安全外自动",
    hint: "安全工具免审，其他由模型审批",
  },
  { value: "full_access", label: "完全放行", hint: "所有工具无需审批" },
];

const DRAFT_PREFIX = "shiritaibot-draft:";
const MODEL_PREFIX = "shiritaibot-model:";
const SEND_MODE_KEY = "shiritaibot-send-mode";

export function draftKey(session: string): string {
  return DRAFT_PREFIX + session;
}

export function modelKey(session: string): string {
  return MODEL_PREFIX + session;
}

export function readValue(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function writeValue(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Private browsing or blocked storage keeps the draft in memory only.
  }
}

export function readSendMode(): SendMode {
  return readValue(SEND_MODE_KEY) === "queue" ? "queue" : "steer";
}

export function writeSendMode(mode: SendMode): void {
  writeValue(SEND_MODE_KEY, mode === "queue" ? "queue" : "");
}

export function composerHeight(scrollHeight: number, max = 240): number {
  return Math.max(88, Math.min(scrollHeight, max));
}

export function stepIndex(
  current: number,
  delta: number,
  length: number,
): number {
  if (length <= 0) return -1;
  return (current + delta + length) % length;
}
