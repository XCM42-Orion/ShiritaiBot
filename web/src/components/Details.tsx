import { ChevronRight } from "lucide-react";

export function Details({
  value,
  summary = "查看详情",
}: {
  value: unknown;
  summary?: string;
}) {
  return (
    <details className="details">
      <summary>
        {summary}
        <ChevronRight size={12} aria-hidden="true" />
      </summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
