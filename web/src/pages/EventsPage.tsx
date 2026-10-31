import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useQuery } from "@tanstack/react-query";
import { Details } from "../components/Details";
import { EmptyState } from "../components/EmptyState";
import { useState } from "react";
import { Pagination, PAGE_SIZE } from "../components/Pagination";
import type { PageProps } from "./common";

export function EventsPage({ client }: PageProps) {
  const [page, setPage] = useState(1);
  const events = useQuery({
    queryKey: ["records", "event", page],
    queryFn: () => client.recordsPage("event", page, PAGE_SIZE),
    refetchInterval: 4000,
  });

  return (
    <section className="panel">
      <h2>事件时间线</h2>
      {events.error && (
        <QueryErrorNotice message={`读取事件失败：${events.error.message}`} />
      )}
      {events.data?.items.map((row) => (
        <article className="record" key={row.id}>
          <div className="panel-head">
            <strong>{row.event.title}</strong>
            <span className="tag">{row.decision?.decision ?? row.status}</span>
          </div>
          <p>{row.decision?.reason}</p>
          <small>
            {row.event.source} · {row.event.kind}
          </small>
          <Details value={row} />
        </article>
      ))}
      {!events.data?.items.length && !events.error && !events.isPending && (
        <EmptyState text="来自插件、提醒和子代理的事件会汇集在这里。" />
      )}
      {events.isPending && <p className="muted">正在读取事件…</p>}
      <Pagination
        label="主动动态"
        page={events.data?.page ?? page}
        total={events.data?.total ?? 0}
        onChange={setPage}
        loading={events.isFetching}
      />
    </section>
  );
}
