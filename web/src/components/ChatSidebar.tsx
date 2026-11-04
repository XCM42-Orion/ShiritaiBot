import { Plus } from "lucide-react";
import type { SessionGroup } from "../lib/sessions";
import { relativeTime } from "../lib/sessions";

export interface ChatSidebarProps {
  groups: SessionGroup[];
  activity: Record<string, number>;
  selected: string;
  running: Set<string>;
  pending: Set<string>;
  onSelect: (id: string) => void;
  onNew: () => void;
}

export function ChatSidebar({
  groups,
  activity,
  selected,
  running,
  pending,
  onSelect,
  onNew,
}: ChatSidebarProps) {
  return (
    <div className="chat-sidebar">
      <div className="chat-brand">
        <strong>ShiritaiBot</strong>
        <small>对话</small>
      </div>
      <button type="button" className="chat-new" onClick={onNew}>
        <Plus size={15} aria-hidden="true" />
        新对话
      </button>
      <nav className="chat-groups" aria-label="会话列表">
        {groups.map((group) => (
          <div className="chat-group" key={group.key}>
            <h2>
              {group.label}
              <span className="chat-group-count">{group.items.length}</span>
            </h2>
            {group.items.map((item) => {
              const active = item.id === selected;
              return (
                <button
                  type="button"
                  key={item.id}
                  className={"chat-session" + (active ? " active" : "")}
                  aria-current={active ? "page" : undefined}
                  onClick={() => onSelect(item.id)}
                >
                  <span className="chat-session-title">{item.title}</span>
                  <small>{relativeTime(activity[item.id] ?? 0)}</small>
                  {running.has(item.id) && (
                    <span className="session-dot" aria-label="运行中" />
                  )}
                  {pending.has(item.id) && (
                    <span className="session-badge" aria-label="待审批">
                      !
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </nav>
    </div>
  );
}
