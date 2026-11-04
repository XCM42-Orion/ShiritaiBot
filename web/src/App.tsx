import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createClient } from "./api/client";
import type { ApiClient } from "./api/client";
import { AppShell } from "./components/AppShell";
import { ChatWorkspace } from "./components/ChatWorkspace";
import { LoginView } from "./components/LoginView";
import { SaveBar } from "./components/SaveBar";
import { UpdateDialog } from "./components/UpdateDialog";
import { isNewer } from "./lib/version";
import { useConfigDraft } from "./hooks/useConfigDraft";
import { useHashRoute } from "./hooks/useHashRoute";
import { NoticeProvider, useNotice } from "./hooks/useNotice";
import { useRealtime } from "./hooks/useRealtime";
import type { RealtimeEvent } from "./hooks/useRealtime";
import { AdaptersPage } from "./pages/AdaptersPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { ChatPage } from "./pages/ChatPage";
import { EventsPage } from "./pages/EventsPage";
import { LogsPage } from "./pages/LogsPage";
import { McpPage } from "./pages/McpPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { MemesPage } from "./pages/MemesPage";
import { MemoryPage } from "./pages/MemoryPage";
import { ModelsPage } from "./pages/ModelsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PluginsPage } from "./pages/PluginsPage";
import { RoutinePage } from "./pages/RoutinePage";
import { SettingsPage } from "./pages/SettingsPage";
import { SkillsPage } from "./pages/SkillsPage";
import { SubagentsPage } from "./pages/SubagentsPage";
import type { Action } from "./pages/common";

const TOKEN_KEY = "shiritaibot-token";
const MODE_KEY = "shiritaibot-mode";

export function App() {
  return (
    <NoticeProvider>
      <ConsoleApp />
    </NoticeProvider>
  );
}

function ConsoleApp() {
  const [linkToken, setLinkToken] = useState(() =>
    location.pathname === "/token"
      ? new URLSearchParams(location.hash.slice(1)).get("token")
      : null,
  );
  const [token, setToken] = useState<string | null>(() =>
    linkToken ? null : sessionStorage.getItem(TOKEN_KEY),
  );
  const [page, navigate] = useHashRoute();
  const { succeed, fail, warn, dismiss } = useNotice();
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [restartInstance, setRestartInstance] = useState<string | null>(null);
  const [updateOpen, setUpdateOpen] = useState(false);
  const [mode, setMode] = useState<"console" | "chat">(() =>
    localStorage.getItem(MODE_KEY) === "chat" ? "chat" : "console",
  );
  const [session, setSession] = useState("web:owner");
  const [drafts, setDrafts] = useState<string[]>([]);
  const queryClient = useQueryClient();

  useEffect(() => {
    localStorage.setItem(MODE_KEY, mode);
  }, [mode]);

  const startConversation = useCallback(() => {
    const id = "web:" + Date.now();
    setDrafts((previous) => [...previous, id]);
    setSession(id);
  }, []);

  const authenticate = useCallback(
    (next: string) => {
      dismiss();
      sessionStorage.setItem(TOKEN_KEY, next);
      setToken(next);
    },
    [dismiss],
  );

  useLayoutEffect(() => {
    if (location.pathname === "/token") {
      history.replaceState(history.state, "", "/");
      if (linkToken) sessionStorage.removeItem(TOKEN_KEY);
    }
  }, [linkToken]);

  useEffect(() => {
    if (!linkToken) return;
    let active = true;
    void createClient(() => linkToken)
      .health()
      .then(() => {
        if (active) authenticate(linkToken);
      })
      .catch((error: unknown) => {
        if (active)
          fail(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (active) setLinkToken(null);
      });
    return () => {
      active = false;
    };
  }, [linkToken, authenticate, fail]);

  const client: ApiClient | null = useMemo(
    () => (token ? createClient(() => token) : null),
    [token],
  );
  const draft = useConfigDraft(client);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => client!.health(),
    enabled: client !== null,
    refetchInterval: restarting ? 1000 : 10000,
    retry: restarting ? false : 3,
  });
  // Background check for a newer stable release; failures stay silent here.
  const updateCheck = useQuery({
    queryKey: ["update", "check", health.data?.instance_id],
    queryFn: async () => {
      const [status, latest] = await Promise.all([
        client!.updateStatus(),
        client!.updateReleases(1, 1, false),
      ]);
      const release = latest?.items?.[0];
      return Boolean(
        release && status?.current && isNewer(release, status.current),
      );
    },
    enabled: client !== null && health.isSuccess && !restarting,
    staleTime: 30 * 60 * 1000,
    refetchInterval: 6 * 60 * 60 * 1000,
    retry: false,
  });
  const approvals = useQuery({
    queryKey: ["records", "approval"],
    queryFn: () => client!.records("approval"),
    enabled: client !== null,
    refetchInterval: 4000,
  });

  // Surface transport/auth failures instead of silently showing empty pages.
  const queryError =
    draft.error ??
    (health.error instanceof Error ? health.error : null) ??
    (client && health.isError ? new Error("无法连接后端服务") : null);
  useEffect(() => {
    if (!queryError || restarting) return;
    if (queryError.message.includes("管理令牌")) {
      sessionStorage.removeItem(TOKEN_KEY);
      setToken(null);
    }
    fail(queryError.message);
  }, [queryError, restarting, fail]);

  useEffect(() => {
    if (
      !restartInstance ||
      !health.data?.instance_id ||
      health.data.instance_id === restartInstance
    )
      return;
    setRestarting(false);
    setRestartInstance(null);
    succeed("重启完成");
    void queryClient.invalidateQueries();
  }, [restartInstance, health.data?.instance_id, queryClient, succeed]);

  useEffect(() => {
    if (!restarting) return;
    const timer = setTimeout(() => {
      setRestarting(false);
      setRestartInstance(null);
      fail("等待重启超时，请检查服务日志或稍后刷新页面。");
    }, 60000);
    return () => clearTimeout(timer);
  }, [restarting, fail]);

  const onRealtime = useCallback(
    (event: RealtimeEvent) => {
      void queryClient.invalidateQueries({ queryKey: ["health"] });
      void queryClient.invalidateQueries({ queryKey: ["records"] });
      if (event.kind === "turn" || event.kind === "reply") {
        void queryClient.invalidateQueries({ queryKey: ["history"] });
      }
    },
    [queryClient],
  );
  useRealtime(token, onRealtime);

  const pending = (approvals.data ?? []).filter(
    (row) => row.status === "pending" && row.reviewer !== "model",
  ).length;

  const act: Action = useCallback(
    async (work, successText) => {
      setBusy(true);
      try {
        const result = await work();
        if (successText !== "") {
          succeed(
            successText ??
              (result &&
              typeof result === "object" &&
              "text" in result &&
              typeof (result as { text?: unknown }).text === "string"
                ? String((result as { text: string }).text).slice(0, 500)
                : "操作已完成"),
          );
        }
        await queryClient.invalidateQueries();
      } catch (error) {
        fail(error instanceof Error ? error.message : String(error));
      } finally {
        setBusy(false);
      }
    },
    [queryClient, succeed, fail],
  );

  async function save() {
    setSaving(true);
    try {
      const result = await draft.save();
      const warnings = result?.warnings ?? [];
      if (warnings.length) {
        warn("配置已保存，但有以下问题：" + warnings.join("；"));
      } else {
        succeed("配置已保存并应用");
      }
      await queryClient.invalidateQueries({ queryKey: ["health"] });
    } catch (error) {
      fail(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  async function restart() {
    if (!client || restarting || busy || saving || draft.dirty) return;
    dismiss();
    setRestarting(true);
    try {
      const result = await client.restart();
      setRestartInstance(result.instance_id);
    } catch (error) {
      setRestarting(false);
      fail(error instanceof Error ? error.message : String(error));
    }
  }

  const startUpdateRestart = useCallback(() => {
    dismiss();
    setRestarting(true);
    setRestartInstance(health.data?.instance_id ?? null);
  }, [dismiss, health.data?.instance_id]);

  if (!client) {
    return (
      <LoginView
        authenticating={Boolean(linkToken)}
        onAuthenticated={authenticate}
        onError={fail}
      />
    );
  }

  if (mode === "chat") {
    return (
      <ChatWorkspace
        client={client}
        act={act}
        assistantName={draft.config?.assistant_name ?? "ShiritaiBot"}
        session={session}
        onSelectSession={setSession}
        onCreateSession={startConversation}
        draftSessions={drafts}
        onOpenSettings={() => {
          setMode("console");
          navigate("settings");
        }}
      />
    );
  }

  function content(api: ApiClient) {
    if (!draft.config) {
      return draft.loading ? <p className="muted">正在读取配置…</p> : null;
    }
    const configProps = {
      client: api,
      act,
      config: draft.config,
      setConfig: draft.update,
    };
    switch (page) {
      case "overview":
        return <OverviewPage client={api} act={act} onNavigate={navigate} />;
      case "chat":
        return (
          <ChatPage
            client={api}
            act={act}
            assistantName={draft.config.assistant_name}
          />
        );
      case "events":
        return <EventsPage client={api} act={act} />;
      case "routine":
        return <RoutinePage client={api} act={act} />;
      case "memory":
        return <MemoryPage client={api} act={act} />;
      case "memes":
        return <MemesPage {...configProps} />;
      case "knowledge":
        return <KnowledgePage {...configProps} />;
      case "logs":
        return <LogsPage client={api} act={act} />;
      case "models":
        return <ModelsPage {...configProps} />;
      case "adapters":
        return <AdaptersPage {...configProps} />;
      case "subagents":
        return <SubagentsPage {...configProps} />;
      case "plugins":
        return <PluginsPage {...configProps} />;
      case "skills":
        return <SkillsPage {...configProps} />;
      case "mcp":
        return <McpPage {...configProps} />;
      case "approvals":
        return <ApprovalsPage {...configProps} />;
      case "settings":
        return <SettingsPage {...configProps} />;
      default:
        return null;
    }
  }

  return (
    <AppShell
      page={page}
      onNavigate={navigate}
      pendingApprovals={pending}
      online={health.isSuccess && !restarting}
      version={"v" + (health.data?.version ?? "0.2.0")}
      onOpenChat={() => setMode("chat")}
      onRestart={() => void restart()}
      restarting={restarting}
      restartDisabled={
        busy ||
        saving ||
        draft.dirty ||
        !health.isSuccess ||
        health.data?.restart_available === false
      }
      restartHint={
        draft.dirty
          ? "请先保存或放弃未保存的配置"
          : health.data?.restart_available === false
            ? "请使用 shiritaibot run 启动以支持重启"
            : "重启 ShiritaiBot 服务"
      }
      onCheckUpdate={() => setUpdateOpen(true)}
      updateAvailable={updateCheck.data === true}
      onLogout={() => {
        dismiss();
        setUpdateOpen(false);
        setRestarting(false);
        setRestartInstance(null);
        sessionStorage.removeItem(TOKEN_KEY);
        setToken(null);
        queryClient.clear();
      }}
    >
      {busy && <span className="sr-only">正在执行操作</span>}
      {content(client)}
      {updateOpen && (
        <UpdateDialog
          client={client}
          onClose={() => setUpdateOpen(false)}
          onRestarting={startUpdateRestart}
          restarting={restarting}
          blockedReason={
            draft.dirty
              ? "请先保存或放弃未保存的配置"
              : busy || saving
                ? "请等待当前操作完成"
                : null
          }
        />
      )}
      {draft.dirty && (
        <SaveBar
          busy={saving}
          onSave={() => void save()}
          onDiscard={draft.discard}
        />
      )}
    </AppShell>
  );
}
