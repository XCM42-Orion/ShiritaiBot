import { useRef, useState, type ComponentType } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { PluginSettingSchema } from "../api/types";
import {
  pickPath,
  schemaDefault,
  schemaLabel,
  templateEntry,
} from "../lib/pluginSchema";

/** The recursive field renderer, passed in to avoid a circular import. */
type FieldComponent = ComponentType<{
  name: string;
  schema: PluginSettingSchema;
  value: unknown;
  onChange: (value: unknown) => void;
}>;

type FieldsComponent = ComponentType<{
  properties: Record<string, PluginSettingSchema>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}>;

/** `x-widget: dict` with `x-template-schema`: add known parameters one by one. */
export function DictField({
  label,
  schema,
  value,
  onChange,
  Field,
}: {
  label: string;
  schema: PluginSettingSchema;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  Field: FieldComponent;
}) {
  const templates = schema["x-template-schema"] ?? {};
  const available = Object.keys(templates).filter((key) => !(key in value));
  const [choice, setChoice] = useState("");
  const pending = available.includes(choice) ? choice : available[0];
  return (
    <div className="plugin-collection">
      {Object.keys(value).length === 0 && (
        <p className="muted">尚未添加参数。</p>
      )}
      {Object.entries(value).map(([key, item]) => {
        const child = templates[key] ?? {};
        return (
          <div key={key} className="plugin-collection-row">
            <Field
              name={key}
              schema={child}
              value={item}
              onChange={(next) => onChange({ ...value, [key]: next })}
            />
            <button
              type="button"
              aria-label={`移除参数 ${schemaLabel(key, child)}`}
              onClick={() => {
                const next = { ...value };
                delete next[key];
                onChange(next);
              }}
            >
              <Trash2 size={14} />
            </button>
          </div>
        );
      })}
      <div className="inline">
        <select
          aria-label={`${label}可添加参数`}
          value={pending ?? ""}
          disabled={!available.length}
          onChange={(event) => setChoice(event.target.value)}
        >
          {available.length ? (
            available.map((key) => (
              <option key={key} value={key}>
                {schemaLabel(key, templates[key])}
              </option>
            ))
          ) : (
            <option value="">已添加全部参数</option>
          )}
        </select>
        <button
          type="button"
          disabled={!pending}
          onClick={() =>
            pending &&
            onChange({ ...value, [pending]: schemaDefault(templates[pending]) })
          }
        >
          <Plus size={14} />
          添加参数
        </button>
      </div>
    </div>
  );
}

function summaryText(value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 60 ? text.slice(0, 57) + "…" : text;
}

let entrySerial = 0;

/** `x-widget: template_list`: an ordered list of entries, each built from a named template. */
export function TemplateListField({
  label,
  schema,
  value,
  onChange,
  Field,
  Fields,
}: {
  label: string;
  schema: PluginSettingSchema;
  value: unknown;
  onChange: (value: unknown) => void;
  Field: FieldComponent;
  Fields: FieldsComponent;
}) {
  const templates = schema["x-templates"] ?? {};
  const keys = Object.keys(templates);
  const rows = (Array.isArray(value) ? value : []) as Record<string, unknown>[];
  const [choice, setChoice] = useState(keys[0] ?? "");
  const [fresh, setFresh] = useState<string | null>(null);
  // Stable React keys so removing an entry does not shift open state to its neighbour.
  const ids = useRef<string[]>([]);
  while (ids.current.length < rows.length)
    ids.current.push(`e${entrySerial++}`);
  ids.current.length = rows.length;

  return (
    <div className="plugin-collection">
      {rows.length === 0 && <p className="muted">尚未添加条目。</p>}
      {rows.map((entry, index) => {
        const id = ids.current[index];
        const key = String(entry?.__template_key ?? "");
        const template = templates[key];
        const name = template?.name || key || "未知模板";
        const display = template?.display_item
          ? summaryText(pickPath(entry, template.display_item))
          : "";
        const update = (next: unknown) =>
          onChange(rows.map((row, at) => (at === index ? next : row)));
        return (
          <div key={id} className="plugin-entry">
            <details open={id === fresh || undefined}>
              <summary>
                <strong>{name}</strong>
                {display && <span>{display}</span>}
                {template?.hint && !template.hide_hint_in_list && (
                  <small>{template.hint}</small>
                )}
              </summary>
              <div className="plugin-entry-body">
                {template?.hint && template.hide_hint_in_list && (
                  <p className="field-help">{template.hint}</p>
                )}
                {template ? (
                  <Fields
                    properties={template.properties}
                    value={entry}
                    onChange={update}
                  />
                ) : (
                  <>
                    <p className="field-error" role="alert">
                      未知模板 {key || "（缺少 __template_key）"}，可直接编辑
                      JSON。
                    </p>
                    <Field
                      name={`${label}第 ${index + 1} 项`}
                      schema={{ type: "object" }}
                      value={entry}
                      onChange={update}
                    />
                  </>
                )}
              </div>
            </details>
            <button
              type="button"
              aria-label={`移除第 ${index + 1} 项 ${name}`}
              onClick={() => {
                ids.current.splice(index, 1);
                onChange(rows.filter((_, at) => at !== index));
              }}
            >
              <Trash2 size={14} />
            </button>
          </div>
        );
      })}
      <div className="inline">
        <select
          aria-label={`${label}条目模板`}
          value={choice}
          disabled={!keys.length}
          onChange={(event) => setChoice(event.target.value)}
        >
          {keys.map((key) => (
            <option key={key} value={key}>
              {templates[key].name || key}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={!templates[choice]}
          onClick={() => {
            const id = `e${entrySerial++}`;
            ids.current.push(id);
            setFresh(id);
            onChange([...rows, templateEntry(choice, templates[choice])]);
          }}
        >
          <Plus size={14} />
          添加条目
        </button>
      </div>
    </div>
  );
}
