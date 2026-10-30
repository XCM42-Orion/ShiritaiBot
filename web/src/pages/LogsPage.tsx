import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { EmptyState } from "../components/EmptyState";
import { formatTime } from "../lib/utils";
import type { PageProps } from "./common";

const LINE_OPTIONS = [200, 500, 1000, 2000];

function ActivityLog({ client }: Pick<PageProps, "client">) {
  const activity = useQuery({
    queryKey: ["records", "activity"],
    queryFn: () => client.records("activity"),
    refetchInterval: 4000,
  });
  return (
    <>
      {activity.error && (
        <QueryErrorNotice message={`读取日志失败：${activity.error.message}`} />
      )}
      {activity.data?.map((row) => (
        <div className="log-row" key={row.id}>
          <time>{formatTime(row.at)}</time>
          <span className="tag">{row.kind}</span>
          <span>{row.detail}</span>
        </div>
      ))}
      {!activity.data?.length && !activity.error && <EmptyState />}
    </>
  );
}

function RawLog({ client }: Pick<PageProps, "client">) {
  const [lines, setLines] = useState(500);
  const [filter, setFilter] = useState("");
  const [follow, setFollow] = useState(true);
  const viewer = useRef<HTMLPreElement>(null);
  const raw = useQuery({
    queryKey: ["raw-logs", lines, filter],
    queryFn: () => client.rawLogs(lines, filter),
    refetchInterval: follow ? 3000 : false,
  });
  const text = raw.data?.lines.join("\n") ?? "";
  useEffect(() => {
    if (follow && viewer.current) {
      viewer.current.scrollTop = viewer.current.scrollHeight;
    }
  }, [text, follow]);
  return (
    <>
      <div className="inline raw-log-toolbar">
        <input
          aria-label="过滤原始日志"
          placeholder="过滤关键词…"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <select
          aria-label="显示行数"
          value={lines}
          onChange={(event) => setLines(Number(event.target.value))}
        >
          {LINE_OPTIONS.map((value) => (
            <option key={value} value={value}>
              最近 {value} 行
            </option>
          ))}
        </select>
        <label className="raw-log-follow">
          <input
            type="checkbox"
            checked={follow}
            onChange={(event) => setFollow(event.target.checked)}
          />
          自动滚动
        </label>
        <button type="button" onClick={() => void raw.refetch()}>
          刷新
        </button>
      </div>
      {raw.error && (
        <QueryErrorNotice message={`读取原始日志失败：${raw.error.message}`} />
      )}
      {text ? (
        <pre className="raw-log" ref={viewer} aria-label="原始日志">
          {text}
        </pre>
      ) : (
        !raw.error && <EmptyState text="暂无日志输出。" />
      )}
      <p className="muted raw-log-hint">
        日志文件：data/logs/shiritaibot.log（单文件 5 MB 滚动，保留 3 份）
      </p>
    </>
  );
}

export function LogsPage({ client }: PageProps) {
  const [tab, setTab] = useState<"activity" | "raw">("activity");
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{tab === "activity" ? "活动日志" : "原始滚动日志"}</h2>
        <div className="range-tabs" role="group" aria-label="日志类型">
          {(
            [
              ["activity", "活动日志"],
              ["raw", "原始日志"],
            ] as const
          ).map(([value, label]) => (
            <button
              type="button"
              key={value}
              className={tab === value ? "selected" : ""}
              aria-pressed={tab === value}
              onClick={() => setTab(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {tab === "activity" ? (
        <ActivityLog client={client} />
      ) : (
        <RawLog client={client} />
      )}
    </section>
  );
}
