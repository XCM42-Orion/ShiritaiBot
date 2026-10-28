import { useEffect, useRef, useState } from "react";
import { splitLines } from "../lib/utils";

/** Multiline editor for string arrays (list of group numbers, tool names, ...). */
export function TextFieldList({
  value,
  onChange,
  label,
  placeholder = "每行一个值",
}: {
  value: string[];
  onChange: (value: string[]) => void;
  label?: string;
  placeholder?: string;
}) {
  const [text, setText] = useState(value.join("\n"));
  const lastValue = useRef(value);

  useEffect(() => {
    if (value !== lastValue.current) {
      lastValue.current = value;
      setText(value.join("\n"));
    }
  }, [value]);

  return (
    <textarea
      aria-label={label}
      rows={Math.max(2, Math.min(value.length || 1, 6))}
      value={text}
      placeholder={placeholder}
      onChange={(event) => {
        setText(event.target.value);
        const next = splitLines(event.target.value);
        // Keep trailing newlines while typing; only external values reset text.
        lastValue.current = next;
        onChange(next);
      }}
    />
  );
}
