import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { PAGES, pageMeta } from "../navigation";
import type { PageId } from "../navigation";

export function AppShell({
  page,
  onNavigate,
  pendingApprovals,
  online,
  version,
  onLogout,
  children,
}: {
  page: PageId;
  onNavigate: (page: PageId) => void;
  pendingApprovals: number;
  online: boolean;
  version: string;
  onLogout: () => void;
  children: ReactNode;
}) {
  const meta = pageMeta(page);
  return (
    <div className="layout">
      <aside>
        <a
          className="brand"
          href="#/overview"
          onClick={(event) => {
            event.preventDefault();
            onNavigate("overview");
          }}
        >
          <span className="brand-mark">
            <img src="/icon.png" alt="" />
          </span>
          <span>
            ShiritaiBot<small>个人控制台</small>
          </span>
        </a>
        <div className="workspace">
          <span className="avatar" aria-hidden="true">
            知
          </span>
          <div>
            <strong>我的工作空间</strong>
            <small>Owner · 私人助理</small>
          </div>
          <span className={"dot " + (online ? "on" : "")} aria-hidden="true" />
        </div>
        <nav aria-label="主导航">
          {PAGES.map(({ id, label, icon: Icon }) => (
            <button
              type="button"
              key={id}
              title={label}
              className={page === id ? "active" : ""}
              aria-current={page === id ? "page" : undefined}
              onClick={() => onNavigate(id)}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
              {id === "approvals" && pendingApprovals > 0 && (
                <b>{pendingApprovals}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className={"dot " + (online ? "on" : "")} aria-hidden="true" />
          {online ? "系统运行中" : "连接未就绪"}
          <small>{version}</small>
        </div>
      </aside>
      <main id="main-content">
        <header>
          <div>
            <span className="muted">本机控制台</span>
            <ChevronRight size={13} aria-hidden="true" />
            <span aria-current="page">{meta.label}</span>
          </div>
          <div>
            <span className="local-badge">仅本机</span>
            <button type="button" onClick={onLogout}>
              退出
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-title">
            <div>
              <h1>{meta.label}</h1>
              <p className="subtitle">{meta.subtitle}</p>
            </div>
          </div>
          {children}
        </div>
        <footer>
          ShiritaiBot <span>·</span> 本地数据
          <span className="muted">明确权限 · 持续记忆</span>
        </footer>
      </main>
    </div>
  );
}
