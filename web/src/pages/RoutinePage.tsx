import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import type { RoutineKind } from "../api/types";
import { formatDateTime, localInputToIso } from "../lib/utils";
import type { PageProps } from "./common";

const KINDS: { value: RoutineKind; label: string }[] = [
  { value: "todo", label: "待办" },
  { value: "event", label: "日程" },
  { value: "reminder", label: "提醒" },
  { value: "job", label: "定时 Agent 任务" },
];

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
      <section className="panel">
        <h2>日程与任务</h2>
        {routines.error && (
          <p className="error-text">读取日程失败：{routines.error.message}</p>
        )}
        {routines.data?.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <strong>{row.title}</strong>
              <span className="tag">
                {row.kind} · {row.status}
              </span>
            </div>
            <p className="muted">
              {row.due ? formatDateTime(row.due) : row.cron || "没有截止时间"}
            </p>
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
          </article>
        ))}
        {!routines.data?.length && !routines.error && (
          <EmptyState text="还没有日程或任务。" />
        )}
      </section>
    </>
  );
}
