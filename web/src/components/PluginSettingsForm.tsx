import { useState } from "react";
import type { PluginConfig, PluginInfo } from "../api/types";
import type { FieldSpec } from "../config/schema";
import { inferFieldSpec } from "../config/schema";
import { Field } from "./FieldForm";

function JsonSetting({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState(false);
  return (
    <div className="field full">
      <span className="field-name">{label}</span>
      <textarea
        aria-label={label}
        aria-invalid={error}
        rows={6}
        value={draft ?? JSON.stringify(value ?? {}, null, 2)}
        onChange={(event) => {
          const text = event.target.value;
          setDraft(text);
          try {
            const parsed: unknown = JSON.parse(text);
            if (
              !parsed ||
              typeof parsed !== "object" ||
              Array.isArray(parsed) !== Array.isArray(value)
            )
              throw new Error();
            setError(false);
            onChange(parsed);
          } catch {
            setError(true);
          }
        }}
        onBlur={() => {
          if (!error) setDraft(null);
        }}
      />
      {error && (
        <small className="field-error" role="alert">
          请填写有效的 JSON {Array.isArray(value) ? "数组" : "对象"}后保存。
        </small>
      )}
    </div>
  );
}

export function PluginSettingsForm({
  plugin,
  metadata,
  onChange,
}: {
  plugin: PluginConfig;
  metadata?: PluginInfo;
  onChange: (settings: Record<string, unknown>) => void;
}) {
  const properties = metadata?.settings_schema?.properties ?? {};
  const record = { ...metadata?.defaults, ...plugin.settings };
  const keys = [
    ...new Set([...Object.keys(properties), ...Object.keys(record)]),
  ];
  return (
    <>
      <div className="field-grid">
        {keys.map((key) => {
          const property = properties[key] ?? {};
          const label = property.title ?? key;
          if (
            ["object", "array"].includes(property.type ?? "") &&
            !(property.type === "array" && property.items?.type === "string")
          ) {
            return (
              <JsonSetting
                key={key}
                label={label}
                value={record[key] ?? (property.type === "array" ? [] : {})}
                onChange={(value) => onChange({ ...record, [key]: value })}
              />
            );
          }
          const spec: FieldSpec = {
            key,
            label,
            help: property.description,
            kind: property.writeOnly
              ? "secret"
              : property.enum
                ? "select"
                : property.type === "boolean"
                  ? "boolean"
                  : ["number", "integer"].includes(property.type ?? "")
                    ? "number"
                    : property.type === "array"
                      ? "list"
                      : inferFieldSpec(key, record[key] ?? property.default)
                          .kind,
            options: property.enum,
            min: property.minimum,
            max: property.maximum,
          };
          return (
            <Field
              key={key}
              spec={spec}
              value={record[key]}
              onChange={(value) => onChange({ ...record, [key]: value })}
            />
          );
        })}
      </div>
      <details>
        <summary>编辑完整配置 JSON</summary>
        <JsonSetting
          label="插件配置 JSON"
          value={record}
          onChange={(value) => onChange(value as Record<string, unknown>)}
        />
      </details>
    </>
  );
}
