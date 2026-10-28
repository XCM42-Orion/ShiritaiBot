import { useQuery } from "@tanstack/react-query";
import { EmptyState } from "../components/EmptyState";
import { formatTime } from "../lib/utils";
import type { PageProps } from "./common";

export function LogsPage({ client }: PageProps) {
  const activity = useQuery({
    queryKey: ["records", "activity"],
    queryFn: () => client.records("activity"),
    refetchInterval: 4000,
  });
  return (
    <section className="panel">
      <h2>活动日志</h2>
      {activity.error && (
        <p className="error-text">读取日志失败：{activity.error.message}</p>
      )}
      {activity.data?.map((row) => (
        <div className="log-row" key={row.id}>
          <time>{formatTime(row.at)}</time>
          <span className="tag">{row.kind}</span>
          <span>{row.detail}</span>
        </div>
      ))}
      {!activity.data?.length && !activity.error && <EmptyState />}
    </section>
  );
}
