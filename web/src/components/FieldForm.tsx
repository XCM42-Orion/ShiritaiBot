import { useEffect, useRef, useState } from "react";
import type { FieldSpec } from "../config/schema";
import { inferFieldSpec } from "../config/schema";
import { SecretField } from "./SecretField";
import { TextFieldList } from "./TextFieldList";

export function jsonObjectError(value: unknown): string | null {
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return '请填写 JSON 对象，例如 {"temperature": 0.7}';
    }
    return null;
  } catch {
    return "JSON 格式无效，请检查引号、逗号和括号。";
  }
}

/** Number inputs keep transient empty text so clearing a field does not snap to 0. */
function NumberInput({
  spec,
  value,
  onChange,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (value: number) => void;
}) {
  const current = typeof value === "number" ? value : 0;
  const [text, setText] = useState(String(current));
  const committed = useRef(current);

  useEffect(() => {
    if (current !== committed.current) {
      committed.current = current;
      setText(String(current));
    }
  }, [current]);

  return (
    <input
      aria-label={spec.label}
      type="number"
      min={spec.min}
      max={spec.max}
      step={spec.step}
      value={text}
      onChange={(event) => {
        const raw = event.target.value;
        setText(raw);
        if (raw === "") return;
        const next = Number(raw);
        committed.current = next;
        onChange(next);
      }}
      onBlur={() => {
        if (text === "") setText(String(committed.current));
      }}
    />
  );
}

function Control({
  spec,
  value,
  onChange,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (spec.kind === "json") {
    const error = jsonObjectError(value);
    return (
      <>
        <textarea
          aria-label={spec.label}
          aria-invalid={Boolean(error)}
          rows={6}
          spellCheck={false}
          placeholder={spec.placeholder}
          value={
            typeof value === "string"
              ? value
              : JSON.stringify(value ?? {}, null, 2)
          }
          onChange={(event) => onChange(event.target.value)}
        />
        {error && (
          <small className="field-error" role="alert">
            {error}
          </small>
        )}
      </>
    );
  }
  if (spec.kind === "multiselect") {
    const selected = Array.isArray(value) ? value : [];
    return (
      <div role="group" aria-label={spec.label} className="modality-options">
        {(spec.options ?? []).map((option) => (
          <label key={option}>
            <input
              type="checkbox"
              checked={selected.includes(option)}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...selected, option]
                    : selected.filter((item) => item !== option),
                )
              }
            />
            {spec.optionLabels?.[option] ?? option}
          </label>
        ))}
      </div>
    );
  }
  if (spec.kind === "select") {
    return (
      <select
        aria-label={spec.label}
        value={String(value ?? "")}
        onChange={(event) => onChange(event.target.value)}
      >
        {(spec.options ?? []).map((option) => (
          <option key={option} value={option}>
            {spec.optionLabels?.[option] ?? option}
          </option>
        ))}
      </select>
    );
  }
  if (spec.kind === "number") {
    return (
      <NumberInput
        spec={spec}
        value={value}
        onChange={(next) => onChange(next)}
      />
    );
  }
  if (spec.kind === "time") {
    return (
      <input
        aria-label={spec.label}
        type="time"
        value={String(value ?? "")}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  if (spec.kind === "textarea") {
    return (
      <textarea
        aria-label={spec.label}
        rows={4}
        placeholder={spec.placeholder}
        value={String(value ?? "")}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  if (spec.kind === "list") {
    return (
      <TextFieldList
        label={spec.label}
        value={Array.isArray(value) ? (value as string[]) : []}
        onChange={(next) => onChange(next)}
      />
    );
  }
  if (spec.kind === "secret") {
    return <SecretField value={String(value ?? "")} onChange={onChange} />;
  }
  return (
    <input
      aria-label={spec.label}
      type="text"
      placeholder={spec.placeholder}
      value={String(value ?? "")}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function Field({
  spec,
  value,
  onChange,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const help = spec.help ? (
    <small className="field-help">{spec.help}</small>
  ) : null;
  if (spec.kind === "boolean") {
    return (
      <div className="field field-toggle">
        <div>
          <span className="field-name">{spec.label}</span>
          {help}
        </div>
        <input
          aria-label={spec.label}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
      </div>
    );
  }
  return (
    <div className={"field" + (spec.full ? " full" : "")}>
      <span className="field-name">{spec.label}</span>
      <Control spec={spec} value={value} onChange={onChange} />
      {help}
    </div>
  );
}

/**
 * Renders a typed form from declarative field specs. Unknown keys can be shown
 * as generic fields (used for plugin settings defined outside the frontend).
 */
export function FieldForm<T extends object>({
  value,
  fields,
  onChange,
  allowExtra = false,
}: {
  value: T;
  fields: FieldSpec[];
  onChange: (next: T) => void;
  allowExtra?: boolean;
}) {
  const record = value as Record<string, unknown>;
  const known = new Set(fields.map((field) => field.key));
  const specs = allowExtra
    ? [
        ...fields,
        ...Object.keys(record)
          .filter((key) => !known.has(key))
          .map((key) => inferFieldSpec(key, record[key])),
      ]
    : fields;
  return (
    <div className="field-grid">
      {specs.map((spec) => (
        <Field
          key={spec.key}
          spec={spec}
          value={record[spec.key]}
          onChange={(next) => onChange({ ...record, [spec.key]: next } as T)}
        />
      ))}
    </div>
  );
}
