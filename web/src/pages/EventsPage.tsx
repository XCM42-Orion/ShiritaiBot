import { useQuery } from "@tanstack/react-query";
import { Details } from "../components/Details";
import { EmptyState } from "../components/EmptyState";
import type { PageProps } from "./common";

export function EventsPage({ client }: PageProps) {
  const events = useQuery({
    queryKey: ["records", "event"],
    queryFn: () => client.records("event"),
    refetchInterval: 4000,
  });

  return (
    <section className="panel">
      <h2>事件时间线</h2>
      {events.error && (
        <p className="error-text">读取事件失败：{events.error.message}</p>
      )}
      {events.data?.map((row) => (
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
      {!events.data?.length && !events.error && (
        <EmptyState text="来自插件、提醒和子代理的事件会汇集在这里。" />
      )}
    </section>
  );
}
