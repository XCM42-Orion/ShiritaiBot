import { X } from "lucide-react";

export function Toast({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div role="status" className="toast">
      <span>{message}</span>
      <button type="button" aria-label="关闭通知" onClick={onDismiss}>
        <X size={15} />
      </button>
    </div>
  );
}
