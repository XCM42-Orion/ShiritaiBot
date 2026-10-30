import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { EmptyState } from "../components/EmptyState";
import { formatTime } from "../lib/utils";
import type { PageProps } from "./common";

const LINE_OPTIONS = [200, 500, 1000, 2000];
const LEVEL_OPTIONS = [
  ["", "显示全部"],
  ["log", "只看 LOG 及以上"],
  ["warning", "只看 WARNING 及以上"],
  ["error", "只看 ERROR"],
] as const;
const RECORD_LEVELS = [
  ["debug", "DEBUG（全部细节）"],
  ["info", "LOG（默认）"],
  ["warning", "WARNING"],
  ["error", "ERROR"],
] as const;

function RecordLevel({ client, act }: PageProps) {
  const queryClient = useQueryClient();
  const current = useQuery({
    queryKey: ["log-level"],
    queryFn: () => client.logLevel(),
  });
  return (
    <label
      className="raw-log-follow"
      title="立即生效：终端、日志文件和本页同时按此级别记录"
    >
      记录级别
      <select
        aria-label="记录级别"
        value={current.data?.level ?? "info"}
        disabled={!current.data}
        onChange={(event) => {
          const level = event.target.value;
          void act(async () => {
            queryClient.setQueryData(
              ["log-level"],
              await client.setLogLevel(level),
            );
          }, "日志级别已更新，立即生效");
        }}
      >
        {RECORD_LEVELS.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
    </label>
  );
}

type Tab = "runtime" | "activity" | "raw";

function RunLog({ client, act }: PageProps) {
  const [limit, setLimit] = useState(500);
  const [level, setLevel] = useState("");
  const [source, setSource] = useState("");
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const viewer = useRef<HTMLDivElement>(null);
  const feed = useQuery({
    queryKey: ["logs", limit, level, source, query],
    queryFn: () => client.logs({ limit, level, source, query }),
    refetchInterval: follow ? 2000 : false,
  });
  const entries = feed.data?.entries ?? [];
  const sources = feed.data?.sources ?? [];
  const last = entries.length ? entries[entries.length - 1].at : "";
  useEffect(() => {
    if (follow && viewer.current) {
      viewer.current.scrollTop = viewer.current.scrollHeight;
    }
  }, [last, entries.length, follow]);
  return (
    <>
      <div className="inline raw-log-toolbar">
        <RecordLevel client={client} act={act} />
        <input
          aria-label="过滤运行日志"
          placeholder="过滤关键词…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          aria-label="筛选级别"
          value={level}
          onChange={(event) => setLevel(event.target.value)}
        >
          {LEVEL_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select
          aria-label="日志来源"
          value={source}
          onChange={(event) => setSource(event.target.value)}
        >
          <option value="">全部来源</option>
          {[...new Set([...sources, ...(source ? [source] : [])])].map(
            (name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ),
          )}
        </select>
        <select
          aria-label="显示条数"
          value={limit}
          onChange={(event) => setLimit(Number(event.target.value))}
        >
          {LINE_OPTIONS.map((value) => (
            <option key={value} value={value}>
              最近 {value} 条
            </option>
          ))}
        </select>
        <label className="raw-log-follow">
          <input
            type="checkbox"
            checked={follow}
            onChange={(event) => setFollow(event.target.checked)}
          />
          实时跟随
        </label>
      </div>
      {feed.error && (
        <QueryErrorNotice message={`读取运行日志失败：${feed.error.message}`} />
      )}
      {entries.length ? (
        <div className="run-log" ref={viewer} aria-label="运行日志">
          {entries.map((entry, index) => (
            <div
              className={`run-log-row level-${entry.level.toLowerCase()}`}
              key={`${entry.at}-${index}`}
            >
              <time dateTime={entry.at}>{formatTime(entry.at)}</time>
              <span className="run-log-level">{entry.level}</span>
              <span className="run-log-source">[{entry.source}]</span>
              {entry.tags.map((tag, position) => (
                <span className="run-log-tag" key={position}>
                  [{tag}]
                </span>
              ))}
              <span className="run-log-message">{entry.message}</span>
            </div>
          ))}
        </div>
      ) : (
        !feed.error && <EmptyState text="暂无运行日志。" />
      )}
      <p className="muted raw-log-hint">
        「记录级别」决定终端、日志文件和本页记录哪些日志，修改后立即生效并在重启后保留；
        「筛选级别」只影响本页显示。文件：data/logs/shiritaibot.log。
      </p>
    </>
  );
}

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

export function LogsPage({ client, act }: PageProps) {
  const [tab, setTab] = useState<Tab>("runtime");
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>
          {tab === "runtime"
            ? "运行日志"
            : tab === "activity"
              ? "活动记录"
              : "原始滚动日志"}
        </h2>
        <div className="range-tabs" role="group" aria-label="日志类型">
          {(
            [
              ["runtime", "运行日志"],
              ["activity", "活动记录"],
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
      {tab === "runtime" && <RunLog client={client} act={act} />}
      {tab === "activity" && <ActivityLog client={client} />}
      {tab === "raw" && <RawLog client={client} />}
    </section>
  );
}
