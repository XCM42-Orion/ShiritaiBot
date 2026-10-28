import { Save } from "lucide-react";

export function SaveBar({
  busy,
  onSave,
  onDiscard,
}: {
  busy: boolean;
  onSave: () => void;
  onDiscard: () => void;
}) {
  return (
    <div className="save-bar" role="region" aria-label="未保存的配置">
      <span>配置有未保存的修改</span>
      <button type="button" onClick={onDiscard}>
        放弃修改
      </button>
      <button
        type="button"
        className="primary"
        disabled={busy}
        onClick={onSave}
      >
        <Save size={15} />
        保存并应用
      </button>
    </div>
  );
}
