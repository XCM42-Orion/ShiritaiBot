export const TIME_ZONE = "Asia/Shanghai";

export function uniqueId(base: string, rows: { id: string }[]): string {
  const safe =
    base.replace(/[^a-zA-Z0-9_.-]/g, "-").slice(0, 110) || "instance";
  let id = safe;
  let suffix = 2;
  while (rows.some((row) => row.id === id)) id = `${safe}-${suffix++}`;
  return id;
}

export function splitLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function isSecretRef(value: string): boolean {
  return /^(secret:|env:)/.test(value);
}

export function formatDateTime(
  value: string | number | null | undefined,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const date =
    typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatHour(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatNumber(value: number): string {
  return Number(value || 0).toLocaleString("zh-CN");
}

/** Accepts the value from a datetime-local input and converts it to ISO. */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
