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
