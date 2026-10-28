import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { formatDateTime } from "../lib/utils";
import type { PageProps } from "./common";

export function MemoryPage({ client, act }: PageProps) {
  const [content, setContent] = useState("");
  const memories = useQuery({
    queryKey: ["memory"],
    queryFn: () => client.memories(),
    refetchInterval: 10000,
  });

  function remember(event: React.FormEvent) {
    event.preventDefault();
    if (!content.trim()) return;
    const text = content.trim();
    void act(async () => {
      await client.addMemory(text);
      setContent("");
    }, "已记住");
  }

  return (
    <>
      <section className="panel">
        <h2>记住一件事</h2>
        <form className="inline" onSubmit={remember}>
          <input
            aria-label="记忆内容"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="例如：解释数学时，先举例再推导"
            required
          />
          <button type="submit" className="primary">
            <Plus size={16} />
            记住
          </button>
        </form>
      </section>
      <section className="panel">
        <h2>记忆库</h2>
        {memories.error && (
          <QueryErrorNotice
            message={`读取记忆失败：${memories.error.message}`}
          />
        )}
        {memories.data?.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <span className="tag">
                {row.kind} · {row.visibility}
              </span>
              <span className="muted">
                {row.status} · {formatDateTime(row.created)}
              </span>
            </div>
            <p>{row.content}</p>
            {row.status === "active" && (
              <button
                type="button"
                onClick={() =>
                  void act(() => client.archiveMemory(row.id), "已归档")
                }
              >
                归档
              </button>
            )}
          </article>
        ))}
        {!memories.data?.length && !memories.error && (
          <EmptyState text="明确记下的事实会存入这里，并用于私人对话。" />
        )}
      </section>
    </>
  );
}
