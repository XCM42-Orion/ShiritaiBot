import { useEffect, useRef, useState } from "react";
import type { FieldSpec } from "../config/schema";
import { inferFieldSpec } from "../config/schema";
import { SecretField } from "./SecretField";
import { TextFieldList } from "./TextFieldList";

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
  if (spec.kind === "select") {
    return (
      <select
        aria-label={spec.label}
        value={String(value ?? "")}
        onChange={(event) => onChange(event.target.value)}
      >
        {(spec.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
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
