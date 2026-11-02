import type { ReleaseInfo, UpdateStatus } from "../api/types";

type Parsed = { core: number[]; pre: string[] | null };

function parse(value: string): Parsed | null {
  const match = /^v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$/.exec(
    value.trim(),
  );
  if (!match) return null;
  return {
    core: match[1].split(".").map(Number),
    pre: match[2] ? match[2].split(".") : null,
  };
}

/** Semver-style comparison; returns null when either side is not a version. */
export function compareVersions(a: string, b: string): number | null {
  const left = parse(a);
  const right = parse(b);
  if (!left || !right) return null;
  for (let i = 0; i < Math.max(left.core.length, right.core.length); i++) {
    const diff = (left.core[i] ?? 0) - (right.core[i] ?? 0);
    if (diff) return Math.sign(diff);
  }
  if (!left.pre || !right.pre) return left.pre ? -1 : right.pre ? 1 : 0;
  for (let i = 0; i < Math.max(left.pre.length, right.pre.length); i++) {
    const x = left.pre[i];
    const y = right.pre[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const nx = /^\d+$/.test(x) ? Number(x) : null;
    const ny = /^\d+$/.test(y) ? Number(y) : null;
    if (nx !== null && ny !== null && nx !== ny) return Math.sign(nx - ny);
    if (nx === null || ny === null) {
      if (nx !== ny) return nx === null ? 1 : -1;
      if (x !== y) return x < y ? -1 : 1;
    }
  }
  return 0;
}

export function isCurrentRelease(
  release: ReleaseInfo,
  current: UpdateStatus["current"],
): boolean {
  if (current.kind === "release") return current.ref === release.tag;
  if (current.kind === "commit") return false;
  return compareVersions(release.tag, current.version) === 0;
}

/** Whether `latest` is newer than what is deployed. */
export function isNewer(
  latest: ReleaseInfo,
  current: UpdateStatus["current"],
): boolean {
  if (current.kind === "commit") {
    return Boolean(
      latest.published_at &&
      current.deployed_at &&
      Date.parse(latest.published_at) > Date.parse(current.deployed_at),
    );
  }
  const base =
    current.kind === "release" && current.ref ? current.ref : current.version;
  const order = compareVersions(latest.tag, base);
  return order === null ? latest.tag !== base : order > 0;
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : "";
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}
