import { Check, X } from "lucide-react";
import type { ApprovalRecord } from "../api/types";
import { formatDateTime } from "../lib/utils";

export function ApprovalCard({
  approval,
  busy,
  onResolve,
}: {
  approval: ApprovalRecord;
  busy: boolean;
  onResolve: (allow: boolean) => void;
}) {
  return (
    <article className="approval">
      <div>
        <span className="eyebrow">需要你确认 · {approval.id}</span>
        <h3>{approval.tool}</h3>
        <pre>{JSON.stringify(approval.args, null, 2)}</pre>
        <p className="muted">
          {approval.scope} · {formatDateTime(approval.expires)} 前有效
        </p>
      </div>
      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={busy}
          onClick={() => onResolve(true)}
        >
          <Check size={14} />
          允许这一次
        </button>
        <button type="button" disabled={busy} onClick={() => onResolve(false)}>
          <X size={14} />
          拒绝
        </button>
      </div>
    </article>
  );
}
