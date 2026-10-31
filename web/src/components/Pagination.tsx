import { ChevronLeft, ChevronRight } from "lucide-react";

export const PAGE_SIZE = 20;

export function Pagination({
  label,
  page,
  total,
  pageSize = PAGE_SIZE,
  onChange,
  loading = false,
}: {
  label: string;
  page: number;
  total: number;
  pageSize?: number;
  onChange: (page: number) => void;
  loading?: boolean;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, pages);
  return (
    <nav className="pagination" aria-label={label + "分页"}>
      <small className="muted" aria-live="polite">
        共 {total} 条 · 第 {current} / {pages} 页
      </small>
      <div className="actions">
        <button
          type="button"
          disabled={loading || current <= 1}
          onClick={() => onChange(current - 1)}
          aria-label={label + "上一页"}
        >
          <ChevronLeft size={14} /> 上一页
        </button>
        <button
          type="button"
          disabled={loading || current >= pages}
          onClick={() => onChange(current + 1)}
          aria-label={label + "下一页"}
        >
          下一页 <ChevronRight size={14} />
        </button>
      </div>
    </nav>
  );
}
