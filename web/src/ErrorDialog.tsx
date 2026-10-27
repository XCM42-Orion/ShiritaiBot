import { useEffect, useRef } from "react";

export function ErrorDialog({
  message,
  onClose,
}: {
  message: string | null;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (message && !ref.current?.open) ref.current?.showModal();
    if (!message && ref.current?.open) ref.current?.close();
  }, [message]);
  return (
    <dialog
      className="error-dialog"
      ref={ref}
      aria-labelledby="error-title"
      aria-describedby="error-detail"
      onCancel={onClose}
      onClose={onClose}
    >
      <h2 id="error-title">操作未完成</h2>
      <p id="error-detail">{message}</p>
      <div className="actions">
        <button className="primary" autoFocus onClick={onClose}>
          知道了
        </button>
      </div>
    </dialog>
  );
}
