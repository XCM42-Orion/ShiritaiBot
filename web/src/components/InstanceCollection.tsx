import type { ReactNode } from "react";
import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { Instance } from "../api/types";
import { FieldForm } from "./FieldForm";
import type { FieldSpec } from "../config/schema";
import { EmptyState } from "./EmptyState";

interface InstanceCollectionProps<T extends Instance> {
  title: string;
  items: T[];
  create: () => T;
  onChange: (items: T[]) => void;
  fields: FieldSpec[];
  subtitle: (item: T) => string;
  emptyText: string;
  renderActions?: (item: T, update: (next: T) => void) => ReactNode;
  renderExtra?: (item: T, update: (next: T) => void) => ReactNode;
}

export function InstanceCollection<T extends Instance>({
  title,
  items,
  create,
  onChange,
  fields,
  subtitle,
  emptyText,
  renderActions,
  renderExtra,
}: InstanceCollectionProps<T>) {
  const [selected, setSelected] = useState(0);
  const index = Math.min(selected, Math.max(0, items.length - 1));
  const item = items[index];

  function add() {
    const next = [...items, create()];
    onChange(next);
    setSelected(next.length - 1);
  }

  function update(next: T) {
    onChange(
      items.map((current, position) => (position === index ? next : current)),
    );
  }

  function remove() {
    onChange(items.filter((_, position) => position !== index));
    setSelected(Math.max(0, index - 1));
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
        <button type="button" onClick={add}>
          <Plus size={14} />
          添加
        </button>
      </div>
      {items.length === 0 ? (
        <EmptyState text={emptyText} />
      ) : (
        <div className="config-workspace">
          <section className="config-sidebar" aria-label={title + "列表"}>
            {items.map((row, position) => (
              <button
                type="button"
                key={position}
                className={
                  "config-item" + (position === index ? " selected" : "")
                }
                onClick={() => setSelected(position)}
              >
                <span className={"dot " + (row.enabled ? "on" : "")} />
                <span>
                  <strong>{row.id || "未命名"}</strong>
                  <small>{subtitle(row)}</small>
                </span>
              </button>
            ))}
          </section>
          <section className="config-editor">
            {item && (
              <>
                <div className="panel-head">
                  <div>
                    <h2>{item.id || "新建实例"}</h2>
                    <p className="muted">{subtitle(item)}</p>
                  </div>
                  <span className={"dot " + (item.enabled ? "on" : "")} />
                </div>
                <FieldForm value={item} fields={fields} onChange={update} />
                {renderExtra?.(item, update)}
                <div className="actions">
                  {renderActions?.(item, update)}
                  <button type="button" className="danger" onClick={remove}>
                    <Trash2 size={14} />
                    移除
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
