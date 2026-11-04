import type { TurnRecord } from "../api/types";
import { TIME_ZONE } from "./utils";

export type SessionGroupKey = "local" | "private" | "group" | "background";

export interface SessionItem {
  id: string;
  title: string;
}

export interface SessionGroup {
  key: SessionGroupKey;
  label: string;
  items: SessionItem[];
}

const GROUP_ORDER: SessionGroupKey[] = ["local", "private", "group", "background"];

const GROUP_LABELS: Record<SessionGroupKey, string> = {
  local: "本机",
  private: "QQ 私聊",
  group: "QQ 群聊",
  background: "后台任务",
};

export function sessionRole(id: string): SessionGroupKey {
  if (id.startsWith("private:")) return "private";
  if (id.startsWith("group:")) return "group";
  if (id.startsWith("background:")) return "background";
  return "local";
}

function clock(timestamp: number): string {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(timestamp));
  const at = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${at("month")}-${at("day")} ${at("hour")}:${at("minute")}`;
}

function day(timestamp: number): string {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(timestamp));
  const at = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${at("month")}-${at("day")}`;
}

export function sessionTitle(id: string): string {
  if (id === "web:owner") return "默认对话";
  if (id.startsWith("web:")) {
    const rest = id.slice(4);
    if (/^\d+$/.test(rest)) return `对话 ${clock(Number(rest))}`;
    return `对话 ${rest}`;
  }
  const private_ = /^private:[^:]+:([^:]+)$/.exec(id);
  if (private_) return `QQ ${private_[1]}`;
  const group = /^group:[^:]+:([^:]+)$/.exec(id);
  if (group) return `群 ${group[1]}`;
  const background = /^background:proactive:([^:]+)$/.exec(id);
  if (background) return `后台任务 ${background[1].slice(0, 6)}`;
  return id;
}

export function relativeTime(at: number, now: number = Date.now()): string {
  if (!at) return "";
  const elapsed = now - at;
  if (elapsed < 60_000) return "刚刚";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} 分钟前`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} 小时前`;
  return day(at);
}

function turnTime(turn: TurnRecord): number {
  const parsed = Date.parse(turn.started ?? turn.finished ?? "");
  return Number.isNaN(parsed) ? 0 : parsed;
}

export function sessionActivity(turns: TurnRecord[]): Record<string, number> {
  const activity: Record<string, number> = {};
  for (const turn of turns) {
    if (!turn.scope) continue;
    const time = turnTime(turn);
    if (!(turn.scope in activity) || time > activity[turn.scope]) {
      activity[turn.scope] = time;
    }
  }
  return activity;
}

export function runningSessions(turns: TurnRecord[]): Set<string> {
  const running = new Set<string>();
  for (const turn of turns) {
    if (turn.scope && turn.status === "running") running.add(turn.scope);
  }
  return running;
}

export function latestTurnStatus(
  turns: TurnRecord[],
  session: string,
): string | null {
  let latest: TurnRecord | null = null;
  for (const turn of turns) {
    if (turn.scope !== session) continue;
    if (!latest || turnTime(turn) >= turnTime(latest)) latest = turn;
  }
  return latest ? latest.status : null;
}

export function sessionGroups(
  ids: string[],
  activity: Record<string, number>,
): SessionGroup[] {
  const buckets = new Map<SessionGroupKey, SessionItem[]>();
  for (const id of ids) {
    const key = sessionRole(id);
    const items = buckets.get(key) ?? [];
    items.push({ id, title: sessionTitle(id) });
    buckets.set(key, items);
  }
  const groups: SessionGroup[] = [];
  for (const key of GROUP_ORDER) {
    const items = buckets.get(key);
    if (!items || items.length === 0) continue;
    items.sort((a, b) => {
      const diff = (activity[b.id] ?? 0) - (activity[a.id] ?? 0);
      return diff !== 0 ? diff : a.id.localeCompare(b.id);
    });
    groups.push({ key, label: GROUP_LABELS[key], items });
  }
  return groups;
}
