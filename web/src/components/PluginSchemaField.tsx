import { useState, type ReactNode } from "react";
import type { PluginSettingSchema } from "../api/types";
import type { FieldKind, FieldSpec } from "../config/schema";
import { inferFieldSpec } from "../config/schema";
import { Control } from "./FieldForm";
import { schemaLabel } from "../lib/pluginSchema";
import { DictField, TemplateListField } from "./PluginSchemaCollections";

export interface SchemaFieldProps {
  name: string;
  schema: PluginSettingSchema;
  value: unknown;
  onChange: (value: unknown) => void;
}

function valueType(value: unknown): string | undefined {
  if (Array.isArray(value)) return "array";
  if (value && typeof value === "object") return "object";
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  return undefined;
}

export function JsonSetting({
  label,
  value,
  onChange,
  help,
}: {
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
  help?: ReactNode;
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
        spellCheck={false}
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
      {help}
    </div>
  );
}

function Help({ schema }: { schema: PluginSettingSchema }) {
  if (!schema.description) return null;
  return (
    <small
      className={
        "field-help" + (schema["x-obvious-hint"] ? " field-help-obvious" : "")
      }
    >
      {schema.description}
    </small>
  );
}

/** Label + control + help; obvious hints sit above the control so they are read first. */
function Shell({
  label,
  schema,
  full,
  badge,
  children,
}: {
  label: string;
  schema: PluginSettingSchema;
  full?: boolean;
  badge?: ReactNode;
  children: ReactNode;
}) {
  const obvious = schema["x-obvious-hint"];
  return (
    <div className={"field" + (full ? " full" : "")}>
      <span className="field-name">
        {label}
        {badge}
      </span>
      {obvious && <Help schema={schema} />}
      {children}
      {!obvious && <Help schema={schema} />}
    </div>
  );
}

function enumOptions(schema: PluginSettingSchema, values: unknown[]) {
  const labels = schema["x-labels"] ?? [];
  const subtitles = schema["x-subtitles"] ?? [];
  return values.map((value, index) => ({
    value,
    key: String(value),
    label: labels[index] ?? String(value),
    subtitle: subtitles[index],
  }));
}

function MultiSelect({
  label,
  schema,
  value,
  onChange,
}: {
  label: string;
  schema: PluginSettingSchema;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const selected = Array.isArray(value) ? value : [];
  return (
    <div role="group" aria-label={label} className="modality-options">
      {enumOptions(schema, schema.items?.enum ?? []).map((option) => (
        <label key={option.key} title={option.subtitle}>
          <input
            type="checkbox"
            checked={selected.includes(option.value)}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? [...selected, option.value]
                  : selected.filter((item) => item !== option.value),
              )
            }
          />
          {option.label}
          {option.subtitle && <small> · {option.subtitle}</small>}
        </label>
      ))}
    </div>
  );
}

function ObjectFieldset({
  label,
  schema,
  value,
  onChange,
}: {
  label: string;
  schema: PluginSettingSchema;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const record =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  return (
    <fieldset className="field full plugin-fieldset">
      <legend>{label}</legend>
      {schema["x-obvious-hint"] && <Help schema={schema} />}
      <SchemaFields
        properties={schema.properties ?? {}}
        value={record}
        onChange={onChange}
      />
      {!schema["x-obvious-hint"] && <Help schema={schema} />}
    </fieldset>
  );
}

function PatternText({
  spec,
  pattern,
  value,
  onChange,
}: {
  spec: FieldSpec;
  pattern?: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  let invalid = false;
  if (pattern && typeof value === "string" && value) {
    try {
      invalid = !new RegExp(pattern, "u").test(value);
    } catch {
      invalid = false;
    }
  }
  return (
    <>
      <Control spec={spec} value={value} onChange={onChange} />
      {invalid && (
        <small className="field-error" role="alert">
          格式不符合要求：<code>{pattern}</code>
        </small>
      )}
    </>
  );
}

/** Renders one plugin setting from its (possibly AstrBot-derived) JSON Schema. */
export function SchemaField({
  name,
  schema,
  value,
  onChange,
}: SchemaFieldProps) {
  if (schema["x-invisible"]) return null;
  const label = schemaLabel(name, schema);
  const current = value === undefined ? schema.default : value;
  const type = schema.type ?? valueType(current);
  const widget = schema["x-widget"];
  const spec = (
    kind: FieldKind,
    extra: Partial<FieldSpec> = {},
  ): FieldSpec => ({
    key: name,
    label,
    kind,
    min: schema.minimum,
    max: schema.maximum,
    ...extra,
  });

  if (type === "boolean") {
    return (
      <div className="field field-toggle">
        <div>
          <span className="field-name">{label}</span>
          <Help schema={schema} />
        </div>
        <input
          aria-label={label}
          type="checkbox"
          checked={Boolean(current)}
          onChange={(event) => onChange(event.target.checked)}
        />
      </div>
    );
  }

  if (type === "array") {
    if (widget === "template_list") {
      return (
        <Shell label={label} schema={schema} full>
          <TemplateListField
            label={label}
            schema={schema}
            value={current}
            onChange={onChange}
            Field={SchemaField}
            Fields={SchemaFields}
          />
        </Shell>
      );
    }
    if (schema.items?.enum) {
      return (
        <Shell label={label} schema={schema} full>
          <MultiSelect
            label={label}
            schema={schema}
            value={current}
            onChange={onChange}
          />
        </Shell>
      );
    }
    // Untyped legacy values that are string arrays keep the line-per-item editor.
    const strings =
      schema.items?.type === "string" ||
      (!schema.type && Array.isArray(current) && current.every(isString));
    if (strings) {
      return (
        <Shell label={label} schema={schema} full>
          <Control
            spec={spec("list")}
            value={Array.isArray(current) ? current : []}
            onChange={onChange}
          />
        </Shell>
      );
    }
    return (
      <JsonSetting
        label={label}
        value={Array.isArray(current) ? current : []}
        onChange={onChange}
        help={<Help schema={schema} />}
      />
    );
  }

  if (type === "object") {
    const record =
      current && typeof current === "object" && !Array.isArray(current)
        ? (current as Record<string, unknown>)
        : {};
    if (widget === "dict" && schema["x-template-schema"]) {
      return (
        <Shell label={label} schema={schema} full>
          <DictField
            label={label}
            schema={schema}
            value={record}
            onChange={onChange}
            Field={SchemaField}
          />
        </Shell>
      );
    }
    if (schema.properties && Object.keys(schema.properties).length) {
      return (
        <ObjectFieldset
          label={label}
          schema={schema}
          value={record}
          onChange={onChange}
        />
      );
    }
    return (
      <JsonSetting
        label={label}
        value={record}
        onChange={onChange}
        help={<Help schema={schema} />}
      />
    );
  }

  if (schema.writeOnly) {
    return (
      <Shell label={label} schema={schema}>
        <Control spec={spec("secret")} value={current} onChange={onChange} />
      </Shell>
    );
  }

  if (schema.enum) {
    const options = enumOptions(schema, schema.enum);
    const selected = options.find((option) => option.value === current);
    return (
      <Shell label={label} schema={schema}>
        <Control
          spec={spec("select", {
            options: options.map((option) => option.key),
            optionLabels: Object.fromEntries(
              options.map((option) => [option.key, option.label]),
            ),
          })}
          value={current}
          onChange={(raw) =>
            onChange(options.find((option) => option.key === raw)?.value ?? raw)
          }
        />
        {selected?.subtitle && (
          <small className="field-help">{selected.subtitle}</small>
        )}
      </Shell>
    );
  }

  if (type === "integer" || type === "number") {
    const step = schema["x-step"] ?? (type === "integer" ? 1 : undefined);
    return (
      <Shell label={label} schema={schema} full={widget === "slider"}>
        <Control
          spec={spec(widget === "slider" ? "slider" : "number", { step })}
          value={current}
          onChange={onChange}
        />
      </Shell>
    );
  }

  if (widget === "editor") {
    const language = schema["x-editor"]?.language ?? "text";
    return (
      <Shell
        label={label}
        schema={schema}
        full
        badge={<span className="tag code-language">{language}</span>}
      >
        <textarea
          aria-label={label}
          className="code-editor"
          rows={10}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          value={String(current ?? "")}
          onChange={(event) => onChange(event.target.value)}
        />
      </Shell>
    );
  }

  const kind: FieldKind =
    widget === "textarea"
      ? "textarea"
      : type === "string"
        ? "text"
        : inferFieldSpec(name, current).kind;
  return (
    <Shell label={label} schema={schema} full={kind === "textarea"}>
      <PatternText
        spec={spec(kind)}
        pattern={schema.pattern}
        value={current}
        onChange={onChange}
      />
    </Shell>
  );
}

function isString(value: unknown) {
  return typeof value === "string";
}

/** A grid of fields for an object schema; unknown keys in `value` are kept untouched. */
export function SchemaFields({
  properties,
  value,
  onChange,
  extraKeys = [],
}: {
  properties: Record<string, PluginSettingSchema>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  extraKeys?: string[];
}) {
  const keys = [...new Set([...Object.keys(properties), ...extraKeys])];
  return (
    <div className="field-grid">
      {keys.map((key) => (
        <SchemaField
          key={key}
          name={key}
          schema={properties[key] ?? {}}
          value={value[key]}
          onChange={(next) => onChange({ ...value, [key]: next })}
        />
      ))}
    </div>
  );
}
