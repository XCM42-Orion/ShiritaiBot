import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CircleCheck,
  CircleX,
  ExternalLink,
  GitCommitHorizontal,
  RefreshCw,
  Tag,
  X,
} from "lucide-react";
import type { ApiClient } from "../api/client";
import type { UpdatePhase } from "../api/types";
import { Markdown } from "./Markdown";
import { Pagination } from "./Pagination";
import {
  formatDate,
  isCurrentRelease,
  isNewer,
  shortSha,
} from "../lib/version";

const PAGE_SIZE = 6;
const ACTIVE: UpdatePhase[] = [
  "downloading",
  "building",
  "deploying",
  "restarting",
];
const STEPS: { phase: UpdatePhase; label: string }[] = [
  { phase: "downloading", label: "下载源码" },
  { phase: "building", label: "构建 WebUI" },
  { phase: "deploying", label: "部署与依赖" },
  { phase: "restarting", label: "重启服务" },
];

type Selection = { kind: "release"; tag: string } | { kind: "commit" };

export function UpdateDialog({
  client,
  onClose,
  onRestarting,
  restarting,
  blockedReason,
}: {
  client: ApiClient;
  onClose: () => void;
  onRestarting: () => void;
  restarting: boolean;
  blockedReason: string | null;
}) {
  const queryClient = useQueryClient();
  const [closing, setClosing] = useState(false);
  const [showBeta, setShowBeta] = useState(false);
  const [showCommit, setShowCommit] = useState(false);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [finished, setFinished] = useState(false);
  const [dismissedFailure, setDismissedFailure] = useState(false);
  const handedOff = useRef(false);
  const dialog = useRef<HTMLDivElement>(null);

  const status = useQuery({
    queryKey: ["update", "status"],
    queryFn: () => client.updateStatus(),
    refetchInterval: (query) =>
      restarting ||
      starting ||
      ACTIVE.includes(query.state.data?.phase ?? "idle")
        ? 1000
        : false,
    retry: false,
  });
  const releases = useQuery({
    queryKey: ["update", "releases", page, showBeta],
    queryFn: () => client.updateReleases(page, PAGE_SIZE, showBeta),
    placeholderData: (previous) => previous,
  });
  const preview = useQuery({
    queryKey: ["update", "preview"],
    queryFn: () => client.updatePreview(),
    enabled: showCommit,
  });

  const phase = status.data?.phase ?? "idle";
  const active = starting || ACTIVE.includes(phase);
  const current = status.data?.current;
  const items = releases.data?.items ?? [];
  const commit = showCommit ? (preview.data?.commit ?? null) : null;

  // Hand the wait for the new process to the console's restart tracker exactly once.
  useEffect(() => {
    if (phase !== "restarting" || handedOff.current) return;
    handedOff.current = true;
    onRestarting();
  }, [phase, onRestarting]);

  useEffect(() => {
    if (!handedOff.current || restarting) return;
    handedOff.current = false;
    setFinished(true);
    void queryClient.invalidateQueries({ queryKey: ["update"] });
  }, [restarting, queryClient]);

  useEffect(() => {
    dialog.current?.focus();
  }, []);

  // Keep the selection valid as pages and filters change.
  useEffect(() => {
    if (
      selected?.kind === "commit" &&
      showCommit &&
      (commit || preview.isPending)
    )
      return;
    if (
      selected?.kind === "release" &&
      items.some((r) => r.tag === selected.tag)
    )
      return;
    if (commit && page === 1) setSelected({ kind: "commit" });
    else if (items[0]) setSelected({ kind: "release", tag: items[0].tag });
    else setSelected(null);
    setConfirming(false);
  }, [items, commit, selected, page, showCommit, preview.isPending]);

  function close() {
    if (active) return;
    setClosing(true);
    setTimeout(onClose, 180);
  }

  async function refresh() {
    setRefreshing(true);
    try {
      const [listing] = await Promise.all([
        client.updateReleases(1, PAGE_SIZE, showBeta, true),
        showCommit ? client.updatePreview(true) : Promise.resolve(null),
      ]);
      setPage(1);
      queryClient.setQueryData(["update", "releases", 1, showBeta], listing);
      await queryClient.invalidateQueries({ queryKey: ["update"] });
    } catch {
      // Query errors are shown inline after invalidation.
    } finally {
      setRefreshing(false);
    }
  }

  async function apply() {
    if (!target) return;
    setStarting(true);
    setStartError(null);
    setFinished(false);
    setDismissedFailure(false);
    try {
      const next = await client.applyUpdate(target.kind, target.ref);
      queryClient.setQueryData(["update", "status"], next);
    } catch (error) {
      setStartError(error instanceof Error ? error.message : String(error));
    } finally {
      setStarting(false);
      setConfirming(false);
    }
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  }

  const release =
    selected?.kind === "release"
      ? items.find((r) => r.tag === selected.tag)
      : undefined;
  const target =
    selected?.kind === "commit" && commit
      ? {
          kind: "commit" as const,
          ref: commit.sha,
          label: shortSha(commit.sha),
        }
      : release
        ? { kind: "release" as const, ref: release.tag, label: release.tag }
        : null;
  const selectedIsCurrent = Boolean(
    current &&
    ((release && isCurrentRelease(release, current)) ||
      (selected?.kind === "commit" &&
        commit &&
        current.kind === "commit" &&
        current.ref === commit.sha)),
  );
  const unavailable = status.data && !status.data.available;
  const applyBlocked =
    blockedReason ?? (unavailable ? status.data?.reason : null) ?? null;
  const showProgress =
    active || (phase === "failed" && !dismissedFailure) || finished;

  return (
    <div
      className={"update-backdrop" + (closing ? " closing" : "")}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="update-title"
        className="update-dialog"
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <div className="update-head">
          <div>
            <h2 id="update-title">检查更新</h2>
            <small className="muted">
              当前版本{" "}
              <code>
                {current
                  ? current.kind === "commit"
                    ? shortSha(current.ref)
                    : (current.ref ?? "v" + current.version)
                  : "…"}
              </code>
              {current?.deployed_at &&
                " · 部署于 " + formatDate(current.deployed_at)}
              {status.data && (
                <>
                  {" · "}
                  <a
                    href={"https://github.com/" + status.data.repository}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {status.data.repository}
                  </a>
                </>
              )}
            </small>
          </div>
          <div className="actions">
            {!showProgress && (
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={refreshing}
                aria-label="重新检查"
                title="重新检查"
              >
                <RefreshCw
                  size={14}
                  className={refreshing ? "spinner" : undefined}
                  aria-hidden="true"
                />
              </button>
            )}
            <button
              type="button"
              onClick={close}
              disabled={active}
              aria-label="关闭"
              title={active ? "更新进行中，请稍候" : "关闭"}
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        </div>

        {showProgress ? (
          <Progress
            phase={finished ? "done" : starting ? "downloading" : phase}
            message={status.data?.message ?? ""}
            output={status.data?.output ?? []}
            current={
              current
                ? current.kind === "commit"
                  ? shortSha(current.ref)
                  : (current.ref ?? "v" + current.version)
                : ""
            }
            onBack={() => {
              setFinished(false);
              setDismissedFailure(true);
            }}
          />
        ) : (
          <>
            <section className="update-notes" aria-label="更新日志">
              {selected?.kind === "commit" && commit ? (
                <>
                  <div className="update-notes-title">
                    <GitCommitHorizontal size={16} aria-hidden="true" />
                    <strong>开发版 {shortSha(commit.sha)}</strong>
                    <span className="tag beta">未发布</span>
                    {commit.url && (
                      <a
                        href={commit.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        aria-label="在 GitHub 查看提交"
                      >
                        <ExternalLink size={13} aria-hidden="true" />
                      </a>
                    )}
                  </div>
                  <p className="muted">
                    {commit.base
                      ? `相对 ${commit.base} 新增 ${commit.ahead_by} 个提交`
                      : `最近 ${commit.commits.length} 个提交`}
                    {commit.ahead_by > commit.commits.length &&
                      `，显示最近 ${commit.commits.length} 个`}
                  </p>
                  <ul className="commit-list">
                    {commit.commits.map((row) => (
                      <li key={row.sha}>
                        <code>{shortSha(row.sha)}</code>
                        <span>{row.message}</span>
                        <small className="muted">{row.author}</small>
                      </li>
                    ))}
                  </ul>
                </>
              ) : release ? (
                <>
                  <div className="update-notes-title">
                    <Tag size={16} aria-hidden="true" />
                    <strong>{release.name}</strong>
                    {release.prerelease && (
                      <span className="tag beta">测试版</span>
                    )}
                    {release.url && (
                      <a
                        href={release.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        aria-label="在 GitHub 查看版本"
                      >
                        <ExternalLink size={13} aria-hidden="true" />
                      </a>
                    )}
                  </div>
                  {release.body.trim() ? (
                    <Markdown text={release.body} />
                  ) : (
                    <p className="muted">此版本没有填写更新日志。</p>
                  )}
                </>
              ) : releases.isPending || (showCommit && preview.isPending) ? (
                <p className="muted">正在检查 GitHub…</p>
              ) : releases.isError ? (
                <p role="alert">{releases.error.message}</p>
              ) : (
                <p className="muted">
                  {showBeta ? "仓库还没有发布版本。" : "还没有正式版。"}
                </p>
              )}
            </section>

            <section className="update-picker" aria-label="版本选择">
              <div className="update-filters">
                <label>
                  <input
                    type="checkbox"
                    checked={showBeta}
                    onChange={(event) => {
                      setShowBeta(event.target.checked);
                      setPage(1);
                    }}
                  />
                  显示测试版
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={showCommit}
                    onChange={(event) => {
                      setShowCommit(event.target.checked);
                      setPage(1);
                      if (event.target.checked) setSelected({ kind: "commit" });
                    }}
                  />
                  显示未发布的最新提交
                </label>
              </div>
              <div className="update-versions" role="listbox" aria-label="版本">
                {showCommit && page === 1 && (
                  <>
                    {preview.isPending && (
                      <p className="muted">正在读取最新提交…</p>
                    )}
                    {preview.isError && (
                      <p role="alert">{preview.error.message}</p>
                    )}
                    {preview.isSuccess && !commit && (
                      <p className="muted">最新提交已包含在最新版本中。</p>
                    )}
                    {commit && (
                      <button
                        type="button"
                        role="option"
                        aria-selected={selected?.kind === "commit"}
                        className={
                          "update-version" +
                          (selected?.kind === "commit" ? " selected" : "")
                        }
                        onClick={() => setSelected({ kind: "commit" })}
                      >
                        <GitCommitHorizontal size={15} aria-hidden="true" />
                        <span>
                          <strong>开发版 {shortSha(commit.sha)}</strong>
                          <small>{commit.message}</small>
                        </span>
                        <span className="tag beta">未发布</span>
                        <small className="muted">
                          {formatDate(commit.date)}
                        </small>
                      </button>
                    )}
                  </>
                )}
                {items.map((row, index) => {
                  const isCurrent = current
                    ? isCurrentRelease(row, current)
                    : false;
                  const chosen =
                    selected?.kind === "release" && selected.tag === row.tag;
                  return (
                    <button
                      type="button"
                      role="option"
                      aria-selected={chosen}
                      key={row.tag}
                      className={"update-version" + (chosen ? " selected" : "")}
                      style={{ animationDelay: index * 30 + "ms" }}
                      onClick={() =>
                        setSelected({ kind: "release", tag: row.tag })
                      }
                    >
                      <Tag size={15} aria-hidden="true" />
                      <span>
                        <strong>{row.tag}</strong>
                        {row.name !== row.tag && <small>{row.name}</small>}
                      </span>
                      {isCurrent && <span className="tag current">当前</span>}
                      {!isCurrent &&
                        page === 1 &&
                        index === 0 &&
                        current &&
                        isNewer(row, current) && (
                          <span className="tag new">新版本</span>
                        )}
                      {row.prerelease && (
                        <span className="tag beta">测试版</span>
                      )}
                      <small className="muted">
                        {formatDate(row.published_at)}
                      </small>
                    </button>
                  );
                })}
              </div>
              {(releases.data?.total ?? 0) > PAGE_SIZE && (
                <Pagination
                  label="版本"
                  page={page}
                  total={releases.data?.total ?? 0}
                  pageSize={PAGE_SIZE}
                  loading={releases.isFetching}
                  onChange={setPage}
                />
              )}
              <div className="update-apply">
                {startError && <p role="alert">{startError}</p>}
                {applyBlocked && !startError && (
                  <small className="muted">{applyBlocked}</small>
                )}
                {confirming && target ? (
                  <div className="update-confirm">
                    <span>
                      将下载 <code>{target.label}</code>
                      ，重新构建并重启服务。期间机器人会短暂离线。
                    </span>
                    <div className="actions">
                      <button
                        type="button"
                        className="primary"
                        onClick={() => void apply()}
                        disabled={starting}
                      >
                        确认切换
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(false)}
                        disabled={starting}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="primary"
                    disabled={
                      !target || selectedIsCurrent || Boolean(applyBlocked)
                    }
                    onClick={() => setConfirming(true)}
                  >
                    {selectedIsCurrent
                      ? "已是当前版本"
                      : target
                        ? `切换到 ${target.label}`
                        : "选择一个版本"}
                  </button>
                )}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}

function Progress({
  phase,
  message,
  output,
  current,
  onBack,
}: {
  phase: UpdatePhase | "done";
  message: string;
  output: string[];
  current: string;
  onBack: () => void;
}) {
  const log = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [output.length]);
  const index = STEPS.findIndex((step) => step.phase === phase);
  const done = phase === "done";
  const failed = phase === "failed";
  return (
    <section className="update-progress" aria-live="polite">
      {done ? (
        <div className="update-result ok">
          <CircleCheck size={36} aria-hidden="true" />
          <strong>更新完成</strong>
          <span className="muted">当前版本 {current}</span>
        </div>
      ) : failed ? (
        <div className="update-result failed">
          <CircleX size={36} aria-hidden="true" />
          <strong>更新失败</strong>
          <span role="alert">{message}</span>
          <span className="muted">程序文件已恢复为原版本，服务未重启。</span>
        </div>
      ) : (
        <ol className="update-steps">
          {STEPS.map((step, i) => (
            <li
              key={step.phase}
              className={i < index ? "done" : i === index ? "active" : ""}
            >
              <span className="update-step-dot" aria-hidden="true" />
              {step.label}
            </li>
          ))}
        </ol>
      )}
      {!done && !failed && <p className="update-message">{message}</p>}
      {output.length > 0 && !done && (
        <pre ref={log} className="update-log">
          {output.join("\n")}
        </pre>
      )}
      {(done || failed) && (
        <div className="actions">
          <button type="button" onClick={onBack}>
            返回版本列表
          </button>
        </div>
      )}
    </section>
  );
}
