import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { Pagination, PAGE_SIZE } from "../components/Pagination";
import type { MemoryKind } from "../api/types";
import type { PageProps } from "./common";

const KINDS: { key: MemoryKind | ""; label: string }[] = [
  { key: "", label: "全部" },
  { key: "entity", label: "人物与事物" },
  { key: "fact", label: "事实" },
  { key: "event", label: "事件" },
  { key: "conflict", label: "待确认" },
];
const STATUS: Record<string, string> = {
  active: "有效",
  confirmed: "已确认",
  candidate: "新认识",
  conflicted: "有冲突",
  superseded: "已取代",
  archived: "已遗忘",
  open: "待主人确认",
  resolved: "已处理",
};

// Read-only by design: the Agent maintains memory through tools (DESIGN.md §1.1).
export function MemoryPage({ client }: PageProps) {
  const [kind, setKind] = useState<MemoryKind | "">("");
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState("");
  const [page, setPage] = useState(1);
  const memories = useQuery({
    queryKey: ["memory", kind, query, page],
    queryFn: () => client.memoriesPage(kind, query, page, PAGE_SIZE),
    refetchInterval: 10000,
  });
  const detail = useQuery({
    queryKey: ["memory-detail", open],
    queryFn: () => client.memory(open),
    enabled: Boolean(open),
  });

  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2>记忆库</h2>
          <div className="range-tabs" role="group" aria-label="记忆类型">
            {KINDS.map(({ key, label }) => (
              <button
                type="button"
                key={key || "all"}
                className={kind === key ? "selected" : ""}
                aria-pressed={kind === key}
                onClick={() => {
                  setKind(key);
                  setPage(1);
                  setOpen("");
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <p className="muted">
          记忆由 Agent 维护：在对话里说“记住……”“忘掉……”或直接更正，有冲突时
          Agent 会问你。这里用于浏览和查看证据。
        </p>
        <form
          className="inline"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(draft.trim());
            setPage(1);
            setOpen("");
          }}
        >
          <input
            aria-label="搜索记忆"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="人名、事物或关键词（包括已取代和已遗忘的条目）"
          />
          <button type="submit">
            <Search size={16} />
            搜索
          </button>
        </form>
        {memories.error && (
          <QueryErrorNotice
            message={`读取记忆失败：${memories.error.message}`}
          />
        )}
        {memories.data?.items.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <span className="tag">
                {row.id} · {STATUS[row.status] ?? row.status}
              </span>
              <button
                type="button"
                aria-expanded={open === row.id}
                onClick={() => setOpen(open === row.id ? "" : row.id)}
              >
                {open === row.id ? "收起" : "详情与证据"}
              </button>
            </div>
            <p
              className={
                row.status === "archived" || row.status === "superseded"
                  ? "muted"
                  : ""
              }
            >
              {row.text}
            </p>
            {open === row.id && (
              <div className="memory-detail">
                {detail.error && (
                  <QueryErrorNotice
                    message={`读取详情失败：${detail.error.message}`}
                  />
                )}
                {detail.data?.id === row.id && (
                  <>
                    {[
                      ...(detail.data.facts ?? []),
                      ...(detail.data.events ?? []),
                      ...(detail.data.items ?? []),
                    ].map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                    {detail.data.history?.length ? (
                      <>
                        <h4>历史</h4>
                        {detail.data.history.map((line) => (
                          <p className="muted" key={line}>
                            {line}
                          </p>
                        ))}
                      </>
                    ) : null}
                    {detail.data.evidence?.map((item, position) => (
                      <blockquote key={position}>
                        “{item.quote}”
                        <small className="muted">
                          {" "}
                          —— {item.source}，{item.recorded}
                        </small>
                      </blockquote>
                    ))}
                    {detail.data.resolution && (
                      <p className="muted">
                        处理结果：{detail.data.resolution}
                      </p>
                    )}
                  </>
                )}
              </div>
            )}
          </article>
        ))}
        {!memories.data?.items.length &&
          !memories.error &&
          !memories.isPending && (
            <EmptyState
              text={
                query
                  ? "没有匹配的记忆。"
                  : "还没有记忆。在私聊里告诉 Agent 需要记住的人和事，它会记录在这里。"
              }
            />
          )}
        {memories.isPending && <p className="muted">正在读取记忆…</p>}
        <Pagination
          label="记忆库"
          page={memories.data?.page ?? page}
          total={memories.data?.total ?? 0}
          loading={memories.isFetching}
          onChange={(next) => {
            setPage(next);
            setOpen("");
          }}
        />
      </section>
    </>
  );
}
