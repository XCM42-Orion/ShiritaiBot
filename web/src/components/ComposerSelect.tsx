import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { ComposerOption } from "../lib/composer";

export function ComposerSelect<T extends string>({
  label,
  value,
  options,
  onSelect,
  display,
}: {
  label: string;
  value: T | "";
  options: ComposerOption<T>[];
  onSelect: (value: T) => void;
  display?: string;
}) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function close(event: MouseEvent) {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="composer-select" ref={boxRef}>
      <button
        type="button"
        className="composer-chip"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span>{display || label}</span>
        <ChevronDown size={13} aria-hidden="true" />
      </button>
      {open && (
        <ul className="composer-popover" role="listbox" aria-label={label}>
          {options.map((option) => (
            <li key={option.value} role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={option.value === value}
                onClick={() => {
                  setOpen(false);
                  onSelect(option.value);
                }}
              >
                <strong>{option.label}</strong>
                <small>{option.hint}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
