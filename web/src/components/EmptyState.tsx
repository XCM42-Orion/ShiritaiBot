import { Circle } from "lucide-react";

export function EmptyState({ text = "这里还没有内容" }: { text?: string }) {
  return (
    <div className="empty">
      <Circle size={26} aria-hidden="true" />
      <p>{text}</p>
    </div>
  );
}
