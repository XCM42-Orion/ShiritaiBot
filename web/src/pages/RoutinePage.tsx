import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import type { RoutineKind, RoutineRecord } from "../api/types";
import { TIME_ZONE, formatDateTime, localInputToIso } from "../lib/utils";
import type { PageProps } from "./common";

const KINDS: { value: RoutineKind; label: string }[] = [
  { value: "todo", label: "待办" },
  { value: "event", label: "日程" },
  { value: "reminder", label: "提醒" },
  { value: "job", label: "定时 Agent 任务" },
];

const KIND_LABEL: Record<RoutineKind, string> = {
  todo: "待办",
  event: "日程",
  reminder: "提醒",
  job: "定时任务",
};

const STATUS_LABEL: Record<string, string> = {
  open: "进行中",
  scheduled: "已排期",
  fired: "已触发",
  completed: "已完成",
  cancelled: "已取消",
};

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];
/** Guard against runaway multi-day events. */
const MAX_SPAN_DAYS = 62;

const dayKeyFormat = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
const clockFormat = new Intl.DateTimeFormat("zh-CN", {
  timeZone: TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** "YYYY-MM-DD" of the instant in the dashboard time zone. */
function dayKey(value: string | Date): string | null {
  const date = typeof value === "string" ? new Date(value) : value;
  return Number.isNaN(date.getTime()) ? null : dayKeyFormat.format(date);
}

function clock(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : clockFormat.format(date);
}

/** Calendar arithmetic on date-only keys, done in UTC so DST never bites. */
function keyToUtc(key: string): Date {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function utcToKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function addDays(key: string, days: number): string {
  const date = keyToUtc(key);
  date.setUTCDate(date.getUTCDate() + days);
  return utcToKey(date);
}

function isDone(row: RoutineRecord): boolean {
  return row.status === "completed" || row.status === "cancelled";
}

/** Every day key a routine occupies (events with an end span several days). */
function routineDays(row: RoutineRecord): string[] {
  if (!row.due) return [];
  const start = dayKey(row.due);
  if (!start) return [];
  const end = row.end ? dayKey(row.end) : null;
  const days = [start];
  if (end && end > start) {
    let cursor = start;
    while (cursor < end && days.length < MAX_SPAN_DAYS) {
      cursor = addDays(cursor, 1);
      days.push(cursor);
    }
  }
  return days;
}

/** 6×7 grid of day keys covering the month, weeks starting on Monday. */
function monthGrid(year: number, month: number): string[] {
  const first = new Date(Date.UTC(year, month, 1));
  const offset = (first.getUTCDay() + 6) % 7;
  const start = addDays(utcToKey(first), -offset);
  return Array.from({ length: 42 }, (_, index) => addDays(start, index));
}

function sortByDue(a: RoutineRecord, b: RoutineRecord): number {
  return (a.due ?? "").localeCompare(b.due ?? "");
}

function RoutineItem({
  row,
  client,
  act,
}: { row: RoutineRecord } & Pick<PageProps, "client" | "act">) {
  const timeText = row.due
    ? row.end
      ? `${formatDateTime(row.due)} – ${formatDateTime(row.end)}`
      : formatDateTime(row.due)
    : row.cron
      ? `Cron：${row.cron}${row.next_fire ? ` · 下次 ${formatDateTime(row.next_fire)}` : ""}`
      : "没有截止时间";
  return (
    <article
      className={`routine-item kind-${row.kind}${isDone(row) ? " done" : ""}`}
    >
      <div className="routine-item-main">
        <strong>{row.title}</strong>
        <span className="muted">{timeText}</span>
      </div>
      <span className="tag">
        {KIND_LABEL[row.kind] ?? row.kind} ·{" "}
        {STATUS_LABEL[row.status] ?? row.status}
      </span>
      {!isDone(row) && (
        <div className="actions">
          <button
            type="button"
            onClick={() =>
              void act(
                () => client.setRoutineStatus(row.id, "completed"),
                "已标记完成",
              )
            }
          >
            完成
          </button>
          <button
            type="button"
            onClick={() =>
              void act(
                () => client.setRoutineStatus(row.id, "cancelled"),
                "已取消",
              )
            }
          >
            取消
          </button>
        </div>
      )}
    </article>
  );
}

function RoutineCalendar({
  rows,
  client,
  act,
}: { rows: RoutineRecord[] } & Pick<PageProps, "client" | "act">) {
  const today = dayKey(new Date()) ?? utcToKey(new Date());
  const [cursor, setCursor] = useState(() => {
    const [y, m] = today.split("-").map(Number);
    return { year: y, month: m - 1 };
  });
  const [selected, setSelected] = useState(today);

  const byDay = useMemo(() => {
    const map = new Map<string, RoutineRecord[]>();
    for (const row of rows) {
      for (const key of routineDays(row)) {
        const list = map.get(key);
        if (list) list.push(row);
        else map.set(key, [row]);
      }
    }
    for (const list of map.values()) list.sort(sortByDue);
    return map;
  }, [rows]);

  const undated = useMemo(
    () =>
      rows
        .filter((row) => !row.due)
        .sort((a, b) => Number(isDone(a)) - Number(isDone(b))),
    [rows],
  );

  const grid = monthGrid(cursor.year, cursor.month);
  const monthPrefix = `${cursor.year}-${String(cursor.month + 1).padStart(2, "0")}`;
  const selectedRows = byDay.get(selected) ?? [];

  function shiftMonth(delta: number) {
    setCursor(({ year, month }) => {
      const next = new Date(Date.UTC(year, month + delta, 1));
      return { year: next.getUTCFullYear(), month: next.getUTCMonth() };
    });
  }

  function goToday() {
    const [y, m] = today.split("-").map(Number);
    setCursor({ year: y, month: m - 1 });
    setSelected(today);
  }

  const [selY, selM, selD] = selected.split("-").map(Number);
  const selectedWeekday = WEEKDAYS[(keyToUtc(selected).getUTCDay() + 6) % 7];

  return (
    <>
      <section className="panel">
        <div className="panel-head calendar-head">
          <h2>
            {cursor.year} 年 {cursor.month + 1} 月
          </h2>
          <div className="inline calendar-nav">
            <button
              type="button"
              aria-label="上个月"
              onClick={() => shiftMonth(-1)}
            >
              <ChevronLeft size={16} />
            </button>
            <button type="button" onClick={goToday}>
              今天
            </button>
            <button
              type="button"
              aria-label="下个月"
              onClick={() => shiftMonth(1)}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
        <div className="calendar" role="grid" aria-label="日程日历">
          {WEEKDAYS.map((name) => (
            <div className="calendar-weekday" role="columnheader" key={name}>
              {name}
            </div>
          ))}
          {grid.map((key) => {
            const items = byDay.get(key) ?? [];
            const classes = [
              "calendar-day",
              key.startsWith(monthPrefix) ? "" : "outside",
              key === today ? "today" : "",
              key === selected ? "selected" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <button
                type="button"
                role="gridcell"
                key={key}
                className={classes}
                aria-pressed={key === selected}
                aria-label={`${key}，${items.length} 项`}
                onClick={() => {
                  setSelected(key);
                  if (!key.startsWith(monthPrefix)) {
                    const [y, m] = key.split("-").map(Number);
                    setCursor({ year: y, month: m - 1 });
                  }
                }}
              >
                <span className="calendar-date">{Number(key.slice(8))}</span>
                <span className="calendar-items">
                  {items.slice(0, 3).map((row) => (
                    <span
                      key={row.id}
                      className={`calendar-chip kind-${row.kind}${isDone(row) ? " done" : ""}`}
                      title={row.title}
                    >
                      {row.due && dayKey(row.due) === key && (
                        <span className="calendar-chip-time">
                          {clock(row.due)}
                        </span>
                      )}
                      {row.title}
                    </span>
                  ))}
                  {items.length > 3 && (
                    <span className="calendar-more">+{items.length - 3}</span>
                  )}
                </span>
                {items.length > 0 && (
                  <span className="calendar-dots" aria-hidden="true">
                    {items.slice(0, 4).map((row) => (
                      <i
                        key={row.id}
                        className={`kind-${row.kind}${isDone(row) ? " done" : ""}`}
                      />
                    ))}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <div className="calendar-legend">
          {(["event", "reminder", "todo", "job"] as RoutineKind[]).map(
            (kind) => (
              <span key={kind}>
                <i className={`kind-${kind}`} />
                {KIND_LABEL[kind]}
              </span>
            ),
          )}
        </div>
      </section>
      <section className="panel">
        <h2>
          {selY} 年 {selM} 月 {selD} 日 · 周{selectedWeekday}
        </h2>
        {selectedRows.length ? (
          selectedRows.map((row) => (
            <RoutineItem key={row.id} row={row} client={client} act={act} />
          ))
        ) : (
          <EmptyState text="这一天没有安排。" />
        )}
      </section>
      {undated.length > 0 && (
        <section className="panel">
          <h2>未排期待办与定时任务</h2>
          {undated.map((row) => (
            <RoutineItem key={row.id} row={row} client={client} act={act} />
          ))}
        </section>
      )}
    </>
  );
}

export function RoutinePage({ client, act }: PageProps) {
  const [kind, setKind] = useState<RoutineKind>("todo");
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [cron, setCron] = useState("30 7 * * *");
  const routines = useQuery({
    queryKey: ["records", "routine"],
    queryFn: () => client.records("routine"),
    refetchInterval: 4000,
  });

  function create(event: React.FormEvent) {
    event.preventDefault();
    void act(async () => {
      await client.createRoutine({
        kind,
        title,
        due: localInputToIso(due),
        cron: kind === "job" ? cron : "",
        timezone: "Asia/Shanghai",
      });
      setTitle("");
      setDue("");
    }, "已创建");
  }

  return (
    <>
      <section className="panel">
        <h2>安排下一件事</h2>
        <form onSubmit={create}>
          <div className="field-grid">
            <div className="field">
              <span className="field-name">类型</span>
              <select
                aria-label="类型"
                value={kind}
                onChange={(event) => setKind(event.target.value as RoutineKind)}
              >
                {KINDS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <span className="field-name">内容</span>
              <input
                aria-label="内容"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
              />
            </div>
            {kind === "job" ? (
              <div className="field">
                <span className="field-name">Cron（Asia/Shanghai）</span>
                <input
                  aria-label="Cron"
                  value={cron}
                  onChange={(event) => setCron(event.target.value)}
                />
              </div>
            ) : (
              <div className="field">
                <span className="field-name">时间（浏览器本地时区）</span>
                <input
                  aria-label="时间"
                  type="datetime-local"
                  value={due}
                  onChange={(event) => setDue(event.target.value)}
                  required={kind === "event" || kind === "reminder"}
                />
              </div>
            )}
          </div>
          <button type="submit" className="primary">
            <Plus size={16} />
            创建
          </button>
        </form>
      </section>
      {routines.error && (
        <section className="panel">
          <QueryErrorNotice
            message={`读取日程失败：${routines.error.message}`}
          />
        </section>
      )}
      <RoutineCalendar rows={routines.data ?? []} client={client} act={act} />
    </>
  );
}
